"""Read-only projections for `airgrid plan rotation`, `route check`, `track status`, `relay plan`.

Present state only: nothing here reads the suspect path, the scenario queue or any other future
data, and nothing writes. A projection is what the sim would do if asked now, not a promise: the
real relay picks its relief at trigger time.
"""

from __future__ import annotations

from sim import actions, rules
from sim.geo import dist, fmt, mmss, sector_of
from sim.rules import segment_hits_rect


def _next_ready(eng, near, gate: float) -> str | None:
    cands = [d for d in eng.ready_candidates(near, gate) if eng.floor_allows(d)]
    return cands[0].id if cands else None


def rotation_plan(eng, loop: str | None = None):
    if loop is not None and loop not in eng.loops:
        return rules.not_found("loop", loop)
    M = eng.M
    rate, trigger = M["drain_pct_per_min"]["patrol"], M["relief_trigger_pct"]
    plans = []
    for name in [loop] if loop else eng.loops:
        here = [
            d
            for d in eng.drones.values()
            if d.state == "airborne" and d.loop == name and d.mission == "patrol"
        ]
        on = sorted((d for d in here if d.on_station), key=lambda d: (d.bat, d.id))
        tired = on[0] if on else None
        center = eng.loops[name]["center"]
        plans.append(
            {
                "loop": name,
                "on_station": [d.id for d in on],
                "tightest_battery": round(tired.bat, 1) if tired else None,
                "relief_at_pct": trigger,
                "minutes_to_relief": (
                    round(max(0.0, (tired.bat - trigger) / rate), 1) if tired else 0.0
                ),
                "relief_inbound": eng.patrol_relief.get(name),
                "next_relief": _next_ready(eng, center, M["launch_gate_pct"]["patrol"]),
                "grounded": eng.loop_grounded(name),
            }
        )
    return {"plans": plans}


def route_check(eng, drone: str, to: str):
    d = eng.drones.get(drone.upper())
    if d is None:
        return rules.not_found("drone", drone)
    try:
        target = eng.docks[to].at if to in eng.docks else actions._point(eng, to)
    except actions._Reject as r:
        return r.rejection
    denied = rules.no_fly_zone(d.id, d.pos, target, eng.airspace.no_entry())
    if not denied.ok:
        return denied
    crossed = [
        z
        for z in eng.airspace.zones
        if z.get("active", True) and segment_hits_rect(d.pos, target, z["rect"])
    ]
    no_record = [z["id"] for z in crossed if z["kind"] == "no_record"]
    wait = eng.M["launch_delay_min"] if d.state == "docked" else d.launch_wait
    eta = wait + dist(d.pos, target) / eng.M["cruise_km_per_min"]
    sectors = {sector_of(d.pos), sector_of(target)}
    return {
        "drone": d.id,
        "from": [round(v, 3) for v in d.pos],
        "to": [round(v, 3) for v in target],
        "zones": [{"id": z["id"], "kind": z["kind"]} for z in crossed],
        "recording": "off in " + ", ".join(no_record) if no_record else "unchanged",
        "grounded_sectors": sorted(sectors & eng.weather.grounded),
        "eta_min": round(eta, 2),
        "eta": mmss(eta),
    }


def _track_base(eng, iid: str):
    inc = eng.incidents.get(iid)
    if inc is None:
        return None, None, rules.not_found("incident", iid)
    tracker = eng.drones.get(eng.tracker_id) if eng.tracker_id else None
    if tracker is not None and tracker.incident != iid:
        tracker = None
    M = eng.M
    trigger = M["track_relief_trigger_pct"]
    au = eng.approvals.auth(iid, "track")
    minutes = None
    if tracker:
        minutes = round(max(0.0, (tracker.bat - trigger) / M["drain_pct_per_min"]["track"]), 1)
    base = {
        "incident": iid,
        "incident_status": inc["status"],
        "tracker": tracker.id if tracker else None,
        "battery": round(tracker.bat, 1) if tracker else None,
        "mission": tracker.mission if tracker else None,
        "authorisation": (
            {
                "id": au.id,
                "approval_id": au.approval_id,
                "status": au.status,
                "expires": fmt(au.expires_min) if au.expires_min is not None else None,
            }
            if au
            else None
        ),
        "relief_at_pct": trigger,
        "minutes_to_relief": minutes,
    }
    return base, tracker, None


def _sprint_eta(eng, relief, tracker) -> float:
    wait = eng.M["launch_delay_min"] if relief.state == "docked" else relief.launch_wait
    return wait + dist(relief.pos, tracker.pos) / eng.M["sprint_km_per_min"]


def track_status(eng, iid: str):
    base, tracker, rej = _track_base(eng, iid)
    if rej:
        return rej
    relief = eng.drones.get(eng.relief_pending_id) if eng.relief_pending_id else None
    eta = _sprint_eta(eng, relief, tracker) if relief and tracker else None
    return {
        **base,
        "next_relief": relief.id if relief else None,
        "handoff_eta_min": round(eta, 2) if eta is not None else None,
    }


def relay_plan(eng, iid: str):
    base, tracker, rej = _track_base(eng, iid)
    if rej:
        return rej
    if tracker is None:
        return {**base, "relief_candidate": None, "handoff_eta_min": None}
    pid = eng.relief_pending_id
    cid = pid or _next_ready(eng, tracker.pos, eng.M["launch_gate_pct"]["track"])
    cand = eng.drones.get(cid) if cid else None
    return {
        **base,
        "relief_candidate": cid,
        "relief_launched": bool(pid),
        "handoff_eta_min": round(_sprint_eta(eng, cand, tracker), 2) if cand else None,
        "basis": "present positions; the sim picks the relief when the trigger is reached",
    }
