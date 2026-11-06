"""The tick loop. The engine owns all state in-process (SQLite is write-through persistence).

Tick order (identical to tools/fleet_check.py, L6/L29):
  events → expiries (weather, authorisations, zones, intrusion holds)
  → actions (due auto-launches, then golden actions + golden unit tasking)
  → auto policies (grounding RTB, rotation/backfill, track relay)
  → movement / battery / landings (+ handoff, safety RTB, ground units, feed reveal)
  → invariants → KPIs.
DT = 0.1 sim-min; t advances as round(t + DT, 4).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from sim import incidents, policies, privacy
from sim.airspace import Airspace
from sim.approvals import ApprovalBook
from sim.docks import Dock, book_slot
from sim.events import EventLog
from sim.fleet import Drone, charge, drain, move
from sim.geo import DT, EPS, dist, fmt, in_rect, sector_of, tmin
from sim.ground import GroundUnits
from sim.kpi import KPI
from sim.pilots import Pilots
from sim.scenario import load_scenario
from sim.suspect import SuspectPath, visible_position
from sim.weather import Weather
from sim.world import World, load_world

MODES = ("demo", "live", "headless")


class Engine:
    def __init__(
        self,
        world: World,
        scenario: dict,
        seed: str | None = None,
        mode: str = "headless",
        golden: bool | None = None,
        record_frames: bool = False,
        db=None,
    ):
        if mode not in (*MODES, "game", "city"):  # only from POST /game/start, /city/start
            raise ValueError(f"mode must be one of {MODES}")
        self.world = world
        self.scenario = scenario
        self.name = scenario["name"]
        self.title = scenario.get("title", self.name)
        self.seed = seed if seed is not None else scenario.get("seed")
        self.rng = random.Random(self.seed)  # wording/jitter only; physics never uses it
        self.mode = mode
        self.M = world.model
        self.T0, self.T1 = tmin(scenario["start"]), tmin(scenario["end"])
        self._t = self.T0  # time of the next tick to process
        self.now = self.T0  # time of the last processed tick (T0 before the first)
        self.ticks = 0

        raw = world.raw
        self.docks: dict[str, Dock] = {k: Dock(k, v) for k, v in raw["docks"].items()}
        self.loops: dict = raw["patrol_loops"]
        self.pilots = Pilots.from_world(raw["pilots"])
        self.drones: dict[str, Drone] = {k: Drone(k, v) for k, v in raw["fleet"].items()}
        ini = scenario["initial"]
        for did, v in (ini.get("docked") or {}).items():
            d = self.drones[did]
            d.bat, d.slot, d.pos = float(v["battery"]), v["at"], list(self.docks[v["at"]].at)
            self.docks[v["at"]].occupants.append(did)
        for did, v in (ini.get("airborne") or {}).items():
            d = self.drones[did]
            d.bat, d.state, d.mission, d.loop = (
                float(v["battery"]),
                "airborne",
                v["mission"],
                v["loop"],
            )
            d.pos = list(self.loops[v["loop"]]["center"])
            d.on_station, d.flights = True, 1
            self.pilots.assign(d, v["pilot"])

        self.airspace = Airspace(raw["zones"])
        self.weather = Weather(self.M["wind_limit_ms"], raw["district"]["sectors"])
        self.suspect = SuspectPath(scenario.get("suspect_path") or [])
        self.incidents: dict[str, dict] = {}
        self.ground = GroundUnits(raw)
        self._feed = incidents.load_feed(scenario)  # internal; facts are served once revealed
        self.intrusions: list[dict] = []  # unauthorised-drone SEPARATION holds (trigger)
        self.held_launches: set[str] = set()  # policy launches already logged as held
        self.kpi_history: list[dict] = []  # per sim-minute KPI strip (past only)
        self.baseline_result = None  # sim.baseline.Baseline after POST /baseline/run
        self.log = EventLog()
        self.violations: list[str] = []
        self.refusals: list[tuple[float, str, str]] = []
        self.kpi = KPI(list(self.loops))
        self.tracker_id: str | None = None
        self.relief_pending_id: str | None = None
        self.patrol_relief: dict[str, str | None] = {}
        self.pending_auto: list[tuple[float, str]] = []
        self.policy_launches: dict[str, list[dict]] = {}
        self.approvals = ApprovalBook()
        self.privacy = privacy.PrivacyLog()
        self.decisions: list[dict] = []
        self.standing_policy = False  # auto-launch runs only while approved (L31)
        self.auto_waiting: set[str] = set()  # auto-launches queued behind the standing policy

        # scenario queues: internal, never served
        self._events = sorted(
            (tmin(e["t"]), i, e) for i, e in enumerate(scenario.get("events") or [])
        )
        self.golden = mode in ("headless", "game") if golden is None else golden
        self.flight = None  # game mode: the drone under the player's stick (sim/game.py)
        self.city = None  # city mode: roaming, vehicles, decisions (sim/city.py)
        golden_actions = scenario.get("golden_actions") or []
        n_golden = len(golden_actions)
        self._actions = (
            sorted(
                [(self.T0, -1, {"t": scenario["start"], "act": "standing_policy"})]
                + [(tmin(a["t"]), i, a) for i, a in enumerate(golden_actions)]
                # units_golden run as Comms actions after the same-minute golden actions
                + [
                    (tmin(a["t"]), n_golden + j, a)
                    for j, a in enumerate(scenario.get("units_golden") or [])
                ]
            )
            if self.golden
            else []
        )
        self._ei = self._ai = 0
        self._mid_tick = False  # stopped between "due auto-launches" and "actions" (run_to_actions)
        self._log_mark = 0
        # id of the scenario's first CAD alert (KPI key eyes_on_inc1_s); internal, never served
        alerts = [e["incident"] for _t, _i, e in self._events if e["type"] == "cad_alert"]
        self.first_alert_incident: str | None = alerts[0] if alerts else None
        self._action_handler: Callable | None = None
        if self._actions:
            from sim.golden import apply_action

            self._action_handler = apply_action

        self.record_frames = record_frames
        self.frames: list[dict] = []
        self.db = db
        self._db_seq = 0
        if db is not None:
            db.start_run(self)

    # ------------------------------------------------------------------ construction
    @classmethod
    def load(
        cls,
        name: str = "FRIDAY-RUSH",
        seed: str | None = None,
        mode: str = "headless",
        *,
        golden: bool | None = None,
        record_frames: bool = False,
        db=None,
        world: World | None = None,
    ) -> Engine:
        return cls(
            world or load_world(),
            load_scenario(name),
            seed=seed,
            mode=mode,
            golden=golden,
            record_frames=record_frames,
            db=db,
        )

    # ------------------------------------------------------------------ basics
    @property
    def done(self) -> bool:
        return self._t > self.T1 + EPS

    @property
    def events(self) -> list[dict]:
        """Past events only (everything in the log has already happened)."""
        return [e.to_dict() for e in self.log.items]

    def emit(self, t, type_, text, payload=None, timeline=True):
        return self.log.append(t, type_, text, payload, timeline)

    def violation(self, t: float, msg: str) -> None:
        self.violations.append(f"{fmt(t)} {msg}")

    def incident_open_of_kind(self, kind: str) -> bool:
        return any(v["kind"] == kind and v["status"] == "open" for v in self.incidents.values())

    def next_event_time(self) -> float | None:
        """Internal (clock skip only). Never served by the API."""
        cands = []
        if self._ei < len(self._events):
            cands.append(self._events[self._ei][0])
        if self._ai < len(self._actions):
            cands.append(self._actions[self._ai][0])
        cands += [due for due, _ in self.pending_auto]
        cands += [te for te, _ in self.weather.ends]
        cands = [c for c in cands if c >= self._t - EPS]
        return min(cands) if cands else None

    # ------------------------------------------------------------------ primitives (checker)
    def grounded_sectors(self) -> set[str]:
        return self.weather.grounded

    def loop_grounded(self, L: str) -> bool:
        loop, g = self.loops[L], self.weather.grounded
        return sector_of(loop["center"]) in g or bool(set(loop.get("sectors", [])) & g)

    def intruded(self) -> dict[str, str]:
        """Sectors under an unauthorised-drone SEPARATION hold -> 'HH:MM' end."""
        return {x["sector"]: fmt(x["until"]) for x in self.intrusions}

    def ready_count(self) -> int:
        rp = self.M["ready_pct"]
        return sum(
            1
            for d in self.drones.values()
            if d.state == "docked"
            and self.docks[d.slot].usable
            and d.bat >= rp
            and not d.out_of_service
            and not d.fault
        )

    def floor_allows(self, d: Drone) -> bool:
        """L12 ready floor: a policy launch of a READY drone must leave >= ready_floor READY."""
        if d.bat < self.M["ready_pct"]:
            return True
        return self.ready_count() - 1 >= self.world.policies["ready_floor"]

    def ready_candidates(self, near, gate: float, avoid_thermal: bool = True) -> list[Drone]:
        penalty = self.world.policies["thermal_penalty_km"]
        held = self.intruded()
        c = []
        for d in self.drones.values():
            if (
                d.state == "docked"
                and not d.out_of_service
                and not d.fault
                and self.docks[d.slot].usable
                and d.bat >= gate
                and sector_of(d.pos) not in self.weather.grounded
                and sector_of(d.pos) not in held
            ):
                c.append((dist(d.pos, near) + (penalty if (avoid_thermal and d.thermal) else 0), d))
        c.sort(key=lambda x: (x[0], x[1].id))
        return [d for _, d in c]

    def pilot_for(self, prefer: list[str], t: float, purpose: str) -> str | None:
        p = self.pilots.pilot_for(prefer)
        if p is None:  # L36: not an invariant breach; the launch simply doesn't happen
            self.emit(
                t,
                "pilot_capacity",
                f"no pilot capacity for {purpose}",
                {"purpose": purpose, "code": "PILOT_SATURATED"},
                timeline=False,
            )
        return p

    def launch(self, d: Drone, t, mission, target=None, loop=None, incident=None, pilot=None):
        self.docks[d.slot].occupants.remove(d.id)
        d.slot = None
        d.state, d.mission, d.loop, d.incident = "airborne", mission, loop, incident
        d.target = target
        d.launch_wait = self.M["launch_delay_min"]
        d.on_station = False
        d.flights += 1
        self.pilots.assign(d, pilot)

    def book_slot(self, d: Drone, t: float) -> str | None:
        slot, skipped = book_slot(
            self.docks, d.id, d.pos, d.home, self.world.policies["slot_log_radius_km"]
        )
        for k, reason in skipped:
            self.emit(
                t,
                "slot_reroute",
                f"🅿 {d.id} nearest dock {k} unavailable → {reason}, rerouting",
                {"drone": d.id, "dock": k, "code": reason},
            )
        if slot is None:
            self.violation(t, f"{d.id} no free slot anywhere")
        return slot

    def rtb(self, d: Drone, t: float, why: str = "") -> None:
        if d.state != "airborne" or d.mission == "returning":
            return
        slot = self.book_slot(d, t)
        d.mission, d.target, d.loop, d.on_station = "returning", slot, None, False
        d.manual = False
        if why:
            self.emit(
                t,
                "rtb",
                f"↩ {d.id} RTB {why} → {slot} (battery {d.bat:.0f}%)",
                {"drone": d.id, "dock": slot, "why": why, "battery": round(d.bat, 1)},
            )

    def projected_landing(self, d: Drone) -> float:
        best = min(
            (
                dist(d.pos, self.docks[k].at)
                for k in self.docks
                if self.docks[k].free_slots() > 0 or d.id in self.docks[k].reserved
            ),
            default=99,
        )
        return d.bat - best / self.M["cruise_km_per_min"] * self.M["drain_pct_per_min"]["cruise"]

    def landing(self, d: Drone) -> dict:
        """When and at what battery a returning drone lands, from its present position and the
        zones active now. Read-only; empty unless it has a dock to reach."""
        if d.mission != "returning" or d.target not in self.docks:
            return {}
        at = self.docks[d.target].at
        pos, km = d.pos, 0.0
        if d.exit_point is not None:
            pos, km = d.exit_point, dist(d.pos, d.exit_point)
        for _ in range(8):  # one leg per zone corner, as _fly would take them
            aim = self.airspace.detour(pos, at)
            km += dist(pos, aim)
            if aim is at:
                break
            if aim == pos:
                return {}  # dock inside an active zone: holding, no landing time to give
            pos = aim
        else:
            return {}
        rates = self.M["drain_pct_per_min"]
        ticks = math.ceil(km / self.M["cruise_km_per_min"] / DT - EPS)
        wait = max(d.launch_wait, 0.0)
        pct = d.bat - wait * rates["hover"] - ticks * DT * rates["cruise"]
        return {"lands_at": fmt(self.now + wait + ticks * DT), "lands_pct": round(pct, 1)}

    # ------------------------------------------------------------------ world events
    def _do_event(self, t: float, e: dict) -> None:
        k = e["type"]
        if k == "dock_fault":
            self.docks[e["dock"]].online = False
            self.emit(
                t,
                "dock_fault",
                f"⚠ dock {e['dock']} OFFLINE ({e['note']})",
                {"dock": e["dock"], "note": e.get("note")},
            )
        elif k == "dock_restored":
            self.docks[e["dock"]].online = True
            self.emit(t, "dock_restored", f"✔ dock {e['dock']} back online", {"dock": e["dock"]})
        elif k == "weather":
            self.weather.ground(e["sectors"], e["wind_ms"], e["until"])
            self.emit(
                t,
                "weather_grounded",
                f"🌬 wind {e['wind_ms']} m/s > {self.M['wind_limit_ms']} in {e['sectors']} "
                f"→ WEATHER_GROUNDED until {e['until']}",
                {
                    "sectors": list(e["sectors"]),
                    "wind_ms": e["wind_ms"],
                    "until": e["until"],
                    "code": "WEATHER_GROUNDED",
                },
            )
            self.refusals.append((t, "WEATHER_GROUNDED", f"sectors {e['sectors']}"))
        elif k == "cad_alert":
            inc = dict(e, t=t, open=True, status="open")
            inc.setdefault("priority", incidents.priority(self, e["kind"]))
            self.incidents[e["incident"]] = inc
            self.emit(
                t,
                "cad_alert",
                f"🚨 {e['incident']} {e['kind']} at {e['at']}",
                {
                    "incident": e["incident"],
                    "kind": e["kind"],
                    "at": e["at"],
                    "pos": list(self.world.point(e["at"])),
                    "report": e.get("note"),
                },
            )
            policies.on_cad_alert(self, t, inc)
        else:  # shift_brief, unit_radio, trigger: internal (non-timeline) events
            payload = {k2: v for k2, v in e.items() if k2 not in ("t", "type")}
            self.emit(t, k, k.replace("_", " "), payload, timeline=False)

    def inject_alert(self, spec: dict) -> str:
        """Live trigger: a CAD alert happening now (never pre-scheduled, so nothing leaks)."""
        # own series INC-T1, INC-T2…: ids never reveal how many scheduled incidents remain
        n = sum(1 for k in self.incidents if k.startswith("INC-T")) + 1
        iid = f"INC-T{n}"
        self._do_event(self.now, {"type": "cad_alert", "incident": iid, **spec})
        return iid

    # ------------------------------------------------------------------ the tick
    def step(self) -> None:
        if self.done:
            return
        if not self._mid_tick:
            self._begin_tick()
        self._finish_tick()

    def _begin_tick(self) -> None:
        """Tick up to the actions slot. Stopping here lets a write land where a golden action
        lands: same sim time, same place in the tick (run_to_actions, demo dry run)."""
        t = self._t
        self.now = t
        self._log_mark = len(self.log)
        self._mid_tick = True

        # 1. world events
        while self._ei < len(self._events) and self._events[self._ei][0] <= t + EPS:
            self._do_event(t, self._events[self._ei][2])
            self._ei += 1
        # 2. weather expiry
        for secs in self.weather.expire(t):
            self.emit(
                t,
                "weather_reopened",
                f"🌬 wind back under limit, {secs} reopened",
                {"sectors": list(secs)},
            )
        self._expire_authorisations(t)
        self._expire_intrusions(t)
        # 3. actions: due auto-launch orders, then golden (human/agent) actions
        policies.auto_launch_due(self, t)

    def _finish_tick(self) -> None:
        t = self._t
        while self._ai < len(self._actions) and self._actions[self._ai][0] <= t + EPS:
            self._action_handler(self, t, self._actions[self._ai][2])
            self._ai += 1
        # 4. auto policies
        if self.city is not None:
            self.city.tick(t)
        else:
            policies.patrol_rotation(self, t)
            policies.track_relay(self, t)
        # 5. movement, battery, landings
        self._movement(t)
        self._ground_tick(t)
        incidents.update(self, t)
        privacy.update(self, t)
        # 6. invariants, 7. KPIs
        self._invariants(t)
        privacy.check(self, t)
        self.kpi.tick(self, t)
        if self.ticks % 10 == 0:  # each sim-minute
            self.kpi_history.append({"t": round(t, 4), "clock": fmt(t), **self.kpi.strip(self)})

        if self.record_frames and abs(t * 10 - round(t * 10)) < 1e-6 and round(t * 10) % 5 == 0:
            self.frames.append(self.frame())
        self.ticks += 1
        if self.db is not None:
            self.db.write_tick(self, self.log.items[self._log_mark :])
        self._t = round(t + DT, 4)
        self._mid_tick = False
        if self.done and self.db is not None:
            self.db.finish_run(self)

    def run_until(self, minutes: float | str) -> None:
        target = tmin(minutes)
        while not self.done and self._t <= target + EPS:
            self.step()

    def run_to_actions(self, minutes: float | str) -> None:
        """Advance to the first tick at or after `minutes` and stop at its actions slot.
        step() resumes the tick. A no-op if already stopped there."""
        target = tmin(minutes)
        while not self.done and self._t < target - EPS:
            self.step()
        if not self.done and not self._mid_tick:
            self._begin_tick()

    def run(self) -> None:
        while not self.done:
            self.step()

    def _movement(self, t: float) -> None:
        M = self.M
        rates = M["drain_pct_per_min"]
        for d in self.drones.values():
            if d.state == "docked":
                charge(d, M["charge_pct_per_min"], M["charge_cap_pct"])
                continue
            if d.launch_wait > 0:
                d.launch_wait -= DT
                drain(d, rates["hover"])
                continue
            if self.flight is not None and self.flight.drone == d.id:
                self.flight.tick(self, d, t)
                self._airborne_checks(d, t)
                continue
            if d.exit_point is not None:  # leaving a just-activated zone comes first
                drain(d, rates["cruise"])
                if move(d, d.exit_point, M["cruise_km_per_min"]):
                    d.exit_point = None
                self._airborne_checks(d, t)
                continue
            held = False
            if d.hold_until is not None:
                if t < d.hold_until - EPS and d.mission != "returning":
                    held = True
                else:
                    d.hold_until = None
            if held:  # SEPARATION hold: hover in place (patrol drones keep patrolling on station)
                on_patrol = d.mission == "patrol" and d.on_station
                drain(d, rates["patrol"] if on_patrol else rates["hover"])
            elif d.mission == "roam":
                drain(d, rates["cruise"])
                self.city.roam_move(d)
            elif d.mission == "investigate":
                drain(d, rates["cruise"])
                self.city.investigate_move(d)
            elif d.mission == "escort":
                drain(d, rates["cruise"])
                self.city.escort_move(d)
            elif d.mission == "watch":  # city mode: a person asked for eyes on an area (L67)
                drain(d, rates["cruise"])
                self.city.watch_move(d)
            elif d.mission == "patrol":
                if d.on_station:
                    drain(d, rates["patrol"])
                else:
                    drain(d, rates["cruise"])
                    if self._fly(d, self.loops[d.loop]["center"], M["cruise_km_per_min"]):
                        d.on_station = True
                        old = [
                            x
                            for x in self.drones.values()
                            if x.state == "airborne"
                            and x.loop == d.loop
                            and x.on_station
                            and x is not d
                        ]
                        for x in old:
                            self.rtb(x, t, f"relieved by {d.id} on {d.loop}")
                        self.patrol_relief[d.loop] = None
            elif d.mission in ("incident", "perimeter", "corridor", "search"):
                if d.on_station:
                    drain(d, rates["hover"])
                else:
                    drain(d, rates["cruise"])
                    if self._fly(d, d.target, M["cruise_km_per_min"]):
                        d.on_station = True
                        if (
                            d.mission in ("incident", "search")
                            and d.incident not in self.kpi.eyes_on
                        ):
                            self.kpi.eyes_on[d.incident] = t
                            secs = (t - self.incidents[d.incident]["t"]) * 60
                            self.emit(
                                t,
                                "eyes_on",
                                f"👁 EYES-ON {d.incident} by {d.id} ({secs:.0f} s after alert, "
                                f"battery {d.bat:.0f}%)",
                                {
                                    "incident": d.incident,
                                    "drone": d.id,
                                    "seconds": round(secs, 1),
                                    "battery": round(d.bat, 1),
                                },
                            )
            elif d.mission in ("track", "relay"):
                sp = self.suspect.pos(t)
                target = self.airspace.boundary_point(sp) if self.airspace.in_zone(sp) else sp
                drain(d, rates["track"] if d.mission == "track" else rates["cruise"])
                self._fly(
                    d,
                    target,
                    M["sprint_km_per_min"] if d.mission == "relay" else M["cruise_km_per_min"],
                )
                policies.relay_handoff(self, d, t)
            elif d.mission == "hold":
                drain(d, rates["hover"])
                self._fly(d, d.target, M["cruise_km_per_min"])
            elif d.mission == "returning":
                drain(d, rates["cruise"])
                if self._fly(d, self.docks[d.target].at, M["cruise_km_per_min"]):
                    k = d.target
                    self.docks[k].reserved.remove(d.id)
                    self.docks[k].occupants.append(d.id)
                    d.state, d.slot, d.mission, d.incident = "docked", k, None, None
                    self.pilots.assign(d, None)
                    d.min_landing = min(d.min_landing, d.bat)
                    self.emit(
                        t,
                        "landing",
                        f"🛬 {d.id} lands at {k} with {d.bat:.0f}%",
                        {"drone": d.id, "dock": k, "battery": round(d.bat, 1)},
                    )
                    if d.bat < M["reserve_pct"]:
                        self.violation(t, f"{d.id} landed at {d.bat:.1f}% < reserve")
                        self.kpi.landed_below_reserve(d.id)
            if d.state == "airborne":
                self._airborne_checks(d, t)
            else:
                self.kpi.red_zone(d.id, False)

    def _airborne_checks(self, d: Drone, t: float) -> None:
        M = self.M
        inside = bool(self.airspace.in_zone(d.pos))
        self.kpi.red_zone(d.id, inside)
        if inside:
            self.violation(t, f"{d.id} inside RED zone")
        policies.safety_rtb(self, d, t)
        if d.bat < M["reserve_pct"]:
            self.violation(t, f"{d.id} airborne below reserve ({d.bat:.1f}%)")
            self.kpi.below_reserve_airborne(d.id)

    def _fly(self, d: Drone, target, speed: float) -> bool:
        """move() toward target, routed round active VIP zones. True only on reaching target."""
        aim = self.airspace.detour(d.pos, target)
        return move(d, aim, speed) and aim is target

    def _expire_authorisations(self, t: float) -> None:
        """Expiries step: authorisations past their expiry → tracking drones RTB, MISSION_EXPIRED
        logged; zones past `until` deactivate."""
        from sim.actions import log_decision

        for au in self.approvals.expire(t):
            if au.kind == "zone":
                for z in self.airspace.zones:
                    if z["id"] == au.scope:
                        z["active"] = False
                self.refresh_zones(t)
            log_decision(
                self,
                "sim",
                f"expire {au.kind}",
                code="MISSION_EXPIRED",
                rationale=f"{au.id} ({au.approval_id}) expired",
                approval_id=au.approval_id,
            )
            if au.kind != "track":
                continue
            for d in self.drones.values():
                if (
                    d.state == "airborne"
                    and d.incident == au.incident
                    and d.mission in ("track", "relay")
                ):
                    self.rtb(d, t, "MISSION_EXPIRED (track authorisation expired)")
                    if self.tracker_id == d.id:
                        self.tracker_id = None

    def _expire_intrusions(self, t: float) -> None:
        for x in list(self.intrusions):
            if x["until"] <= t + EPS:
                self.intrusions.remove(x)
                self.held_launches.clear()
                self.emit(
                    t,
                    "airspace_intrusion_cleared",
                    f"🛸 SEPARATION hold in {x['sector']} lifted",
                    {"sector": x["sector"]},
                )

    def refresh_zones(self, t: float) -> None:
        """Docks inside an active no-entry (VIP) zone become unusable; drones booked there
        re-book the nearest usable slot. Airborne drones inside leave by the nearest boundary;
        drones whose fixed target is inside RTB (all logged NO_FLY_ZONE)."""
        vip = self.airspace.vip_zones()
        for k in self.docks.values():
            k.restricted = next((z["id"] for z in vip if in_rect(k.at, z["rect"])), None)
        for d in self.drones.values():
            if d.state != "airborne":
                continue
            if d.mission == "returning" and d.target in self.docks:
                k = self.docks[d.target]
                if k.restricted:
                    k.reserved.remove(d.id)
                    d.mission = None
                    self.rtb(d, t, f"{k.id} inside active {k.restricted} (NO_FLY_ZONE)")
            elif isinstance(d.target, list) and self.airspace.vip_at(d.target):
                zid = self.airspace.vip_at(d.target)["id"]
                self.rtb(d, t, f"target inside active {zid} (NO_FLY_ZONE)")
            z = self.airspace.vip_at(d.pos)
            d.exit_point = self.airspace.exit_point(d.pos, z["rect"]) if z else None
            if z:
                self.emit(
                    t,
                    "zone_exit",
                    f"⛔ {d.id} leaves {z['id']} by the nearest boundary (NO_FLY_ZONE)",
                    {
                        "drone": d.id,
                        "zone": z["id"],
                        "to": [round(v, 3) for v in d.exit_point],
                        "code": "NO_FLY_ZONE",
                    },
                )

    def task_unit(self, uid: str, iid: str, scene=None):
        inc = self.incidents[iid]
        where = scene if scene is not None else self.world.point(inc["at"])
        u = self.ground.task(self.now, uid, iid, where, inc["kind"])
        eta = u.arrive_t - self.now
        secs = int(round(eta * 60))
        self.emit(
            self.now,
            "unit_tasked",
            f"🚓 {uid} → {iid} | ETA {secs // 60}:{secs % 60:02d} | en route",
            {"unit": uid, "incident": iid, "eta_min": round(eta, 2), "status": "en_route"},
            timeline=False,
        )
        return u

    def _ground_tick(self, t: float) -> None:
        for ev, u, iid in self.ground.tick(t):
            what = {
                "unit_on_scene": f"🚓 {u.id} on scene {iid}",
                "unit_cleared": f"🚓 {u.id} clears {iid} (on-scene time done), returning",
                "unit_available": f"🚓 {u.id} back at its start point, available",
            }[ev]
            self.emit(t, ev, what, {"unit": u.id, "incident": iid}, timeline=False)

    def _invariants(self, t: float) -> None:
        for p in self.pilots.values():
            if len(p.drones) > p.max_drones:
                self.violation(t, f"pilot over cap {p.id} ({len(p.drones)}/{p.max_drones})")
        for k, dk in self.docks.items():
            if len(dk.occupants) > dk.slots:
                self.violation(t, f"dock {k} over capacity")
        airborne = sum(1 for d in self.drones.values() if d.state == "airborne")
        cap = sum(p.max_drones for p in self.pilots.values())
        if airborne > cap:
            self.violation(t, f"{airborne} airborne > {cap}")
        for d in self.drones.values():
            if d.state == "airborne" and not d.pilot:
                self.violation(t, f"{d.id} airborne without a named pilot")
            if d.state == "airborne" and d.exit_point is None and self.airspace.vip_at(d.pos):
                self.violation(t, f"{d.id} inside an active VIP zone")
        for u in self.ground.units.values():
            if u.status in ("en_route", "on_scene"):
                inc = self.incidents.get(u.incident)
                if inc is None or inc["status"] != "open":
                    self.violation(t, f"{u.id} committed to a closed incident {u.incident}")

    # ------------------------------------------------------------------ views
    def frame(self) -> dict:
        """Replay frame, same shape as fleet_check.py --dump-jsonl (suspect null unless visible)."""
        t = self.now
        return {
            "t": round(t, 2),
            "clock": fmt(t),
            "grounded": sorted(self.weather.grounded),
            "suspect": visible_position(self),
            "drones": {
                d.id: {
                    "state": d.state,
                    "mission": d.mission,
                    "bat": round(d.bat, 1),
                    "pilot": d.pilot,
                    "pos": [round(v, 3) for v in d.pos],
                    "slot": d.slot,
                    "loop": d.loop,
                }
                for d in self.drones.values()
            },
            "docks": {
                k: {"occ": list(v.occupants), "res": list(v.reserved), "online": v.online}
                for k, v in self.docks.items()
            },
        }

    def incident_view(self, iid: str) -> dict:
        return incidents.view(self, iid)

    def state_view(self) -> dict:
        """Whitelisted, JSON-safe snapshot for /state. Past and present only."""
        return {
            "simulated": True,
            "scenario": self.name,
            "mode": self.mode,
            "t": round(self.now, 4),
            "clock": fmt(self.now),
            "start": fmt(self.T0),
            "end": fmt(self.T1),
            "done": self.done,
            "drones": {k: d.view() for k, d in self.drones.items()},
            "docks": {k: v.view() for k, v in self.docks.items()},
            "pilots": {k: p.view() for k, p in self.pilots.items()},
            "grounded": sorted(self.weather.grounded),
            "weather": self.weather.view(),
            "zones": self.airspace.view(),
            "incidents": {k: self.incident_view(k) for k in self.incidents},
            "units": self.ground.view(self.now),
            "intrusions": [
                {"sector": x["sector"], "at": x["at"], "until": fmt(x["until"])}
                for x in self.intrusions
            ],
            "suspect": visible_position(self),
            "kpi": self.kpi.strip(self),
            "standing_policy": self.standing_policy,
            "approvals_pending": len(self.approvals.pending()),
            "authorisations": [a.view() for a in self.approvals.auths if a.status == "active"],
            "violations": len(self.violations),
        }
