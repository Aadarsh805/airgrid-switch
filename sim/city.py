"""City mode: the living city (docs/city-mode.md, L56). A sim you watch, one human decision per
incident, no end.

Drones are the engine's own: battery, docks, slot booking, zones, recording and the logs are the
same code. City adds only what is new here: roaming routes, station vehicles on the road grid,
work timers and the leave-or-stay decision. Every incident write goes through actions.execute.

All timers are in sim-minutes, so a run is a pure function of seed + triggers + decisions (and
the ticks they land on). Numbers come from world.city.
"""

from __future__ import annotations

import heapq
import random

from sim import actions, rules
from sim.auth import Identity
from sim.game import PLAYER
from sim.geo import DT, dist, fmt

DISPATCH = Identity.agent("dispatch")
NAME = "CITY"
ENDLESS_MIN = 1e7  # the engine needs an end time; about 19 years of sim time
ON_ROAD_EPS = 1e-6
LEAVE_STAY, TREAT_TRANSPORT = "leave_stay", "treat_transport"
OPTIONS = {
    LEAVE_STAY: [
        {"id": "leave", "label": "Return to patrol"},
        {"id": "stay", "label": "Stay longer"},
    ],
    TREAT_TRANSPORT: [
        {"id": "transport", "label": "Transport to Hospital"},
        {"id": "treat", "label": "Treat on scene"},
    ],
}
CHOICES = {
    LEAVE_STAY: {"leave": "leave", "approve": "leave", "stay": "stay"},
    TREAT_TRANSPORT: {"transport": "transport", "treat": "treat"},
}
ROLES = {LEAVE_STAY: "incident_lead", TREAT_TRANSPORT: "duty_doctor"}


class CityError(Exception):
    def __init__(self, rejection: rules.Rejection):
        self.rejection = rejection


def scenario(world, seed: str | None = None) -> dict:
    """The engine's scenario for the city: every drone docked, nothing scheduled."""
    c = world.raw["city"]
    docked = {k: {"battery": 100, "at": v["home"]} for k, v in world.raw["fleet"].items()}
    return {
        "name": NAME,
        "title": "Northgate, a living city",
        "seed": seed or c["seed"],
        "start": c["start"],
        "end": ENDLESS_MIN,
        "initial": {"airborne": {}, "docked": docked},
    }


# ---------------------------------------------------------------- road grid
class Roads:
    """Road grid: straight roads between neighbouring junctions. Nothing leaves it."""

    def __init__(self, junctions: dict):
        pts = sorted({tuple(p) for p in junctions.values()})
        xs, ys = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
        self.edges = []
        for y in ys:
            self.edges += [((a, y), (b, y)) for a, b in zip(xs, xs[1:], strict=False)]
        for x in xs:
            self.edges += [((x, a), (x, b)) for a, b in zip(ys, ys[1:], strict=False)]

    @staticmethod
    def _on_edge(p, e) -> bool:
        (x0, y0), (x1, y1) = e
        if y0 == y1:
            return abs(p[1] - y0) <= ON_ROAD_EPS and x0 - ON_ROAD_EPS <= p[0] <= x1 + ON_ROAD_EPS
        return abs(p[0] - x0) <= ON_ROAD_EPS and y0 - ON_ROAD_EPS <= p[1] <= y1 + ON_ROAD_EPS

    def on_road(self, p) -> bool:
        return any(self._on_edge(p, e) for e in self.edges)

    def route(self, a, b) -> list[list[float]]:
        """Shortest road path a→b, turning only at junctions."""
        a, b = tuple(a), tuple(b)
        graph: dict[tuple, list[tuple]] = {}
        for e in self.edges:
            stops = {e[0], e[1]} | {p for p in (a, b) if self._on_edge(p, e)}
            chain = sorted(stops)
            for u, v in zip(chain, chain[1:], strict=False):
                graph.setdefault(u, []).append(v)
                graph.setdefault(v, []).append(u)
        best, prev, heap = {a: 0.0}, {}, [(0.0, a)]
        while heap:
            km, u = heapq.heappop(heap)
            if u == b:
                break
            for v in graph[u]:
                alt = km + dist(u, v)
                if alt < best.get(v, float("inf")) - 1e-9:
                    best[v], prev[v] = alt, u
                    heapq.heappush(heap, (alt, v))
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        return [list(p) for p in reversed(path)]


class Vehicle:
    def __init__(self, vid: str, kind: str, station: str, home):
        self.id, self.kind, self.station = vid, kind, station
        self.home = list(home)
        self.pos = list(home)
        # parked | en_route | working | returning | transporting | blocked
        self.state = "parked"
        self.incident: str | None = None
        self.path: list[list[float]] = []
        self.block: dict | None = None  # the traffic block a police car is sent to

    def drive(self, km: float) -> bool:
        """Along the path; True on arrival."""
        while self.path and km > 0:
            step = dist(self.pos, self.path[0])
            if step <= km:
                self.pos = self.path.pop(0)
                km -= step
                continue
            f = km / step
            self.pos = [
                self.pos[0] + (self.path[0][0] - self.pos[0]) * f,
                self.pos[1] + (self.path[0][1] - self.pos[1]) * f,
            ]
            return False
        return not self.path

    def view(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "pos": [round(v, 3) for v in self.pos],
            "state": self.state,
            "incident": self.incident,
        }


class City:
    def __init__(self, eng):
        self.eng = eng
        self.cfg = eng.world.raw["city"]
        self.speed = eng.world.raw["ground_units"]["road_speed_km_per_min"]
        self.roads = Roads(eng.world.raw["district"]["junctions"])
        junctions = eng.world.raw["district"]["junctions"]
        self.routes = [[junctions[j] for j in r] for r in self.cfg["roam"]]
        self.roam: dict[str, dict] = {}  # drone -> {route, wp}
        self.vehicles: dict[str, Vehicle] = {}
        for sid, st in self.cfg["stations"].items():
            for vid, kind in st["vehicles"].items():
                self.vehicles[vid] = Vehicle(vid, kind, sid, st["road"])
        self.active: dict[str, dict] = {}
        self.responses: list[float] = []
        self.count: dict[str, int] = {}
        self.handled = 0
        self.flares: list[dict] = []
        self.traffic: list[dict] = []
        self.hidden: list[dict] = []  # random fires nobody has found yet: never served (rule 8)
        self.feed: list[str] = []
        self.events: list[dict] = []
        self._check_world()
        eng.city = self

    def _check_world(self) -> None:
        """Everything a vehicle or drone is sent to must be on a road, outside every zone."""
        zones = [z for z in self.eng.airspace.zones if z["kind"] != "no_record"]
        scenes = [s["at"] for v in self.cfg["scenes"].values() for s in v]
        spots = scenes + [s["road"] for s in self.cfg["stations"].values()]
        for p in spots:
            if not self.roads.on_road(p):
                raise ValueError(f"city spot {p} is not on a road")
            if not all(rules.no_fly_zone("", p, p, [z]).ok for z in zones):
                raise ValueError(f"city spot {p} is inside a zone")

    def start(self) -> None:
        for k, bat in zip(range(len(self.routes)), self.cfg["start_battery"], strict=True):
            self._launch_roamer(k)
            d = next(self.eng.drones[i] for i, r in self.roam.items() if r["route"] == k)
            d.bat = float(bat)
        self._say("city_start", "The city is calm. Drones are out on patrol.")

    # ------------------------------------------------------------------ talk
    def _say(self, type_: str, text: str, inc: dict | None = None, **payload) -> None:
        eng = self.eng
        if inc is not None:
            payload["incident"] = inc["id"]
            inc["timeline"].append({"clock": fmt(eng.now), "text": text})
        e = eng.emit(eng.now, type_, text, payload, timeline=False)
        self.feed = [*self.feed, text][-self.cfg["feed_lines"] :]
        self.events.append(
            {"seq": e.seq, "type": type_, "clock": fmt(eng.now), "text": text, "payload": payload}
        )
        del self.events[: -self.cfg["events_kept"]]

    # ------------------------------------------------------------------ roaming
    def _nearest_wp(self, route: int, pos) -> int:
        wps = self.routes[route]
        return min(range(len(wps)), key=lambda i: (dist(pos, wps[i]), i))

    def _launch_roamer(self, route: int) -> bool:
        eng = self.eng
        start = self.routes[route][0]
        gate = eng.M["launch_gate_pct"]["patrol"]
        cands = [d for d in eng.ready_candidates(start, gate) if eng.floor_allows(d)]
        pilot = eng.pilots.pilot_for([])
        if not cands or pilot is None:
            return False
        d = cands[0]
        eng.launch(d, eng.now, "roam", pilot=pilot)
        self.roam[d.id] = {"route": route, "wp": self._nearest_wp(route, d.pos)}
        return True

    def roam_move(self, d) -> None:
        """Engine hook: one tick along the route. Roamers never stop."""
        r = self.roam[d.id]
        wps = self.routes[r["route"]]
        if d.pos == wps[r["wp"]]:  # e.g. launched from a dock on a junction
            r["wp"] = (r["wp"] + 1) % len(wps)
        target = wps[r["wp"]]
        if self.eng._fly(d, target, self.eng.M["cruise_km_per_min"]):
            d.on_station = True
            r["wp"] = (r["wp"] + 1) % len(wps)

    def _to_roam(self, d) -> None:
        by_route = [
            (sum(1 for x in self.roam.values() if x["route"] == k), k)
            for k in range(len(self.routes))
        ]
        route = min(by_route)[1]
        d.mission, d.incident, d.target, d.on_station = "roam", None, None, True
        self.roam[d.id] = {"route": route, "wp": self._nearest_wp(route, d.pos)}

    def _roam_policy(self, t: float) -> None:
        eng = self.eng
        trigger = eng.M["relief_trigger_pct"]
        for did in [k for k in self.roam if eng.drones[k].mission != "roam"]:
            del self.roam[did]
        for k in range(len(self.routes)):
            here = [eng.drones[i] for i, r in self.roam.items() if r["route"] == k]
            low = [d for d in here if d.bat <= trigger]
            fit = [d for d in here if d.bat > trigger]
            on = [d for d in fit if d.on_station]
            if low and on:  # relief is on the route first, then the tired drone leaves
                for d in low:
                    eng.rtb(d, t, f"relieved by {on[0].id} on route {k + 1}")
            elif not fit and not self._launch_roamer(k):
                for d in low:
                    if d.bat <= self.cfg["home_pct"]:
                        eng.rtb(d, t, "low battery, no relief ready")

    # ------------------------------------------------------------------ triggers
    def trigger(
        self,
        kind: str,
        flare_of: str | None = None,
        scene: dict | None = None,
        found_by: dict | None = None,
    ) -> dict:
        if kind not in self.cfg["scenes"]:
            raise CityError(rules.not_found("city trigger", kind))
        eng, rng = self.eng, self.eng.rng
        if scene is None:
            scenes = self.cfg["scenes"][kind]
            busy = {i["scene"] for i in self.active.values()}
            scene = rng.choice([s for s in scenes if s["name"] not in busy] or scenes)
        at_risk = rng.random() < self.cfg["risk_chance"][kind]
        iid = eng.inject_alert(
            {"kind": kind, "at": list(scene["at"]), "note": scene["name"], "city": True}
        )
        self.count[kind] = self.count.get(kind, 0) + 1
        inc = {
            "id": iid,
            "kind": kind,
            "scene": scene["name"],
            "name": f"{scene['name']} {kind}",
            "scene_spec": scene,
            "at": list(scene["at"]),
            "t0": eng.now,
            "stage": "alert",
            "drone": None,
            "relief": None,
            "needs": [],
            "vehicles": [],
            "jobs": {},
            "at_risk": at_risk,
            "injuries": None,  # unknown until assessed on scene
            "serial": self.count.get(kind, 0),
            "decision": None,
            "hold_until": None,
            "care": None,  # {recommend, choice} once the ambulance has assessed
            "escort": None,
            "traffic_cars": [],
            "timeline": [],
            "flare_of": flare_of,
            "found_by": found_by,
        }
        self.active[iid] = inc
        what = "Flare-up" if flare_of else kind.capitalize()
        self._say("city_alert", f"{what} reported at {scene['name']}.", inc, kind=kind)
        finder = found_by and found_by["drone"]
        if finder and self._divert(finder, inc):
            inc["drone"] = finder  # the drone that found it stays on it
        else:
            inc["drone"] = self._assign_drone(inc)
        actions.log_decision(eng, "operator", f"city:trigger:{kind}", rationale=iid)
        return self.incident_view(inc)

    def _divert(self, did: str, inc: dict) -> bool:
        args = {"drone": did, "mission": "incident", "incident": inc["id"], "to": inc["at"]}
        return actions.execute(self.eng, DISPATCH, "divert", args).ok

    # ------------------------------------------------------------------ random fire (L62)
    def random_fire(self) -> dict:
        """A hidden fire at a seeded road-side spot. No incident and no event until a drone's
        simulated sensor finds it."""
        eng = self.eng
        n = self.count.get("random_fire", 0) + 1
        self.count["random_fire"] = n
        rng = random.Random(f"{eng.seed}:random_fire:{n}")
        spot = rng.choice(self._fire_spots())
        self.hidden.append(
            {"at": spot, "t": eng.now, "drone": None, "best": 0, "status": "hidden", "sent": False}
        )
        actions.log_decision(eng, "operator", "city:trigger:random_fire", rationale="hidden")
        return {"kind": "random_fire", "status": "patrols searching"}

    def _fire_spots(self) -> list[list[float]]:
        det = self.cfg["detect"]
        step, clear = det["spot_step_km"], det["clear_km"]
        avoid = [s["road"] for s in self.cfg["stations"].values()]
        avoid += [s["at"] for v in self.cfg["scenes"].values() for s in v]
        avoid += [i["at"] for i in self.active.values()] + [h["at"] for h in self.hidden]
        zones = [z["rect"] for z in self.eng.airspace.zones]
        out = set()
        for (x0, y0), (x1, y1) in self.roads.edges:
            k = round(dist((x0, y0), (x1, y1)) / step)
            for i in range(k + 1):
                p = (round(x0 + (x1 - x0) * i / k, 4), round(y0 + (y1 - y0) * i / k, 4))
                near_zone = any(
                    r["x"][0] - clear <= p[0] <= r["x"][1] + clear
                    and r["y"][0] - clear <= p[1] <= r["y"][1] + clear
                    for r in zones
                )
                if not near_zone and all(dist(p, a) >= clear for a in avoid):
                    out.add(p)
        return [list(p) for p in sorted(out)]

    def confidence(self, km: float) -> int:
        return max(0, min(100, round(100 * (1 - km / self.cfg["detect"]["sense_km"]))))

    def _detect(self, t: float) -> None:
        det = self.cfg["detect"]
        eng = self.eng
        for h in list(self.hidden):
            d = eng.drones.get(h["drone"]) if h["drone"] else None
            if d is not None and d.mission == "investigate" and d.bat <= self.cfg["home_pct"]:
                eng.rtb(d, t, "low battery, heat reading left to another drone")
            if d is not None and d.mission != "investigate":  # e.g. went home on battery
                h["drone"], h["status"], d = None, "hidden", None
            sensing = [
                (self.confidence(dist(x.pos, h["at"])), x.id)
                for x in eng.drones.values()
                if x.state == "airborne"
                and x.mission in ("roam", "investigate")
                and x.launch_wait <= 0
            ]
            best, who = max(sensing, default=(0, None), key=lambda r: (r[0], r[1] == h["drone"]))
            if d is None and best >= det["possible_pct"] and eng.drones[who].mission == "roam":
                self._investigate(h, eng.drones[who], best)
                continue
            if d is not None:
                h["best"] = self.confidence(dist(d.pos, h["at"]))
                if h["best"] >= det["confirm_pct"]:
                    self._confirm(h, d)
                continue
            if not h["sent"] and t - h["t"] >= det["fallback_min"] - 1e-9:
                self._fallback(h)

    def _investigate(self, h: dict, d, pct: int) -> None:
        self.roam.pop(d.id, None)
        d.mission, d.on_station, d.target = "investigate", False, None
        h["drone"], h["best"], h["status"] = d.id, pct, "possible"
        text = (
            f"{d.id} picks up a possible heat source (simulated sensor, {pct}%) and goes to look."
        )
        self._say("heat_possible", text, None, drone=d.id, confidence_pct=pct, sensor="simulated")

    def _fallback(self, h: dict) -> None:
        """Guarantee: the nearest fit roamer is routed past the spot."""
        eng = self.eng
        gate = eng.M["launch_gate_pct"]["incident"]
        fit = [d for d in eng.drones.values() if d.mission == "roam" and d.bat >= gate]
        if not fit:
            return
        d = min(fit, key=lambda x: (dist(x.pos, h["at"]), x.id))
        h["sent"] = True
        self._investigate(h, d, self.confidence(dist(d.pos, h["at"])))

    def investigate_move(self, d) -> None:
        """Engine hook: fly to the heat reading."""
        h = next((x for x in self.hidden if x["drone"] == d.id), None)
        if h is None:
            self._to_roam(d)
            return
        self.eng._fly(d, h["at"], self.eng.M["cruise_km_per_min"])

    def street(self, p) -> str:
        """The nearest named place: the scenes double as the street names."""
        scenes = [s for v in self.cfg["scenes"].values() for s in v]
        return min(scenes, key=lambda s: (dist(p, s["at"]), s["name"]))["name"]

    def _confirm(self, h: dict, d) -> None:
        self.hidden.remove(h)
        street = self.street(h["at"])
        found_s = round((self.eng.now - h["t"]) * 60)
        found = {"drone": d.id, "how": "patrol sensor (simulated)", "found_s": found_s}
        text = f"{d.id} confirms a fire near {street} (simulated sensor, {h['best']}%)."
        self._say("fire_confirmed", text, None, drone=d.id, confidence_pct=h["best"])
        self.trigger("fire", scene={"name": street, "at": list(h["at"])}, found_by=found)

    def detections(self) -> list[dict]:
        return [
            {"drone": h["drone"], "confidence_pct": h["best"], "status": h["status"]}
            for h in self.hidden
            if h["status"] == "possible"
        ]

    def _assign_drone(self, inc: dict, mission: str = "incident", to=None) -> str | None:
        """Nearest roamer diverts; failing that, a READY drone launches. Real validators."""
        eng = self.eng
        to = list(to or inc["at"])
        gate = eng.M["launch_gate_pct"]["incident"]
        base = {"incident": inc["id"], "to": to}
        near = [d for d in eng.drones.values() if d.mission == "roam" and d.bat >= gate]
        for d in sorted(near, key=lambda x: (dist(x.pos, to), x.id)):
            args = {"drone": d.id, "mission": mission, **base}
            if actions.execute(eng, DISPATCH, "divert", args).ok:
                self.roam.pop(d.id, None)
                return d.id
        pilot = eng.pilots.pilot_for([]) or eng.pilots.first_human_remote_pilot()
        for d in eng.ready_candidates(to, gate):
            if pilot and eng.floor_allows(d):
                args = {"drone": d.id, "pilot": pilot, "basis": "city_policy", **base}
                if actions.execute(eng, DISPATCH, "launch", {"mission": "incident", **args}).ok:
                    d.mission = mission
                    return d.id
        return None

    # ------------------------------------------------------------------ the tick
    def tick(self, t: float) -> None:
        """Engine hook, in the auto-policy slot."""
        self._roam_policy(t)
        self._detect(t)
        for f in [x for x in self.flares if x["t"] <= t + 1e-9]:
            self.flares.remove(f)
            self.trigger(f["kind"], flare_of=f["of"], scene=f["scene"])
        for inc in list(self.active.values()):
            self._cover(inc)
            self._advance(inc, t)
        self._drive(t)

    def _cover(self, inc: dict) -> None:
        """Keep one drone on the scene: relief is on station before the old drone leaves."""
        eng = self.eng
        for key in ("drone", "relief"):
            d = eng.drones.get(inc[key]) if inc[key] else None
            if d is not None and (d.mission != "incident" or d.incident != inc["id"]):
                inc[key] = None  # e.g. a safety RTB
        if inc["drone"] is None and inc["relief"]:
            inc["drone"], inc["relief"] = inc["relief"], None
        if inc["drone"] is None:
            inc["drone"] = self._assign_drone(inc)
            return
        d = eng.drones[inc["drone"]]
        if inc["relief"] is None and d.bat <= eng.M["relief_trigger_pct"]:
            inc["relief"] = self._assign_drone(inc)
            if inc["relief"]:
                self._say("city_relief", f"{inc['relief']} is coming to relieve {d.id}.", inc)
        if inc["relief"] is None and d.bat <= self.cfg["home_pct"]:
            eng.rtb(d, eng.now, "low battery, no relief ready")
            self._say("city_relief", f"{d.id} is low and heads home. Nobody could relieve it.", inc)
            inc["drone"] = None
            return
        relief = eng.drones.get(inc["relief"]) if inc["relief"] else None
        if relief is not None and relief.on_station:
            inc["drone"], inc["relief"] = relief.id, None
            eng.rtb(d, eng.now, "relieved at the scene")
            self._say("city_relief", f"{relief.id} is on station. {d.id} heads home.", inc)

    def _advance(self, inc: dict, t: float) -> None:
        stage = inc["stage"]
        if stage == "alert" and inc["id"] in self.eng.kpi.eyes_on:
            self._eyes_on(inc, t)
        elif stage in ("responding", "working", "care_call", "transporting", "blocked"):
            self._assess(inc)
            self._send_vehicles(inc)
            self._care(inc)
            self._work(inc, t)
            if inc["escort"]:
                self._escort_tick(inc, t)
        # "watching" has no deadline: the drone keeps watching until the lead answers (L60)
        elif stage == "holding" and t >= inc["hold_until"] - 1e-9:
            inc["at_risk"] = False
            self._ask(inc, "Clear reading now.")

    def _eyes_on(self, inc: dict, t: float) -> None:
        secs = (self.eng.kpi.eyes_on[inc["id"]] - inc["t0"]) * 60
        inc["response_s"] = secs
        if not inc.get("found_by"):  # a drone that found it is there already: 0 s isn't a response
            self.responses.append(secs)
        inc["needs"] = list(self.cfg["services"][inc["kind"]])
        inc["stage"] = "responding"
        x, y = inc["at"]
        text = f"{inc['drone']} is over {inc['scene']} at ({x:g}, {y:g})."
        self._say("city_eyes_on", text, inc, drone=inc["drone"], response_s=round(secs))

    def _roll_injuries(self, inc: dict) -> int:
        """Seeded per incident kind and number, so other triggers can't change the outcome."""
        rng = random.Random(f"{self.eng.seed}:{inc['kind']}:{inc['serial']}")
        if rng.random() >= self.cfg["injury_chance"][inc["kind"]]:
            return 0
        return rng.randint(1, self.cfg["injuries_max"])

    def _assess(self, inc: dict) -> None:
        """Once the drone is on station and the first crew on scene: does anyone need a medic?"""
        d = self.eng.drones.get(inc["drone"]) if inc["drone"] else None
        if inc["injuries"] is not None or not inc["jobs"] or d is None or not d.on_station:
            return
        n = inc["injuries"] = self._roll_injuries(inc)
        if n:
            inc["needs"] += self.cfg["services"]["injuries"]
            text = f"{n} injured at {inc['scene']}. Ambulance requested from the Hospital."
            self._say("ambulance_requested", text, inc, injuries=n)
        else:
            text = f"Nobody hurt at {inc['scene']}. No ambulance needed."
            self._say("ambulance_not_needed", text, inc, injuries=0)

    def _ambulance(self, inc: dict):
        return next((v for v in inc["vehicles"] if self.vehicles[v].kind == "ambulance"), None)

    def _care(self, inc: dict) -> None:
        """An accident's ambulance on scene: treat here or take them in? Then the doctor's call."""
        amb = self._ambulance(inc)
        if inc["kind"] != "accident" or inc["care"] or amb not in inc["jobs"]:
            return
        rng = random.Random(f"{self.eng.seed}:care:{inc['serial']}")
        go = rng.random() < self.cfg["transport_chance"]
        n = inc["injuries"]
        if go:
            facts = f"{n} injured, condition needs the Hospital. Recommend transport."
        else:
            facts = f"{n} injured, stable and walking. Recommend treating on scene."
        inc["care"] = {"recommend": "transport" if go else "treat", "choice": None}
        self._say("care_assessed", facts, inc, recommend=inc["care"]["recommend"], injuries=n)
        inc["stage"] = "care_call"
        self._call(inc, TREAT_TRANSPORT, facts, f"Duty Doctor, {inc['scene']}: {facts}")

    def _send_vehicles(self, inc: dict) -> None:
        for kind in list(inc["needs"]):
            free = [v for v in self.vehicles.values() if v.kind == kind and v.state == "parked"]
            if not free:
                continue  # waits for one to come home
            v = min(free, key=lambda x: (dist(x.pos, inc["at"]), x.id))
            inc["needs"].remove(kind)
            v.state, v.incident = "en_route", inc["id"]
            v.path = self.roads.route(v.pos, inc["at"])
            inc["vehicles"].append(v.id)
            km = sum(dist(a, b) for a, b in zip([v.pos, *v.path], v.path, strict=False))
            eta = km / self.speed
            name = self.cfg["stations"][v.station]["name"]
            actions.log_decision(self.eng, "sim", "city:dispatch", rationale=f"{v.id} {inc['id']}")
            self._say(
                "city_dispatch",
                f"{name} sends {v.id.replace('_', ' ')} to {inc['scene']}.",
                inc,
                vehicle=v.id,
                kind=kind,
                station=v.station,
                eta_min=round(eta, 1),
            )

    def _work(self, inc: dict, t: float) -> None:
        for vid in inc["vehicles"]:
            v = self.vehicles[vid]
            job = inc["jobs"].get(vid)
            if vid == self._ambulance(inc) and inc["care"] and not inc["care"]["choice"]:
                continue  # the call stays open and the crew keeps treating
            if v.state == "working" and job and t - job["start"] >= job["min"] - 1e-9:
                job["done"] = True
                if vid == self._ambulance(inc) and (inc["care"] or {}).get("choice") == "transport":
                    self._start_transport(inc, v, t)
                    continue
                v.state, v.incident = "returning", None
                v.path = self.roads.route(v.pos, v.home)
                text = f"{job['label']} done. {v.id.replace('_', ' ')} heads back."
                self._say("city_work_done", text, inc, vehicle=vid)
        jobs = inc["jobs"].values()
        all_arrived = not inc["needs"] and len(inc["jobs"]) == len(inc["vehicles"])
        assessed = inc["injuries"] is not None
        moving = inc["escort"] and inc["escort"]["status"] != "delivered"
        if assessed and all_arrived and all(j["done"] for j in jobs) and not moving:
            inc["stage"] = "watching"
            self._ask(inc, "Looks clear." if not inc["at_risk"] else "Mostly clear, keep an eye.")
        elif inc["jobs"] and inc["stage"] == "responding":
            inc["stage"] = "working"

    def _call(self, inc: dict, kind: str, reading: str, text: str) -> None:
        deadline = self.eng.now + self.cfg["decision_timeout_min"]
        inc["decision"] = {"kind": kind, "reading": reading, "deadline": deadline}
        self._say(
            "city_decision",
            text,
            inc,
            drone=inc["drone"],
            at_risk=inc["at_risk"],
            kind=kind,
            role=ROLES[kind],
        )

    def _ask(self, inc: dict, reading: str) -> None:
        inc["stage"] = "watching"
        text = f"{reading} {inc['drone']} at {inc['scene']}: leave, or stay a little longer?"
        self._call(inc, LEAVE_STAY, reading, text)

    # ------------------------------------------------------------------ ambulance escort (L61)
    def _start_transport(self, inc: dict, v: Vehicle, t: float) -> None:
        v.state = "transporting"
        v.path = self.roads.route(v.pos, v.home)
        inc["stage"] = "transporting"
        e = {
            "ambulance": v.id,
            "drone": None,
            "relief": None,
            "status": "en_route",
            "block": None,
            "t_start": t,
            "t_stop": None,
            "t_end": None,
            "delay_min": 0.0,
        }
        inc["escort"] = e
        rng = random.Random(f"{self.eng.seed}:traffic:{inc['serial']}")
        blocked = rng.random() < self.cfg["traffic_chance"]
        on_route = v.path[1:-1]  # every stop between the two ends is a junction
        if blocked and on_route:
            e["block"] = {
                "at": rng.choice(on_route),
                "status": "pending",  # not served: nobody has seen it yet
                "incident": inc["id"],
                "start": None,
                "car": None,
                "requested": False,
            }
            self.traffic.append(e["block"])
        self._say("transport_started", f"Ambulance leaves {inc['scene']} for the Hospital.", inc)

    def _ahead(self, v: Vehicle) -> list[float]:
        """The point escort_lead_km ahead of the vehicle along its road route."""
        pos, left = list(v.pos), self.cfg["escort_lead_km"]
        for p in v.path:
            step = dist(pos, p)
            if step >= left and step > 0:
                f = left / step
                return [pos[0] + (p[0] - pos[0]) * f, pos[1] + (p[1] - pos[1]) * f]
            pos, left = list(p), left - step
        return pos

    def escort_move(self, d) -> None:
        """Engine hook: fly just ahead of the ambulance."""
        inc = self.active.get(d.incident)
        e = inc["escort"] if inc else None
        if e is None or e["status"] == "delivered":
            self._to_roam(d)
            return
        target = self._ahead(self.vehicles[e["ambulance"]])
        self.eng._fly(d, target, self.eng.M["cruise_km_per_min"])

    def _escort_cover(self, inc: dict, e: dict) -> None:
        """One drone on the escort; a relief reaches it before the tired drone leaves."""
        eng = self.eng
        for key in ("drone", "relief"):
            d = eng.drones.get(e[key]) if e[key] else None
            if d is not None and d.mission != "escort":
                e[key] = None  # e.g. a safety RTB
        if e["drone"] is None and e["relief"]:
            e["drone"], e["relief"] = e["relief"], None
        aim = self._ahead(self.vehicles[e["ambulance"]])
        if e["drone"] is None:
            e["drone"] = self._assign_drone(inc, "escort", aim)
            return
        d = eng.drones[e["drone"]]
        if e["relief"] is None and d.bat <= eng.M["relief_trigger_pct"]:
            e["relief"] = self._assign_drone(inc, "escort", aim)
        relief = eng.drones.get(e["relief"]) if e["relief"] else None
        if relief is not None and dist(relief.pos, d.pos) <= self.cfg["escort_handoff_km"]:
            e["drone"], e["relief"] = relief.id, None
            eng.rtb(d, eng.now, "relieved on the escort")
        elif relief is None and d.bat <= self.cfg["home_pct"]:
            eng.rtb(d, eng.now, "low battery, no relief ready")
            e["drone"] = None

    def _escort_tick(self, inc: dict, t: float) -> None:
        e = inc["escort"]
        if e["status"] == "delivered":
            return
        self._escort_cover(inc, e)
        b = e["block"]
        if b is None or b["status"] == "cleared":
            return
        amb = self.vehicles[e["ambulance"]]
        d = self.eng.drones.get(e["drone"]) if e["drone"] else None
        close = dist(amb.pos, b["at"]) <= self.cfg["traffic_stop_km"]
        if b["status"] == "pending" and (
            close or (d is not None and dist(d.pos, b["at"]) <= self.cfg["traffic_sight_km"])
        ):
            b["status"] = "blocked"
            x, y = b["at"]
            self._say(
                "traffic_blocked",
                f"Traffic is blocking the junction at ({x:g}, {y:g}) on the way to the Hospital.",
                inc,
                at=b["at"],
                drone=e["drone"],
            )
        if b["status"] in ("blocked", "clearing") and close and amb.state == "transporting":
            amb.state = "blocked"
            e["status"], e["t_stop"] = "blocked", t
            inc["stage"] = "blocked"
        if b["status"] == "blocked" and b["car"] is None:
            self._send_police(inc, b)
        if b["status"] == "clearing" and t - b["start"] >= self.cfg["traffic_clear_min"] - 1e-9:
            self._cleared(inc, e, b, t)

    def _send_police(self, inc: dict, b: dict) -> None:
        if not b["requested"]:
            b["requested"] = True
            self._say("police_requested", "Police asked to clear the junction.", inc, at=b["at"])
        free = [v for v in self.vehicles.values() if v.kind == "police_car" and v.state == "parked"]
        if not free:
            return  # waits for a car to come home
        car = min(free, key=lambda x: (dist(x.pos, b["at"]), x.id))
        car.state, car.incident, car.block = "en_route", inc["id"], b
        car.path = self.roads.route(car.pos, b["at"])
        b["car"] = car.id
        inc["traffic_cars"].append(car.id)
        actions.log_decision(self.eng, "sim", "city:dispatch", rationale=f"{car.id} traffic")

    def _cleared(self, inc: dict, e: dict, b: dict, t: float) -> None:
        b["status"] = "cleared"
        car = self.vehicles[b["car"]]
        car.state, car.incident, car.block = "returning", None, None
        car.path = self.roads.route(car.pos, car.home)
        amb = self.vehicles[e["ambulance"]]
        if amb.state == "blocked":
            amb.state = "transporting"
            e["delay_min"] += t - e["t_stop"]
        e["status"], e["t_stop"] = "en_route", None
        inc["stage"] = "transporting"
        self._say("traffic_cleared", "Junction clear. The ambulance moves on.", inc, at=b["at"])

    def _delivered(self, inc: dict, v: Vehicle, t: float) -> None:
        e = inc["escort"]
        v.state, v.incident = "parked", None
        e["status"], e["t_end"] = "delivered", t
        inc["stage"] = "working"
        for key in ("drone", "relief"):
            d = self.eng.drones.get(e[key]) if e[key] else None
            if d is not None and d.mission == "escort":
                self._to_roam(d)
        self._say("patient_delivered", "Patient handed over at the Hospital.", inc)

    def _drive(self, t: float) -> None:
        km = self.speed * DT
        for v in self.vehicles.values():
            if v.state not in ("en_route", "returning", "transporting") or not v.drive(km):
                continue
            if v.state == "returning":
                v.state = "parked"
                continue
            inc = self.active[v.incident]
            if v.state == "transporting":
                self._delivered(inc, v, t)
                continue
            v.state = "working"
            if v.block is not None:  # a police car at the blocked junction
                v.block["status"], v.block["start"] = "clearing", t
                continue
            spec = self.cfg["work_min"][v.kind]
            inc["jobs"][v.id] = {
                "label": spec["label"],
                "min": spec["min"],
                "start": t,
                "done": False,
            }
            name = v.id.replace("_", " ")
            self._say("city_on_scene", f"{name} is on scene: {spec['label']}.", inc, vehicle=v.id)

    # ------------------------------------------------------------------ the human decision
    def find(self, place: str) -> dict | None:
        """The one open incident whose name holds `place`; those awaiting a decision go first."""
        frag = place.strip().lower()
        hits = [i for i in self.active.values() if frag and frag in i["name"].lower()]
        waiting = [i for i in hits if i["decision"]]
        for group in (waiting, hits):
            if len(group) == 1:
                return group[0]
        return None

    def decide(
        self, iid: str | None, choice: str, actor: str = PLAYER.name, place: str | None = None
    ) -> dict:
        inc = self.active.get(iid) if iid else self.find(place or "")
        if inc is None:
            raise CityError(rules.not_found("open incident", iid or place))
        iid = inc["id"]
        if inc["decision"] is None:
            raise CityError(rules.mission_expired(f"the decision on {iid}", "closed"))
        kind = inc["decision"]["kind"]
        if choice not in CHOICES[kind]:
            raise ValueError(f"choice must be one of {sorted(CHOICES[kind])}")
        what = CHOICES[kind][choice]
        actions.log_decision(self.eng, actor, f"city:decide:{what}", rationale=iid)
        if kind == TREAT_TRANSPORT:
            self._care_choice(inc, what)
            return {"incident": iid, "stage": inc["stage"]}
        if what == "stay":
            self._stay(inc)
            return {"incident": iid, "stage": inc["stage"]}
        return self._leave(inc)

    def _care_choice(self, inc: dict, what: str) -> None:
        inc["decision"] = None
        inc["care"]["choice"] = what
        inc["stage"] = "working"
        job = inc["jobs"][self._ambulance(inc)]
        if what == "transport":
            job["label"], job["min"] = "Stabilising patient", self.cfg["stabilise_min"]

    def _stay(self, inc: dict, timed_out: bool = False) -> None:
        if timed_out:
            actions.log_decision(self.eng, "sim", "city:decide:stay", rationale="timed out")
        inc["decision"] = None
        inc["stage"] = "holding"
        inc["hold_until"] = self.eng.now + self.cfg["stay_min"]
        why = "No answer, so the drone stays" if timed_out else "The drone stays"
        self._say("city_stay", f"{why} at {inc['scene']} a little longer.", inc)

    def _leave(self, inc: dict) -> dict:
        eng = self.eng
        iid = inc["id"]
        args = {"incident": iid, "note": f"{inc['scene']} clear"}
        actions.execute(eng, DISPATCH, "close_incident", args)
        for key in ("drone", "relief"):
            if inc[key]:
                self._to_roam(eng.drones[inc[key]])
        del self.active[iid]
        self.traffic = [b for b in self.traffic if b["incident"] != iid]
        self.handled += 1
        report = self._report(inc)
        if inc["at_risk"] and eng.rng.random() < self.cfg["flare_chance"]:
            self.flares.append(
                {
                    "t": eng.now + self.cfg["flare_delay_min"],
                    "kind": inc["kind"],
                    "of": iid,
                    "scene": inc["scene_spec"],
                }
            )
        return {"incident": iid, "stage": "closed", "report": report}

    def _transport_report(self, inc: dict) -> dict | None:
        e = inc["escort"]
        if e is None:
            return None
        blocked = e["block"] is not None and e["block"]["status"] == "cleared"
        natural = self.cfg["traffic_natural_clear_min"]
        return {
            "transport_min": round(e["t_end"] - e["t_start"], 1),
            "delay_min": round(e["delay_min"], 1),
            "saved_min": round(max(0.0, natural - e["delay_min"]), 1) if blocked else 0.0,
        }

    def _report(self, inc: dict) -> dict:
        eng = self.eng
        rows = [r for r in eng.privacy.rows() if r["incident"] == inc["id"]]
        on = [r["clock"] for r in rows if r["kind"] == "recording_on"]
        report = {
            "incident": inc["id"],
            "kind": inc["kind"],
            "name": inc["name"],
            "scene": inc["scene"],
            "injuries": inc["injuries"],
            "response_s": round(inc.get("response_s", 0)),
            "who": list(inc["vehicles"]) + list(inc["traffic_cars"]),
            "care": (inc["care"] or {}).get("choice"),
            "transport": self._transport_report(inc),
            "recording": {"from": on[0] if on else None, "to": fmt(eng.now) if on else None},
            "timeline": list(inc["timeline"]),
        }
        found = inc.get("found_by")
        if found:
            report["found_s"] = found["found_s"]
            text = f"{inc['scene']} is closed. Found on patrol in {found['found_s']} s."
        else:
            text = f"{inc['scene']} is closed. First eyes on in {report['response_s']} s."
        self._say("city_report", text, inc, report=report)
        return report

    # ------------------------------------------------------------------ views
    def _label(self, d) -> str:
        if d.state == "docked":
            return "Ready" if d.bat >= self.eng.M["ready_pct"] else "Charging"
        if d.mission == "roam":
            return "On patrol"
        if d.mission == "escort":
            return "Escorting ambulance"
        if d.mission == "investigate":
            return "Checking a heat reading"
        if d.mission == "returning":
            return "Heading to dock"
        inc = self.active.get(d.incident)
        if inc is None:
            return "Flying"
        return f"Watching {inc['scene']}" if d.on_station else f"Heading to {inc['scene']}"

    def incident_view(self, inc: dict) -> dict:
        jobs = list(inc["jobs"].values())
        pct, label = 0, None
        if jobs:
            now = self.eng.now
            rows = [(min(1.0, (now - j["start"]) / j["min"]), j["label"]) for j in jobs]
            for vid in inc["vehicles"]:  # still driving in: its job hasn't started
                if vid not in inc["jobs"]:
                    rows.append((0.0, self.cfg["work_min"][self.vehicles[vid].kind]["label"]))
            share, name = min(rows)
            pct = int(share * 100)
            label = f"{name} {pct}%"
        out = {
            "id": inc["id"],
            "kind": inc["kind"],
            "name": inc["name"],
            "scene": inc["scene"],
            "at": inc["at"],
            "stage": inc["stage"],
            "progress_pct": pct,
            "progress_label": label,
            "drone": inc["drone"],
            "vehicles": list(inc["vehicles"]) + list(inc["traffic_cars"]),
        }
        if inc["decision"]:
            kind = inc["decision"]["kind"]
            left = (inc["decision"]["deadline"] - self.eng.now) / self.cfg["sim_min_per_s"]
            ask = " Leave, or stay a little longer?" if kind == LEAVE_STAY else ""
            out["decision"] = {
                "kind": kind,
                "role": ROLES[kind],
                "prompt": f"{inc['decision']['reading']}{ask}",
                "options": OPTIONS[kind],
                "remaining_s": round(max(0.0, left), 1),
            }
        if inc["escort"]:
            out["escort"] = self._escort_view(inc["escort"])
        if inc.get("found_by"):
            out["found_by"] = dict(inc["found_by"])
        return out

    def _escort_view(self, e: dict) -> dict:
        b = e["block"]
        seen = b is not None and b["status"] != "pending"
        delay = e["delay_min"]
        if e["t_stop"] is not None:
            delay += self.eng.now - e["t_stop"]
        return {
            "ambulance": e["ambulance"],
            "drone": e["drone"],
            "status": e["status"],
            "blocked_at": b["at"] if seen else None,
            "delay_s": round(delay * 60),
        }

    def _traffic_view(self, b: dict) -> dict:
        pct = 100 if b["status"] == "cleared" else 0
        if b["status"] == "clearing":
            pct = int(min(1.0, (self.eng.now - b["start"]) / self.cfg["traffic_clear_min"]) * 100)
        return {"at": b["at"], "status": b["status"], "progress_pct": pct}

    def view(self, since: int = 0) -> dict:
        eng = self.eng
        minute = eng.now % 1440
        n = len(self.responses)
        return {
            "started": True,
            "clock_label": f"{self.cfg['day']} {fmt(minute)[:5]}",
            "counters": {
                "handled": self.handled,
                "avg_response_s": round(sum(self.responses) / n) if n else None,
                "drones_up": sum(1 for d in eng.drones.values() if d.state == "airborne"),
            },
            "drones": [
                {
                    "id": d.id,
                    "pos": [round(v, 3) for v in d.pos],
                    "battery": round(d.bat, 1),
                    "state": d.state,
                    "label": self._label(d),
                    "recording": d.recording,
                }
                for d in eng.drones.values()
            ],
            "vehicles": [v.view() for v in self.vehicles.values()],
            "stations": stations_view(self.cfg),
            "incidents": [self.incident_view(i) for i in self.active.values()],
            "traffic": [self._traffic_view(b) for b in self.traffic if b["status"] != "pending"],
            "detections": self.detections(),
            "feed": list(self.feed),
            "events": [e for e in self.events if e["seq"] > since],
        }


def stations_view(cfg: dict) -> list[dict]:
    return [
        {"id": k, "name": s["name"], "kind": k, "at": s["at"], "road": s["road"]}
        for k, s in cfg["stations"].items()
    ]


def idle_view(world) -> dict:
    cfg = world.raw["city"]
    return {
        "started": False,
        "clock_label": f"{cfg['day']} {cfg['start']}",
        "counters": {"handled": 0, "avg_response_s": None, "drones_up": 0},
        "drones": [],
        "vehicles": [],
        "stations": stations_view(cfg),
        "incidents": [],
        "traffic": [],
        "detections": [],
        "feed": [],
        "events": [],
    }
