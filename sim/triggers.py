"""Live trigger menu (scenarios/triggers.yaml → POST /trigger). Operator control (L33).

Every trigger happens at "now": nothing is pre-scheduled, so nothing about the future leaks.
| kind               | effect                                                                 |
| fire               | INC-T fire; auto-launch with no thermal penalty (thermal preferred)    |
| missing_child      | INC-T missing_person; auto-launches a search pair (2 drones, `search`) |
| underpass_flood    | INC-T flood; auto-launch                                               |
| drone_fault        | tightest airborne drone: safety RTB now + pending out_of_service card  |
| unauthorised_drone | SEPARATION hold in the sector for policies.intrusion_hold_min          |
| vip_zone           | pending `zone` card for VIP-TEMP until now + offset                    |
| officer_request    | `room_message` event for the World Bot; no sim effect                  |
"""

from __future__ import annotations

import math
import re

from sim.geo import fmt, sector_of
from sim.scenario import load_triggers


class TriggerNotFound(KeyError):
    pass


class InvalidAt(ValueError):
    """Malformed `at` → HTTP 422, no state change."""


_HHMM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def parse_at(world, at):
    """None (absent, or an HH:MM time, which is ignored: triggers fire now), [x, y] inside the
    district, or a landmark name. Anything else raises InvalidAt."""
    if at is None or (isinstance(at, str) and _HHMM.fullmatch(at)):
        return None
    if isinstance(at, str) and at in world.landmarks:
        return at
    if isinstance(at, list) and len(at) == 2:
        nums = all(isinstance(v, int | float) and not isinstance(v, bool) for v in at)
        if nums and all(map(math.isfinite, at)):
            w, h = world.raw["district"]["size_km"]
            if 0 <= at[0] <= w and 0 <= at[1] <= h:
                return [float(at[0]), float(at[1])]
    raise InvalidAt(f"at must be [x, y] inside the district or a landmark, got {at!r}")


def menu() -> dict:
    return load_triggers()


def fire(eng, kind: str, at=None) -> dict:
    spec = menu().get(kind)
    if spec is None:
        raise TriggerNotFound(kind)
    at = parse_at(eng.world, at)
    t = eng.now
    if "cad_alert" in spec:
        alert = dict(spec["cad_alert"])
        if at is not None:
            alert["at"] = at
        for k in ("auto_launch", "thermal_preferred", "search_pair"):
            if spec.get(k):
                alert[k] = True
        iid = eng.inject_alert(alert)
        return {"incident": iid, "kind": alert["kind"], "clock": fmt(t)}
    if "drone_fault" in spec:
        return _drone_fault(eng, t, spec["drone_fault"])
    if "airspace_intrusion" in spec:
        where = spec["airspace_intrusion"]["at"] if at is None else eng.world.point(at)
        return _intrusion(eng, t, list(where))
    if "approval" in spec:
        return _approval(eng, t, spec["approval"])
    if "room_message" in spec:
        m = spec["room_message"]
        eng.emit(
            t, "room_message", m["text"], {"room": m["room"], "text": m["text"]}, timeline=False
        )
        return {"room": m["room"], "text": m["text"], "clock": fmt(t)}
    raise TriggerNotFound(kind)


def tightest_airborne(eng):
    c = [d for d in eng.drones.values() if d.state == "airborne" and d.mission != "returning"]
    return min(c, key=lambda d: (d.bat, d.id)) if c else None


def _drone_fault(eng, t: float, spec: dict) -> dict:
    d = tightest_airborne(eng) if spec.get("pick") == "tightest_airborne" else None
    if d is None:
        eng.emit(t, "drone_fault", "drone fault trigger: no airborne drone", {}, timeline=False)
        return {"drone": None, "approval_id": None, "clock": fmt(t)}
    fault = spec.get("kind", "fault")
    d.fault = fault
    eng.emit(
        t,
        "drone_fault",
        f"⚠ {d.id} fault ({fault}) at {d.bat:.0f}% → safety RTB, out of service pending approval",
        {"drone": d.id, "fault": fault, "battery": round(d.bat, 1)},
    )
    eng.rtb(d, t, f"safety ({fault} fault)")
    a = eng.approvals.propose(
        t,
        "sim",
        "out_of_service",
        {"drone": d.id, "why": fault},
        f"{d.id} out of service ({fault})",
    )
    _card(eng, t, a)
    return {"drone": d.id, "approval_id": a.id, "dock": d.target, "clock": fmt(t)}


def _intrusion(eng, t: float, where) -> dict:
    sector = sector_of(where)
    until = round(t + eng.world.policies["intrusion_hold_min"], 4)
    eng.intrusions.append({"sector": sector, "at": where, "until": until})
    held = []
    for d in eng.drones.values():
        if d.state == "airborne" and d.mission != "returning" and sector_of(d.pos) == sector:
            d.hold_until = until
            held.append(d.id)
    eng.emit(
        t,
        "airspace_intrusion",
        f"🛸 unauthorised drone at {where} ({sector}) → SEPARATION hold until {fmt(until)}"
        + (f"; holding {held}" if held else ""),
        {"sector": sector, "at": where, "until": fmt(until), "code": "SEPARATION", "held": held},
    )
    return {"sector": sector, "until": fmt(until), "held": held, "clock": fmt(t)}


def _approval(eng, t: float, spec: dict) -> dict:
    args = {"zone": spec["id"], "until": fmt(t + spec.get("until_offset", 60))}
    a = eng.approvals.propose(
        t, "sim", spec["type"], args, f"activate {spec['id']} until {args['until']}"
    )
    _card(eng, t, a)
    return {"approval_id": a.id, "type": a.type, "until": args["until"], "clock": fmt(t)}


def _card(eng, t: float, a) -> None:
    eng.emit(
        t,
        "approval_needed",
        f"approval needed: {a.type} {a.id} ({a.summary}), watch_commander",
        {"approval_id": a.id, "what": a.type, "needs": "watch_commander", **a.args},
        timeline=False,
    )
