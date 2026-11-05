"""Game mode: "Friday Rush", a 3-minute playable shift (docs/game-mode.md, L54).

A thin layer over the real engine. The scenario's golden actions run as usual, except the
moments that belong to the player: there the game opens a prompt and the clock holds. Every
choice goes through actions.execute / propose / decide, so validators and logs are the real
ones. Players act as `kazum (dashboard)`.

Determinism: ticks are stepped by Game.step(); same choices + same fly inputs at the same ticks
give the same events and score. Real time only decides *when* the timeout default fires.
Numbers come from world/scenarios; the scenario queue stays internal, as everywhere else.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from sim import actions, baseline, golden, kpi, rules, suspect
from sim.auth import Identity
from sim.geo import DT, EPS, dist, fmt, tmin

PLAYER = Identity(
    "human",
    "kazum (dashboard)",
    ("watch_commander", "remote_pilot", "field_officer", "game_operator"),
    pilot="kazum",
)
DISPATCH = Identity.agent("dispatch")
BISECT_STEPS = 20  # how finely a blocked move is clipped to the zone boundary

STEPS = ("idle", "crash", "robbery", "trap", "finale", "score")
OPTIONS = {
    "crash": [("release", "RELEASE the unit"), ("keep", "Keep it on scene")],
    "robbery": [("approve", "APPROVE track"), ("reject", "REJECT track")],
    "trap_face": [("try", "Try it"), ("lawful", "Use the lawful alternative")],
    "trap_route": [("try", "Try it"), ("lawful", "Use the lawful alternative")],
    "finale": [("take_control", "TAKE CONTROL"), ("leave", "Leave it to the ground units")],
    "finale_no_track": [("leave", "Leave it to the ground units")],
}
SAFE_DEFAULT = {
    "crash": "keep",
    "robbery": "reject",
    "trap_face": "lawful",
    "trap_route": "lawful",
    "finale": "leave",
    "finale_no_track": "leave",
}
RATIO = {"lawful": 1.0, "try": 0.5, "timed out": 0.5}  # share of a trap's rule points


class GameError(Exception):
    """A rule rejection for the API (always a §4.2 code)."""

    def __init__(self, rejection: rules.Rejection):
        self.rejection = rejection


class Flight:
    """The drone under take_control. Stepped by the engine in place of its mission movement."""

    def __init__(self, drone: str):
        self.drone = drone
        self.dx = self.dy = 0.0
        self.blocked = False

    def tick(self, eng, d, t: float) -> None:
        M = eng.M
        rates = M["drain_pct_per_min"]
        if not (self.dx or self.dy):
            d.bat -= rates["hover"] * DT
            return
        d.bat -= rates["cruise"] * DT
        step = M["cruise_km_per_min"] * DT
        w, h = eng.world.raw["district"]["size_km"]
        new = [
            min(w, max(0.0, d.pos[0] + self.dx * step)),
            min(h, max(0.0, d.pos[1] + self.dy * step)),
        ]
        zones = eng.airspace.no_entry()
        denied = rules.no_fly_zone(d.id, d.pos, new, zones)
        if denied.ok:
            d.pos, self.blocked = new, False
            return
        d.pos = _clip(d.pos, new, zones)
        if not self.blocked:  # one event per contact with the boundary
            self.blocked = True
            eng.emit(
                t,
                "game_blocked",
                f"⛔ {denied.reason}",
                {"drone": d.id, "code": denied.code, "reason": denied.reason},
                timeline=False,
            )


def _clip(a, b, zones) -> list[float]:
    """The furthest point along a→b that stays clear of every zone."""
    lo, hi = 0.0, 1.0
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        p = [a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid]
        if rules.no_fly_zone("", a, p, zones).ok:
            lo = mid
        else:
            hi = mid
    return [a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo]


class Game:
    def __init__(self, eng, time_source: Callable[[], float] = time.monotonic):
        self.eng = eng
        self.time = time_source
        pol = eng.world.policies
        self.cfg = pol["game_clock"]
        self.catch_km = pol["game_catch_km"]
        self.score_cfg = pol["game_score"]
        self.step_name = "idle"
        self.prompt: dict | None = None
        self.result: dict | None = None
        self.choices: dict[str, str] = {}  # beat -> choice ("timed out" recorded separately)
        self.timed_out: list[str] = []
        self.approval_id: str | None = None
        self.track_approved = False
        self.deferred: list[tuple[float, dict]] = []
        self.flight: Flight | None = None
        self.last_fly = 0.0
        self.caught_at: float | None = None
        self.suspect_incident: str | None = None
        self.final: dict | None = None
        self.done = False
        actions_ = eng.scenario.get("golden_actions") or []
        for a in actions_:
            if a["act"] == "close_incident":
                self.suspect_incident = a["incident"]
        beats = [tmin(e["t"]) for e in eng.scenario["events"] if e["type"] == "cad_alert"]
        beats += [
            tmin(a["t"])
            for a in actions_
            if a["act"] in ("release_unit", "reacquire")
            or (a["act"] == "approve" and a["what"] == "track")
            or (a["act"] == "refuse" and a["code"] in ("PRIVACY_POLICY", "NO_FLY_ZONE"))
        ]
        self._beats = sorted(beats)
        eng._action_handler = self._apply

    # ------------------------------------------------------------------ flow
    def start(self) -> None:
        """Jump to just before the first alert, standing policy already approved."""
        target = self._beats[0] - self.cfg["jump_lead_min"]
        self.eng.run_until(target - DT)
        self.step_name = "crash"

    def step(self) -> bool:
        """One tick. False when held at a prompt or finished."""
        self.poll()
        if self.done or self.prompt is not None:
            return False
        self.eng.step()
        self._after_tick()
        return not self.done and self.prompt is None

    def run_until(self, minutes) -> None:
        """Advance, stopping at any prompt (operator skip and tests)."""
        target = tmin(minutes)
        while not self.done and self.eng._t <= target + EPS and self.step():
            pass

    def poll(self) -> None:
        """Real-time side: a decision typed in the room, the prompt fallback, fly idling."""
        if self.prompt is not None and self.approval_id:
            a = self.eng.approvals.items[self.approval_id]
            if a.status != "pending":  # decided elsewhere (`approve A-000n` in #ops)
                self._resolved_track(a.status == "approved", a.decided_by)
        if self.prompt is not None and self.remaining_s() <= 0:
            self.timeout()
        idle = self.time() - self.last_fly >= self.cfg["fly_idle_s"]
        if self.flight is not None and idle:
            self.flight.dx = self.flight.dy = 0.0

    def remaining_s(self) -> float:
        left = self.prompt["opened"] + self.cfg["prompt_timeout_s"] - self.time()
        return max(0.0, left)

    def timeout(self) -> None:
        beat = self.prompt["beat"]
        self.timed_out.append(beat)
        self.choose(SAFE_DEFAULT[beat], timed_out=True)

    def skip_target(self) -> float | None:
        """Sim time to auto-skip to (internal: never served). None around a beat."""
        now = self.eng._t
        lead = self.cfg["beat_lead_min"]
        if self.prompt is not None or self.step_name == "finale":
            return None
        for b in self._beats:
            if now <= b + lead:
                return None if now >= b - lead else b - lead
        return None

    # ------------------------------------------------------------------ scripted actions
    def _apply(self, eng, t: float, a: dict) -> None:
        act = a["act"]
        if act == "release_unit":
            self._open("crash", "crash", self._crash_text(a), a)
        elif act == "approve":
            if a["what"] == "track":  # other golden approvals aren't in the game
                self._open_track(t, a)
        elif act == "divert":
            if self.prompt is not None and self.prompt["beat"] == "robbery":
                self.deferred.append((t, a))
            elif self.track_approved:
                golden.apply_action(eng, t, a)
        elif act == "refuse" and a["code"] == "PRIVACY_POLICY":
            self.step_name = "trap"
            self._open("trap_face", "trap", f"Field officer asks: '{a['request']}'", a)
        elif act == "refuse" and a["code"] == "NO_FLY_ZONE":
            self.step_name = "trap"
            self._open("trap_route", "trap", f"Field officer asks: '{a['request']}'", a)
        elif act == "hold":
            if self.track_approved and eng.tracker_id:
                golden.apply_action(eng, t, a)
        elif act == "reacquire":
            self.step_name = "finale"
            if self.track_approved:
                golden.apply_action(eng, t, a)
                d = self._flyer(a)
                text = (
                    f"The suspect is out and {a['drone']} is watching the exit. "
                    f"Take control of {d} and fly it to the scooter"
                )
                self._open("finale", "finale", text, a)
            else:
                text = "No track authorisation: ground units carry on without you"
                self._open("finale_no_track", "finale", text, a)
        elif act == "take_control" or a.get("expect"):
            return  # the player's own takeover; the scripted extra launch isn't in the game
        elif act in ("close_incident", "task_unit") and self._closed(a["incident"]):
            return
        else:
            golden.apply_action(eng, t, a)

    def _flyer(self, a: dict) -> str:
        """The drone holding at the red line (far from the exit); the watcher if there is none."""
        held = [d.id for d in self.eng.drones.values() if d.mission == "hold"]
        return held[0] if held else a["drone"]

    def _closed(self, iid: str) -> bool:
        return self.eng.incidents.get(iid, {}).get("status") == "closed"

    def _crash_text(self, a: dict) -> str:
        return f"Feed: {golden._quote_fact(self.eng, a)}. Release {a['unit']} from the scene?"

    def _open(self, beat: str, step: str, text: str, a: dict) -> None:
        self.step_name = step
        self.prompt = {"beat": beat, "text": text, "action": a, "opened": self.time()}
        self.eng.emit(
            self.eng.now,
            "game_prompt",
            f"🎮 {text}",
            {"step": step, "beat": beat, "options": [k for k, _ in OPTIONS[beat]]},
            timeline=False,
        )

    def _open_track(self, t: float, a: dict) -> None:
        from sim.approvals import track_minutes

        args = {k: a[k] for k in ("incident", "scope") if k in a}
        args["minutes"] = track_minutes(args)
        p = actions.propose(self.eng, DISPATCH, "track", args, f"track ({a.get('scope', '')})")
        self.approval_id = p.result["id"]
        self._open("robbery", "robbery", f"Track needs your approval: {a.get('scope', '')}", a)

    # ------------------------------------------------------------------ player
    def choose(self, choice: str, timed_out: bool = False) -> dict:
        if self.prompt is None:
            raise GameError(rules.mission_expired("this prompt", "closed"))
        beat = self.prompt["beat"]
        if choice not in [k for k, _ in OPTIONS[beat]]:
            raise ValueError(f"choice must be one of {[k for k, _ in OPTIONS[beat]]}")
        a = self.prompt["action"]
        res = getattr(self, "_on_" + beat)(choice, a, timed_out) or {}
        label = "timed out" if timed_out else choice
        self.choices[beat] = label
        self.result = {"beat": beat, "choice": choice, "timed_out": timed_out, **res}
        actions.log_decision(
            self.eng,
            PLAYER.name,
            f"game:{beat}",
            code=res.get("code"),
            rationale=f"{choice}" + (" (timed out)" if timed_out else ""),
        )
        self.eng.emit(
            self.eng.now,
            "game_choice",
            f"🎮 {beat}: {choice}" + (" (timed out)" if timed_out else ""),
            {"beat": beat, "choice": choice, "timed_out": timed_out, **res},
            timeline=False,
        )
        self.prompt = None
        self._after_choice(beat)
        return self.result

    def _after_choice(self, beat: str) -> None:
        pending, self.deferred = self.deferred, []
        for t, a in pending:
            self._apply(self.eng, t, a)
        if beat == "crash":
            self.step_name = "robbery"
        elif beat == "robbery":
            self.step_name = "trap"
        elif beat == "trap_route":
            self.step_name = "finale"

    def _on_crash(self, choice, a, timed_out):
        if choice == "keep":
            return {"ok": True}
        args = {"unit": a["unit"], "evidence": golden._quote_fact(self.eng, a), "note": a["note"]}
        return _outcome(actions.execute(self.eng, PLAYER, "release_unit", args))

    def _on_robbery(self, choice, a, timed_out):
        ok = choice == "approve"
        note = "timed out" if timed_out else "dashboard"
        out = actions.decide(self.eng, PLAYER, self.approval_id, ok, note)
        self.track_approved = ok and out.ok
        self.approval_id = None
        return _outcome(out)

    def _resolved_track(self, approved: bool, by) -> None:
        self.approval_id = None
        self.choose_elsewhere(approved, by)

    def choose_elsewhere(self, approved: bool, by) -> None:
        """The card was decided in the room; the game follows, the decision is already logged."""
        beat = "robbery"
        self.track_approved = approved
        choice = "approve" if approved else "reject"
        self.choices[beat] = choice
        self.result = {"beat": beat, "choice": choice, "timed_out": False, "ok": True, "by": by}
        self.prompt = None
        self._after_choice(beat)

    def _on_trap_face(self, choice, a, timed_out):
        if choice == "lawful":
            return self._declined(a)
        out = actions.execute(
            self.eng, PLAYER, "request_face_id", {"request": a["request"], "by": a.get("by")}
        )
        self._refusal_line(a, out)
        return _outcome(out)

    def _on_trap_route(self, choice, a, timed_out):
        if choice == "lawful":
            return self._declined(a)
        drone = self._route_drone()
        r = self.eng.airspace.red
        args = {
            "drone": drone.id,
            "incident": drone.incident,
            "to": [(r["x"][0] + r["x"][1]) / 2, (r["y"][0] + r["y"][1]) / 2],
        }
        out = actions.execute(self.eng, PLAYER, "corridor_watch", args)
        self._refusal_line(a, out)
        return _outcome(out)

    def _route_drone(self):
        eng = self.eng
        if eng.tracker_id:
            return eng.drones[eng.tracker_id]
        mine = [
            d
            for d in eng.drones.values()
            if d.state == "airborne"
            and d.incident == self.suspect_incident
            and d.mission != "returning"
        ]
        return mine[0]

    def _declined(self, a: dict) -> dict:
        args = {"about": a["request"], "code": a["code"], "by": a.get("by")}
        return _outcome(actions.execute(self.eng, PLAYER, "log_refusal", args))

    def _refusal_line(self, a: dict, out) -> None:
        if not out.ok:
            code = out.rejection.code
            self.eng.emit(
                self.eng.now,
                "refusal",
                f"⛔ {code}: '{a['request']}'",
                {"code": code, "request": a["request"], "by": a.get("by")},
            )

    def _on_finale(self, choice, a, timed_out):
        if choice == "leave":
            return {"ok": True}
        drone = self._flyer(a)
        out = actions.execute(self.eng, PLAYER, "take_control", {"drone": drone})
        if out.ok:
            self.flight = Flight(drone)
            self.eng.flight = self.flight
            self.last_fly = self.time()
        return _outcome(out)

    def _on_finale_no_track(self, choice, a, timed_out):
        return {"ok": True}

    def fly(self, dx: float, dy: float) -> dict:
        if self.flight is None:
            raise GameError(rules.auth_required("manual flight", False, "this drone"))
        self.flight.dx, self.flight.dy = dx, dy
        self.last_fly = self.time()
        return self.flight_view()

    # ------------------------------------------------------------------ catch + end
    def _after_tick(self) -> None:
        eng = self.eng
        if self.flight is not None:
            d = eng.drones[self.flight.drone]
            if d.mission not in ("track", "hold"):  # safety RTB took it home
                self._land_flight()
            elif dist(d.pos, eng.suspect.pos(eng.now)) <= self.catch_km:
                self._caught(d)
                return
        if self.step_name == "finale" and self.prompt is None and self.suspect_incident:
            if self._closed(self.suspect_incident):
                self._finish()
        if eng.done and not self.done:
            self._finish()

    def _land_flight(self) -> None:
        if self.flight is not None:
            d = self.eng.drones[self.flight.drone]
            if d.mission == "hold":  # stays where the player left it
                d.target = list(d.pos)
        self.flight = None
        self.eng.flight = None

    def _caught(self, d) -> None:
        eng = self.eng
        self.caught_at = eng.now
        eng.emit(
            eng.now,
            "game_catch",
            f"🎯 caught by you: {d.id}",
            {"drone": d.id, "battery": round(d.bat, 1)},
            timeline=False,
        )
        note = f"suspect caught by you ({d.id}, ground units close in)"
        args = {"incident": self.suspect_incident, "note": note}
        actions.execute(eng, DISPATCH, "close_incident", args)
        actions.execute(eng, PLAYER, "release_control", {"drone": d.id})
        self._land_flight()
        self._finish()

    def _finish(self) -> None:
        """The chase is over: the rest of the shift runs at once, for the real scorecard."""
        self._land_flight()
        eng = self.eng
        eng.run()
        eng.baseline_result = baseline.run(eng.world, eng.scenario)
        self.step_name = "score"
        self.prompt = None
        self.final = self._final()
        self.done = True

    # ------------------------------------------------------------------ score
    def _points(self) -> dict[str, dict]:
        eng = self.eng
        w = self.score_cfg["weights"]
        first = self._beats and next(iter(sorted(eng.kpi.eyes_on)), None)
        share = 0.5 * bool(first) + 0.5 * (self.choices.get("crash") == "release")
        traps = [self.choices.get(k) for k in ("trap_face", "trap_route")]
        rule = sum(RATIO.get(x, 0.0) for x in traps) / len(traps)
        if eng.violations or eng.kpi.red_zone_entries or eng.kpi.reserve_breaches:
            rule = 0.0
        over = self.done or self.final is not None or eng.done
        cover = eng.kpi.coverage_pct(eng) / 100 if over else 0.0
        pts = {
            "response": (share, "eyes-on + unit released on evidence"),
            "approval": (float(self.track_approved), "track approved"),
            "rules": (rule, "rules kept"),
            "catch": (float(self.caught_at is not None), "caught by you"),
            "coverage": (cover, "patrol coverage"),
        }
        return {
            k: {"points": round(w[k] * v), "max": w[k], "detail": detail}
            for k, (v, detail) in pts.items()
        }

    def score_so_far(self) -> int:
        return sum(p["points"] for p in self._points().values())

    def _rank(self, score: int) -> str:
        ranks = sorted(self.score_cfg["ranks"].items(), key=lambda r: -r[1])
        return next(name for name, floor in ranks if score >= floor)

    def _final(self) -> dict:
        eng = self.eng
        pts = self._points()
        score = sum(p["points"] for p in pts.values())
        rows = kpi.compare(eng, eng.baseline_result)
        return {
            "score": score,
            "max": sum(p["max"] for p in pts.values()),
            "rank": self._rank(score),
            "breakdown": pts,
            "caught": self.caught_at is not None,
            "caught_at": fmt(self.caught_at) if self.caught_at is not None else None,
            "timed_out": list(self.timed_out),
            "old_way": [
                {"label": r["label"], "you": r["managed"], "old_way": r["baseline"]}
                for r in rows
                if r["counted"]
            ],
        }

    # ------------------------------------------------------------------ views
    def flight_view(self) -> dict | None:
        if self.flight is None:
            return None
        eng = self.eng
        d = eng.drones[self.flight.drone]
        seen = suspect.visible_position(eng)
        km = dist(d.pos, seen) if seen else 0.0
        return {
            "drone": d.id,
            "battery": round(d.bat, 1),
            "pos": [round(v, 3) for v in d.pos],
            "suspect": seen,  # current position only, and only while a tracker has it in view
            "distance_km": round(km, 3) if seen else None,
            "steer": [round((seen[i] - d.pos[i]) / (km or 1), 3) for i in (0, 1)] if seen else None,
            "blocked": self.flight.blocked,
        }

    def view(self) -> dict:
        p = self.prompt
        beat = p["beat"] if p else None
        return {
            "step": self.step_name,
            "beat": beat,
            "prompt": p["text"] if p else None,
            "options": [{"id": k, "label": v} for k, v in OPTIONS[beat]] if p else [],
            "clock": fmt(self.eng.now),
            "score_so_far": self.score_so_far(),
            "done": self.done,
            "approval_id": self.approval_id,
            "remaining_s": round(self.remaining_s(), 1) if p else None,
            "result": self.result,
            "flight": self.flight_view(),
        }

    def score_view(self) -> dict:
        if self.final is None:
            return {"done": False, "score_so_far": self.score_so_far()}
        return {"done": True, **self.final}


def idle_view() -> dict:
    return {
        "step": "idle",
        "beat": None,
        "prompt": "Start the shift: you are the Watch Commander",
        "options": [],
        "clock": None,
        "score_so_far": 0,
        "done": False,
        "approval_id": None,
        "remaining_s": None,
        "result": None,
        "flight": None,
    }


def _outcome(out) -> dict:
    if out.ok:
        return {"ok": True}
    r = out.rejection
    return {"ok": False, "code": r.code, "reason": r.reason, "hint": r.hint}


def valid_direction(dx, dy) -> bool:
    """A unit vector, or 0,0 to hold."""
    for v in (dx, dy):
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            return False
    return (dx == 0 and dy == 0) or abs(math.hypot(dx, dy) - 1) <= 0.01
