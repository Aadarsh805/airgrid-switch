"""Validated actions: execute(engine, identity, action, args) for POST /actions, plus approval
proposals and decisions. Role-checked against config/roles.yaml (names mapped below).
Every write and every rejection appends a decisions_log row.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from sim import incidents, privacy, rules
from sim.approvals import SILENT, TYPES, WHAT, track_minutes, zone_until
from sim.auth import Identity, roles_config
from sim.geo import dist, fmt, mmss, sector_of
from sim.rules import Rejection

# config/roles.yaml name -> actions it grants
ROLE_ACTIONS = {
    "incident_open": ["open_incident"],
    "incident_close": ["close_incident"],
    "launch": ["launch", "divert", "assign_track", "assign_perimeter"],
    "propose_broadcast": ["broadcast"],
    "rtb": ["rtb", "dock_at", "rotate_patrol"],
    "hold": ["hold"],
    "corridor": ["corridor_watch"],
    "propose_zone": ["activate_zone"],
    "evidence_tag": ["evidence_tag"],
    "propose_footage_release": ["request_footage_release", "release_footage"],
    "privacy_check": ["privacy_check", "set_recording"],
    "unit_task": ["task_unit"],
    "unit_release": ["release_unit", "stand_down"],
    "advisory": ["issue_advisory", "retime_signal"],
    "log_refusal": ["log_refusal"],
    "simulate": ["simulate"],
}
# config/roles.yaml name -> approval types an agent holding it may propose
ROLE_PROPOSALS = {
    "propose_track": ["track"],
    "propose_broadcast": ["broadcast"],
    "propose_zone": ["zone"],
    "propose_out_of_service": ["out_of_service"],
    "propose_footage_release": ["footage_release"],
    "launch": ["divert_off_incident", "standing_policy"],
}
HUMAN_ACTIONS = {"take_control", "release_control"}  # role from roles.yaml approvals
# Actions a human may also perform directly, by the 04 §4.3 role that owns that domain
# (solo kazum acts without an agent in between). Agents keep their roles.yaml grants.
HUMAN_ROLE_ACTIONS = {
    "watch_commander": [
        "launch",
        "divert",
        "assign_track",
        "assign_perimeter",
        "broadcast",
        "release_footage",
        "set_recording",
    ],
    "remote_pilot": ["launch", "take_control", "release_control", "dock_at"],
    "field_officer": [],
    # game mode only (L54): the dashboard identity also plays the desks' moments of the shift
    "game_operator": ["release_unit", "corridor_watch"],
}
OPEN_ACTIONS = {"request_face_id"}  # anyone may ask; the answer is always PRIVACY_POLICY
LAUNCH_MISSIONS = ("incident", "perimeter", "corridor", "patrol", "search")
HOVER_MISSIONS = ("incident", "perimeter", "corridor", "search")


class InvalidRequest(ValueError):
    """Malformed args → HTTP 422 (not a rule rejection, no reason code)."""


class _Reject(Exception):
    def __init__(self, rejection: Rejection):
        self.rejection = rejection


@dataclass
class Outcome:
    ok: bool
    result: dict | None = None
    rejection: Rejection | None = None
    status: int = 200

    def body(self) -> dict:
        return {"ok": True, "result": self.result} if self.ok else self.rejection.body()


def _status(rej: Rejection, identity) -> int:
    if rej.code == "FORBIDDEN_ROLE":
        return 401 if identity is None else 403
    if rej.code == "NOT_FOUND":
        return 404
    return 409


def need(r) -> None:
    if not r.ok:
        raise _Reject(r)


def _arg(args: dict, key: str):
    if args.get(key) in (None, ""):
        raise InvalidRequest(f"missing arg '{key}'")
    return args[key]


# ---------------------------------------------------------------- permissions
def _granted(names) -> set[str]:
    out = set()
    for n in names:
        out.update(ROLE_ACTIONS.get(n, []))
    return out


def allowed_actions(identity: Identity, role: str | None = None) -> set[str]:
    """Actions this identity may perform; a human's claimed `role` narrows it to that role."""
    cfg = roles_config()
    acts = set(OPEN_ACTIONS) | _granted(cfg["agents"].get("any", []))
    if identity.is_human:
        for r in [role] if role else identity.roles:
            acts |= {a for a in cfg["approvals"].get(r, []) if a in HUMAN_ACTIONS}
            acts |= set(HUMAN_ROLE_ACTIONS.get(r, []))
        return acts
    return acts | _granted(cfg["agents"].get(identity.name, []))


def allowed_proposals(identity: Identity) -> set[str]:
    if identity.is_human:
        return set()
    out = set()
    for n in roles_config()["agents"].get(identity.name, []):
        out.update(ROLE_PROPOSALS.get(n, []))
    return out


def approver_role(type_: str) -> str | None:
    for role, types in roles_config()["approvals"].items():
        if type_ in types:
            return role
    return None


# ---------------------------------------------------------------- decisions log
def log_decision(
    eng, actor, action, code=None, rationale="", approval_id=None, kpi_before=None, kpi_after=None
) -> dict:
    row = {
        "seq": len(eng.decisions) + 1,
        "sim_min": round(eng.now, 4),
        "clock": fmt(eng.now),
        "actor": actor,
        "action": action,
        "approval_id": approval_id,
        "code": code,
        "rationale": rationale,
        "kpi_before": kpi_before,
        "kpi_after": kpi_after,
        "switch_entry_id": None,
    }
    eng.decisions.append(row)
    return row


def _kpi(eng) -> dict:
    s = eng.kpi.strip(eng)
    return {k: s[k] for k in ("airborne", "ready", "coverage_pct", "flights", "refusals")}


# ---------------------------------------------------------------- entry point
def execute(
    eng,
    identity: Identity | None,
    action: str,
    args: dict | None = None,
    role: str | None = None,
    *,
    log: bool = True,
) -> Outcome:
    args = dict(args or {})
    actor = identity.name if identity else "anonymous"
    before = _kpi(eng) if log else None
    approval_id = None
    try:
        if identity is None:
            raise _Reject(rules.forbidden_role("anonymous", action))
        if role is not None and role != identity.name and role not in identity.roles:
            raise _Reject(rules.forbidden_role(f"{actor} (as {role})", action))
        handler = HANDLERS.get(action)
        if handler is None:
            raise _Reject(rules.not_found("action", action))
        if action not in allowed_actions(identity, role if identity.is_human else None):
            raise _Reject(rules.forbidden_role(actor, action))
        result = handler(eng, identity, args) or {}
        approval_id = result.get("approval_id")
        out = Outcome(True, result)
    except _Reject as r:
        rej = r.rejection
        out = Outcome(False, rejection=rej, status=_status(rej, identity))
        if rej.code not in rules.ACCESS_CODES:
            eng.refusals.append((eng.now, rej.code, rej.reason))
            if rej.code == "PILOT_SATURATED":
                eng.kpi.pilot_saturated += 1
    if log and (action != "simulate" or not out.ok):
        log_decision(
            eng,
            actor,
            action,
            code=None if out.ok else out.rejection.code,
            rationale=str(
                args.get("why")
                or args.get("note")
                or args.get("request")
                or (out.rejection.reason if not out.ok else "")
            ),
            approval_id=approval_id or args.get("approval_id"),
            kpi_before=before,
            kpi_after=_kpi(eng),
        )
    return out


# ---------------------------------------------------------------- lookups
def _drone(eng, args):
    did = str(_arg(args, "drone")).upper()
    d = eng.drones.get(did)
    if d is None:
        raise _Reject(rules.not_found("drone", did))
    return d


def _incident(eng, iid, required=True):
    if iid is None:
        if required:
            raise InvalidRequest("missing arg 'incident'")
        return None
    inc = eng.incidents.get(iid)
    if inc is None:
        raise _Reject(rules.not_found("incident", iid))
    return inc


def _point(eng, p):
    if isinstance(p, str):
        if p in eng.world.landmarks:
            return list(eng.world.landmarks[p])
        if "," in p:
            try:
                x, y = (float(v) for v in p.split(","))
                return [x, y]
            except ValueError as e:
                raise InvalidRequest(f"bad point '{p}'") from e
        raise _Reject(rules.not_found("landmark", p))
    if isinstance(p, list | tuple) and len(p) == 2:
        return [float(p[0]), float(p[1])]
    raise InvalidRequest(f"bad point {p!r}")


def _no_fly_zones(eng) -> list[dict]:
    return eng.airspace.no_entry()


def _pilot(eng, args, d):
    p = args.get("pilot")
    if p is None:  # never silently reassigned: a saturated default is refused with the retry
        p = eng.pilots.human_remote_pilot() or list(eng.pilots)[0]
    if p not in eng.pilots:
        raise _Reject(rules.not_found("pilot", p))
    pl = eng.pilots[p]
    retry = eng.pilots.least_loaded_duty_pilot()
    need(rules.pilot_saturated(p, sorted(pl.drones), pl.max_drones, d.id, retry))
    return p


def _open_or_expired(inc_id, inc):
    need(rules.mission_expired(inc_id, inc["status"]))


def _drain_rate(eng, mission) -> float:
    rates = eng.M["drain_pct_per_min"]
    return (
        rates["patrol"]
        if mission == "patrol"
        else (rates["track"] if mission == "track" else rates["hover"])
    )


def _projected_after(eng, d, start, target, for_min, mission, launch: bool) -> float:
    M = eng.M
    cruise = M["cruise_km_per_min"]
    c = M["drain_pct_per_min"]["cruise"]
    p = d.bat
    if launch:
        p -= M["launch_delay_min"] * M["drain_pct_per_min"]["hover"]
    p -= dist(start, target) / cruise * c
    p -= float(for_min or 0) * _drain_rate(eng, mission)
    home = [k for k in eng.docks.values() if k.online and (k.free_slots() > 0 or k.id == d.slot)]
    back = min((dist(target, k.at) for k in home), default=99)
    return p - back / cruise * c


def _route_checks(eng, d, start, target):
    need(rules.no_fly_zone(d.id, start, target, _no_fly_zones(eng)))


# ---------------------------------------------------------------- handlers
def _launch_core(eng, d, args, mission, target, incident, loop=None):
    M = eng.M
    gate = M["launch_gate_pct"].get(mission, M["launch_gate_pct"]["incident"])
    dock_online = eng.docks[d.slot].usable if d.slot else False
    oos = d.out_of_service or bool(d.fault)
    need(rules.drone_not_ready(d.id, d.state, d.bat, gate, dock_online, oos))
    sectors = [sector_of(d.pos), sector_of(target)]
    need(rules.weather_grounded(d.id, sectors, eng.weather.grounded))
    need(rules.airspace_intrusion(d.id, sectors, eng.intruded()))
    _route_checks(eng, d, d.pos, target)
    need(rules.out_of_range(d.id, eng.docks[d.slot].at, target, M["radius_km"]))
    band = args.get("band")
    others = [
        (x.id, x.target, x.band)
        for x in eng.drones.values()
        if x.state == "airborne" and x.band is not None and isinstance(x.target, list)
    ]
    need(rules.separation(d.id, target, band, others, eng.world.policies["separation_km"]))
    proj = _projected_after(eng, d, d.pos, target, args.get("for_min"), mission, launch=True)
    need(rules.battery_reserve(d.id, proj, M["reserve_pct"]))
    p = _pilot(eng, args, d)
    eng.launch(d, eng.now, mission, target, loop=loop, incident=incident, pilot=p)
    d.band = band
    basis = args.get("basis", "")
    eng.emit(
        eng.now,
        "launch",
        f"🚀 {d.id} → {mission} {incident if incident is not None else ''} ({d.bat:.0f}%, "
        f"pilot {p}, basis {basis})",
        {
            "drone": d.id,
            "mission": mission,
            "incident": incident,
            "battery": round(d.bat, 1),
            "pilot": p,
            "basis": basis,
        },
    )
    return {"drone": d.id, "mission": mission, "pilot": p, "incident": incident}


def a_launch(eng, ident, args):
    d = _drone(eng, args)
    mission = _arg(args, "mission")
    if mission not in LAUNCH_MISSIONS:
        raise InvalidRequest(f"mission must be one of {LAUNCH_MISSIONS}")
    iid = args.get("incident")
    loop = None
    if iid is not None:
        _open_or_expired(iid, _incident(eng, iid))
    elif mission != "patrol":
        raise InvalidRequest("missing arg 'incident'")
    if mission == "patrol":
        loop = _arg(args, "loop")
        if loop not in eng.loops:
            raise _Reject(rules.not_found("loop", loop))
        target = list(eng.loops[loop]["center"])
    else:
        target = _point(eng, _arg(args, "to"))
    return _launch_core(eng, d, args, mission, target, iid, loop)


def _airborne(d):
    if d.state != "airborne":
        need(rules.drone_not_airborne(d.id, d.state))


def a_divert(eng, ident, args):
    d = _drone(eng, args)
    iid = _arg(args, "incident")
    mission = args.get("mission", "track")
    inc = _incident(eng, iid)
    _airborne(d)
    cur = eng.incidents.get(d.incident) if d.incident else None
    if cur and cur["status"] == "open" and d.incident != iid:
        a, used = eng.approvals.consume("divert_off_incident", drone=d.id)
        need(rules.auth_required("divert_off_incident", a is not None, d.id, used))
    _open_or_expired(iid, inc)
    if mission == "track":
        _track_auth_checks(eng, iid)
    p = args.get("pilot")
    if p is not None:
        p = _pilot(eng, args, d)
    old = d.loop
    if mission == "track":
        d.mission, d.incident, d.loop, d.on_station = "track", iid, None, False
    else:
        target = _point(eng, args["to"]) if args.get("to") else list(eng.world.point(inc["at"]))
        _route_checks(eng, d, d.pos, target)
        d.mission, d.incident, d.loop, d.on_station, d.target = mission, iid, None, False, target
    if p is not None:
        eng.pilots.assign(d, p)
    if mission == "track":
        eng.tracker_id = d.id
        text = f"🎯 {d.id} diverted from {old} patrol to TRACK {iid} at {d.bat:.0f}%"
    else:
        text = f"🎯 {d.id} diverted to {mission} {iid} at {d.bat:.0f}%"
    eng.emit(
        eng.now,
        "divert",
        text,
        {
            "drone": d.id,
            "from_loop": old,
            "mission": mission,
            "incident": iid,
            "battery": round(d.bat, 1),
            "pilot": args.get("pilot"),
        },
    )
    return {"drone": d.id, "mission": mission, "incident": iid}


def _track_auth_checks(eng, iid):
    au = eng.approvals.auth(iid, "track")
    need(rules.auth_required("track", au is not None, iid))
    need(rules.mission_expired(iid, "open", auth_expired=au.status != "active"))


def a_assign_track(eng, ident, args):
    d = _drone(eng, args)
    iid = args.get("incident")
    need(rules.privacy_track_needs_incident(iid))
    inc = _incident(eng, iid)
    _open_or_expired(iid, inc)
    _track_auth_checks(eng, iid)
    _airborne(d)
    old = eng.tracker_id
    d.mission, d.incident = "track", iid
    eng.tracker_id = d.id
    if old and old != d.id and eng.drones[old].mission == "hold":
        sp = [round(x, 2) for x in eng.suspect.pos(eng.now)]  # drone has it in view now
        eng.emit(
            eng.now,
            "reacquire",
            f"🎯 {d.id} reacquires suspect at {sp} (battery {d.bat:.0f}%); "
            f"{old} keeps boundary hold",
            {"drone": d.id, "previous": old, "battery": round(d.bat, 1)},
        )
    else:
        eng.emit(
            eng.now,
            "track_assigned",
            f"🎯 {d.id} assigned to TRACK {iid} (battery {d.bat:.0f}%)",
            {"drone": d.id, "previous": old, "incident": iid, "battery": round(d.bat, 1)},
        )
    return {"drone": d.id, "incident": iid, "previous": old}


def _retask(eng, d, args, mission, iid, target):
    if d.state == "docked":
        return _launch_core(eng, d, args, mission, target, iid)
    _airborne(d)
    _route_checks(eng, d, d.pos, target)
    d.mission, d.incident, d.loop, d.on_station, d.target = mission, iid, None, False, target
    eng.emit(
        eng.now,
        mission,
        f"🧭 {d.id} {mission} {iid} → {[round(v, 2) for v in target]}",
        {"drone": d.id, "mission": mission, "incident": iid, "to": target},
    )
    return {"drone": d.id, "mission": mission, "incident": iid}


def a_assign_perimeter(eng, ident, args):
    d = _drone(eng, args)
    iid = _arg(args, "incident")
    inc = _incident(eng, iid)
    _open_or_expired(iid, inc)
    target = _point(eng, args["to"]) if args.get("to") else list(eng.world.point(inc["at"]))
    return _retask(eng, d, args, "perimeter", iid, target)


def a_corridor_watch(eng, ident, args):
    d = _drone(eng, args)
    iid = _arg(args, "incident")
    inc = _incident(eng, iid)
    _open_or_expired(iid, inc)
    target = _point(eng, _arg(args, "to"))
    return _retask(eng, d, args, "corridor", iid, target)


def a_hold(eng, ident, args):
    d = _drone(eng, args)
    zid = _arg(args, "at_boundary")
    zone = next((z for z in eng.airspace.zones if z["id"] == zid), None)
    if zone is None:
        raise _Reject(rules.not_found("zone", zid))
    _airborne(d)
    d.mission = "hold"
    d.target = eng.airspace.boundary_point(d.pos, zone["rect"])
    eng.emit(
        eng.now,
        "hold",
        f"⛔ {d.id} holds at {zid} boundary (battery {d.bat:.0f}%)",
        {"drone": d.id, "zone": zid, "code": "NO_FLY_ZONE", "battery": round(d.bat, 1)},
    )
    return {"drone": d.id, "zone": zid, "at": [round(v, 3) for v in d.target]}


def _forced_dock(eng, d, dock_id, why):
    k = eng.docks.get(dock_id)
    if k is None:
        raise _Reject(rules.not_found("dock", dock_id))
    already = d.mission == "returning" and d.target == dock_id
    if not already:
        need(rules.dock_available(dock_id, k.online, k.free_slots()))
        if d.mission == "returning" and d.target in eng.docks:
            eng.docks[d.target].reserved.remove(d.id)
        k.reserved.append(d.id)
    d.mission, d.target, d.loop, d.on_station, d.manual = "returning", dock_id, None, False, False
    eng.emit(
        eng.now,
        "rtb",
        f"↩ {d.id} RTB {why} → {dock_id} (battery {d.bat:.0f}%)",
        {"drone": d.id, "dock": dock_id, "why": why, "battery": round(d.bat, 1)},
    )


def a_rtb(eng, ident, args):
    d = _drone(eng, args)
    _airborne(d)
    why = args.get("why") or args.get("note") or "requested"
    if args.get("dock"):
        _forced_dock(eng, d, args["dock"], why)
    elif d.mission == "returning":
        return {"drone": d.id, "dock": d.target, "already_returning": True, **eng.landing(d)}
    else:
        eng.rtb(d, eng.now, why)
    if eng.tracker_id == d.id:
        eng.tracker_id = None
    return {"drone": d.id, "dock": d.target, **eng.landing(d)}


def a_dock_at(eng, ident, args):
    _arg(args, "dock")
    return a_rtb(eng, ident, args)


def _human_pilot(eng, ident):
    name = ident.pilot or ident.name
    if name not in eng.pilots:
        raise _Reject(rules.forbidden_role(ident.name, "take_control (not a pilot in world)"))
    return eng.pilots[name]


def a_take_control(eng, ident, args):
    d = _drone(eng, args)
    pl = _human_pilot(eng, ident)
    _airborne(d)
    need(rules.pilot_saturated(pl.id, sorted(pl.drones), pl.max_drones, d.id))
    eng.pilots.assign(d, pl.id)
    d.manual = True
    load = f"{len(pl.drones)}/{pl.max_drones}"
    eng.emit(
        eng.now,
        "take_control",
        f"🕹 {pl.id} has manual control of {d.id} (pilot load {load})",
        {"drone": d.id, "pilot": pl.id},
    )
    return {"drone": d.id, "pilot": pl.id, "load": load}


def a_release_control(eng, ident, args):
    d = _drone(eng, args)
    pl = _human_pilot(eng, ident)
    d.manual = False
    eng.emit(
        eng.now,
        "release_control",
        f"🕹 {pl.id} released manual control of {d.id}",
        {"drone": d.id, "pilot": pl.id},
    )
    return {"drone": d.id, "pilot": d.pilot}


def a_broadcast(eng, ident, args):
    d = _drone(eng, args)
    text = _arg(args, "text")
    a, used = eng.approvals.consume("broadcast", drone=d.id)
    need(rules.auth_required("broadcast", a is not None, d.id, used))
    eng.emit(
        eng.now, "broadcast", f"📣 {d.id} loudspeaker: '{text}'", {"drone": d.id, "text": text}
    )
    return {"drone": d.id}


def a_request_footage_release(eng, ident, args):
    iid = _arg(args, "incident")
    _incident(eng, iid)
    a = eng.approvals.propose(
        eng.now,
        ident.name,
        "footage_release",
        {"incident": iid, "clip": _arg(args, "clip"), "to": _arg(args, "to")},
        args.get("summary", f"release {args['clip']} of {iid} to {args['to']}"),
    )
    return {"approval_id": a.id, "status": a.status}


def a_release_footage(eng, ident, args):
    aid = _arg(args, "approval_id")
    a = eng.approvals.items.get(aid)
    if a is None or a.type != "footage_release":
        raise _Reject(rules.not_found("footage approval", aid))
    if a.status == "approved" and a.used:
        need(rules.auth_required("footage_release", False, aid, used=aid))
    need(rules.auth_required("footage_release", a.status == "approved", aid))
    a.used = True
    eng.privacy.append(
        eng.now,
        None,
        a.args.get("incident"),
        "footage_released",
        f"clip {a.args.get('clip')} to {a.args.get('to')} ({aid})",
        ident.name,
    )
    return {"approval_id": aid, "released": True}


def a_set_recording(eng, ident, args):
    d = _drone(eng, args)
    on = args.get("on")
    if not isinstance(on, bool):
        raise InvalidRequest("arg 'on' must be true or false")
    if on:
        nr = eng.airspace.in_zone(d.pos, "no_record") if d.state == "airborne" else None
        inside = d.state == "airborne" and privacy.in_geofence(eng, d)
        need(rules.privacy_recording(d.id, inside, nr))
        d.recording_off = False
    else:
        d.recording_off = True
    privacy.update(eng, eng.now)
    return {"drone": d.id, "recording": d.recording}


def a_request_face_id(eng, ident, args):
    req = _arg(args, "request")
    detail = f"'{req}' (PRIVACY_POLICY)"
    if args.get("by"):
        detail += f"; requested_by: {args['by']}"
    eng.privacy.append(
        eng.now, args.get("drone"), args.get("incident"), "face_id_refused", detail, ident.name
    )
    raise _Reject(rules.privacy_face_id(req))


FACE_WORDS = ("face", "facial", "identify", "identification", "recogni")


def a_privacy_check(eng, ident, args):
    req = str(_arg(args, "request"))
    if any(w in req.lower() for w in FACE_WORDS):
        return a_request_face_id(eng, ident, {**args, "request": req})
    return {"request": req, "classification": "lawful within an open incident geofence"}


def a_activate_zone(eng, ident, args):
    zid = _arg(args, "zone")
    zone = next((z for z in eng.airspace.zones if z["id"] == zid), None)
    if zone is None:
        raise _Reject(rules.not_found("zone", zid))
    ok = bool(eng.approvals.approved("zone", zone=zid))
    need(rules.auth_required("zone", ok, zid))
    zone["active"] = True
    if args.get("until"):
        zone["until"] = args["until"]
    eng.refresh_zones(eng.now)
    return {"zone": zid, "active": True, "until": zone.get("until")}


def a_open_incident(eng, ident, args):
    src = args.get("from_alert")
    if src and src in eng.incidents:
        return {"incident": src, "status": eng.incidents[src]["status"]}
    if src:
        raise _Reject(rules.not_found("alert", src))
    kind, at = _arg(args, "kind"), _point(eng, _arg(args, "at"))
    iid = f"INC-{len(eng.incidents) + 1}"
    while iid in eng.incidents:
        iid = f"INC-{int(iid[4:]) + 1}"
    eng.incidents[iid] = {
        "incident": iid,
        "kind": kind,
        "at": at,
        "note": args.get("note"),
        "t": eng.now,
        "open": True,
        "status": "open",
    }
    eng.emit(
        eng.now,
        "incident_opened",
        f"🚨 {iid} {kind} at {at}",
        {"incident": iid, "kind": kind, "at": at, "by": ident.name},
    )
    return {"incident": iid, "status": "open"}


def a_close_incident(eng, ident, args):
    iid = _arg(args, "incident")
    inc = _incident(eng, iid)
    _open_or_expired(iid, inc)
    note = args.get("note", "")
    inc["status"], inc["open"], inc["closed_t"] = "closed", False, eng.now
    eng.emit(
        eng.now, "incident_closed", f"🚓 {iid} closed: {note}", {"incident": iid, "note": note}
    )
    for uid in eng.ground.stand_down(eng.now, iid):
        eng.emit(
            eng.now,
            "unit_stood_down",
            f"🚓 {uid} stood down: {iid} closed",
            {"unit": uid, "incident": iid},
            timeline=False,
        )
    return {"incident": iid, "status": "closed"}


def _unit(eng, args):
    uid = str(_arg(args, "unit"))
    if uid not in eng.ground:
        raise _Reject(rules.not_found("unit", uid))
    return eng.ground.units[uid]


def a_task_unit(eng, ident, args):
    u = _unit(eng, args)
    iid = _arg(args, "incident")
    inc = _incident(eng, iid)
    _open_or_expired(iid, inc)
    scene = _point(eng, args["to"]) if args.get("to") else None
    eng.task_unit(u.id, iid, scene)
    eta = u.arrive_t - eng.now
    return {
        "unit": u.id,
        "incident": iid,
        "status": u.status,
        "eta_min": round(eta, 2),
        "eta": mmss(eta),
        "simulated": True,
    }


def _committed(eng, u, iid):
    if u.status not in ("en_route", "on_scene") or not u.incident or (iid and iid != u.incident):
        raise _Reject(rules.not_found("unit tasking", f"{u.id} on {iid or 'an incident'}"))
    return u.incident


def a_release_unit(eng, ident, args):
    """Early release: only while the unit is still committed (en route or on scene before its
    on-scene timer ends), and the evidence must quote a revealed feed fact of that incident."""
    u = _unit(eng, args)
    ev = args.get("evidence") or _arg(args, "note")
    iid = _committed(eng, u, args.get("incident"))
    _incident(eng, iid)
    quoted = incidents.evidence_matches(ev, incidents.revealed(eng, iid))
    need(rules.release_evidence(u.id, iid, quoted))
    saved = eng.ground.release(eng.now, u.id)
    eng.kpi.released_early += 1
    eng.emit(
        eng.now,
        "unit_released",
        f"✅ {u.id} released early ({args.get('note') or ev})",
        {"unit": u.id, "incident": iid, "evidence": ev, "saves_min": round(saved, 1)},
    )
    return {"unit": u.id, "incident": iid, "released": True, "saves_min": round(saved, 1)}


def a_stand_down(eng, ident, args):
    """Send a unit home without the early-release credit (no evidence needed)."""
    u = _unit(eng, args)
    iid = _committed(eng, u, args.get("incident"))
    eng.ground._return(u, eng.now)
    eng.emit(
        eng.now,
        "unit_stood_down",
        f"🚓 {u.id} stood down from {iid}",
        {"unit": u.id, "incident": iid},
        timeline=False,
    )
    return {"unit": u.id, "incident": iid, "status": u.status}


def _minimal(kind, required):
    def h(eng, ident, args):
        for k in required:
            _arg(args, k)
        if args.get("incident"):
            _incident(eng, args["incident"])
        payload = {k: v for k, v in args.items() if isinstance(v, str | int | float | bool)}
        eng.emit(eng.now, kind, f"{kind.replace('_', ' ')}: {payload}", payload, timeline=False)
        return {kind: True, **payload}

    return h


def a_log_refusal(eng, ident, args):
    code = _arg(args, "code")
    if code not in rules.CODES:
        raise InvalidRequest(f"code must be one of {rules.CODES}")
    about = _arg(args, "about")
    if code == "PRIVACY_POLICY":
        detail = about + (f"; requested_by: {args['by']}" if args.get("by") else "")
        eng.privacy.append(
            eng.now, None, args.get("incident"), "refusal_logged", detail, ident.name
        )
    _refusal_line(eng, code, about, args)
    return {"logged": True, "code": code, "about": about, "requested_by": args.get("by")}


def _refusal_line(eng, code: str, about: str, args: dict) -> None:
    """The timeline line of an agent-logged refusal (same text as the golden run's)."""
    p, fallback = args.get("pilot"), args.get("fallback_pilot")
    if code == "PILOT_SATURATED" and p in eng.pilots and fallback:
        eng.emit(
            eng.now,
            "rejected",
            f"❌ REJECTED {args.get('drone')} under {p}: {code} "
            f"({sorted(eng.pilots[p].drones)}) → {fallback}",
            {"drone": args.get("drone"), "pilot": p, "code": code, "fallback_pilot": fallback},
        )
    else:
        eng.emit(
            eng.now,
            "refusal",
            f"⛔ {code}: '{about}'",
            {"code": code, "request": about, "by": args.get("by")},
        )


def a_rotate_patrol(eng, ident, args):
    plan = {
        L: [
            d.id
            for d in eng.drones.values()
            if d.state == "airborne" and d.loop == L and d.mission == "patrol"
        ]
        for L in eng.loops
    }
    return {"note": "rotation is sim policy (automatic)", "loops": plan}


def a_simulate(eng, ident, args):
    inner = _arg(args, "action")
    if inner == "simulate":
        raise InvalidRequest("cannot simulate simulate")
    if inner in ("approve", "reject"):  # human decisions are never simulated
        raise _Reject(rules.forbidden_role(ident.name, f"simulate {inner}"))
    if inner == "propose":  # `airgrid simulate propose ...`: validated on a copy, nothing queued
        shadow = copy.deepcopy(eng, {id(eng.db): None})
        shadow.db = None
        p = args.get("args") or {}
        out = propose(shadow, ident, _arg(p, "type"), p.get("args"), p.get("summary", ""))
        return {"would": out.body(), "kpi_delta": {k: 0 for k in _kpi(eng)}}
    if inner not in HANDLERS:
        raise _Reject(rules.not_found("action", inner))
    if inner == "request_face_id":  # a face-ID request is refused and logged for real, always
        out = execute(eng, ident, inner, args.get("args") or {})
        return {"would": out.body(), "kpi_delta": {k: 0 for k in _kpi(eng)}, "logged": True}
    shadow = copy.deepcopy(eng, {id(eng.db): None})
    shadow.db = None
    before = _kpi(shadow)
    out = execute(shadow, ident, inner, args.get("args") or {}, log=False)
    after = _kpi(shadow)
    delta = {
        k: (after[k] - before[k]) if isinstance(after[k], int | float) else after[k] for k in after
    }
    return {"would": out.body(), "kpi_delta": delta}


HANDLERS = {
    "launch": a_launch,
    "divert": a_divert,
    "assign_track": a_assign_track,
    "assign_perimeter": a_assign_perimeter,
    "corridor_watch": a_corridor_watch,
    "hold": a_hold,
    "rtb": a_rtb,
    "dock_at": a_dock_at,
    "take_control": a_take_control,
    "release_control": a_release_control,
    "broadcast": a_broadcast,
    "request_footage_release": a_request_footage_release,
    "release_footage": a_release_footage,
    "set_recording": a_set_recording,
    "request_face_id": a_request_face_id,
    "privacy_check": a_privacy_check,
    "activate_zone": a_activate_zone,
    "open_incident": a_open_incident,
    "close_incident": a_close_incident,
    "issue_advisory": _minimal("advisory", ["area", "text"]),
    "task_unit": a_task_unit,
    "release_unit": a_release_unit,
    "retime_signal": _minimal("signal_retimed", ["junction"]),
    "stand_down": a_stand_down,
    "rotate_patrol": a_rotate_patrol,
    "evidence_tag": _minimal("evidence_tag", ["incident", "what"]),
    "log_refusal": a_log_refusal,
    "simulate": a_simulate,
}


# ---------------------------------------------------------------- approvals
def propose(
    eng,
    identity: Identity | None,
    type_: str,
    args: dict | None = None,
    summary: str = "",
    hold_clock: bool | None = None,
) -> Outcome:
    args = dict(args or {})
    actor = identity.name if identity else "anonymous"
    try:
        if identity is None:
            raise _Reject(rules.forbidden_role("anonymous", f"propose {type_}"))
        if type_ not in TYPES:
            raise InvalidRequest(f"type must be one of {TYPES}")
        if type_ not in allowed_proposals(identity):
            raise _Reject(rules.forbidden_role(actor, f"propose {type_}"))
        if type_ == "track":
            iid = _arg(args, "incident")
            _open_or_expired(iid, _incident(eng, iid))
            if track_minutes(args) is None:
                raise InvalidRequest("track needs args.minutes (or 'N min' in scope)")
        elif type_ in ("broadcast", "divert_off_incident", "out_of_service"):
            _drone(eng, args)
        elif type_ == "zone":
            zid = _arg(args, "zone")
            if not any(z["id"] == zid for z in eng.airspace.zones):
                raise _Reject(rules.not_found("zone", zid))
        elif type_ == "footage_release":
            _incident(eng, _arg(args, "incident"))
        a = eng.approvals.propose(eng.now, actor, type_, args, summary, hold_clock)
        out = Outcome(True, {"id": a.id, "status": a.status})
    except _Reject as r:
        out = Outcome(False, rejection=r.rejection, status=_status(r.rejection, identity))
    log_decision(
        eng,
        actor,
        f"propose {type_}",
        code=None if out.ok else out.rejection.code,
        rationale=summary,
        approval_id=out.result["id"] if out.ok else None,
    )
    return out


def decide(
    eng, identity: Identity | None, approval_id: str, approve: bool, note: str = ""
) -> Outcome:
    actor = identity.name if identity else "anonymous"
    verb = "approve" if approve else "reject"
    before = _kpi(eng)
    try:
        if identity is None:
            raise _Reject(rules.forbidden_role("anonymous", verb))
        if not identity.is_human:
            raise _Reject(rules.forbidden_role(actor, f"{verb} (human token only)"))
        a = eng.approvals.items.get(approval_id)
        if a is None:
            raise _Reject(rules.not_found("approval", approval_id))
        role = approver_role(a.type)
        if role not in identity.roles:
            raise _Reject(rules.forbidden_role(actor, f"{verb} {a.type} (needs {role})"))
        if a.status != "pending":
            raise _Reject(rules.decided_already(a.id, a.status))
        a.status = "approved" if approve else "rejected"
        a.decided_by, a.decided_min, a.reason = actor, eng.now, note or None
        if approve:
            _apply_approval(eng, a, actor)
        elif a.type == "out_of_service" and a.args.get("drone") in eng.drones:
            eng.drones[a.args["drone"]].fault = None  # back in service
        out = Outcome(True, {"id": a.id, "status": a.status, "type": a.type})
    except _Reject as r:
        out = Outcome(False, rejection=r.rejection, status=_status(r.rejection, identity))
    log_decision(
        eng,
        actor,
        verb,
        code=None if out.ok else out.rejection.code,
        rationale=note,
        approval_id=approval_id,
        kpi_before=before,
        kpi_after=_kpi(eng),
    )
    return out


def _apply_approval(eng, a, by: str) -> None:
    t = eng.now
    if a.type == "track":
        eng.approvals.add_auth(
            a, t, by, a.args["incident"], "track", a.args.get("scope"), t + track_minutes(a.args)
        )
    elif a.type == "zone":
        zone = next(z for z in eng.airspace.zones if z["id"] == a.args["zone"])
        zone["active"] = True
        zone["until"] = a.args.get("until")
        until = zone_until(a.args)
        eng.approvals.add_auth(a, t, by, None, "zone", a.args["zone"], until)
        eng.refresh_zones(t)
    elif a.type == "standing_policy":
        eng.standing_policy = True
    elif a.type == "out_of_service":
        d = eng.drones[a.args["drone"]]
        d.out_of_service = True
        eng.rtb(d, t, "out of service")
    elif a.type in ("footage_release", "divert_off_incident", "broadcast"):
        eng.approvals.add_auth(a, t, by, a.args.get("incident"), a.type, a.args.get("scope"), None)
    if a.type not in SILENT:
        what = WHAT.get(a.type, a.type)
        scope = a.args.get("scope")
        eng.emit(
            t,
            "approved",
            f"🔐 {what} approved by {by}" + (f" — scope: {scope}" if scope else ""),
            {
                "what": what,
                "by": by,
                "scope": scope,
                "incident": a.args.get("incident"),
                "drone": a.args.get("drone"),
            },
        )
