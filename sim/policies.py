"""Auto policies (sim code, never an agent; L9): auto-launch, patrol rotation + backfill,
track relay, safety RTB, weather grounding RTB. Slot booking lives in Engine.rtb / docks.book_slot.

Every function mirrors the matching block of tools/fleet_check.py, including op order and
tie-breaks, so the golden run reproduces docs/golden-timeline.txt.
"""

from sim import rules
from sim.geo import EPS, dist, sector_of

EYES_MISSIONS = ("incident", "search")  # missions that count as eyes-on (L34)


# ---- 1. auto-launch (L29a) ----
def on_cad_alert(eng, t: float, inc: dict) -> None:
    """Schedule an auto-launch after the triage delay, or raise an approval card."""
    if inc.get("city"):  # city mode diverts a roaming drone itself
        return
    if inc["kind"] in eng.world.policies["auto_launch_kinds"] or inc.get("auto_launch"):
        due = round(t + eng.world.policies["auto_launch_triage_min"], 4)
        eng.pending_auto.append((due, inc["incident"]))
        return
    what = "track" if inc["kind"] == "robbery" else "launch"
    eng.emit(
        t,
        "approval_needed",
        f"approval needed: {what} for {inc['incident']} ({inc['kind']}), watch_commander",
        {
            "incident": inc["incident"],
            "kind": inc["kind"],
            "what": what,
            "code": "AUTH_REQUIRED",
            "needs": "watch_commander",
        },
        timeline=False,
    )


def auto_launch_due(eng, t: float) -> None:
    for due, iid in list(eng.pending_auto):
        if due > t + EPS:
            continue
        inc = eng.incidents.get(iid)
        if not inc or inc.get("status") != "open":
            eng.pending_auto.remove((due, iid))
            continue
        if not eng.standing_policy:  # stays queued; fires the tick after approval
            if iid in eng.auto_waiting:
                continue
            eng.auto_waiting.add(iid)
            eng.emit(
                t,
                "approval_needed",
                f"approval needed: standing auto-launch policy for {iid}, watch_commander",
                {
                    "incident": iid,
                    "what": "standing_policy",
                    "code": "AUTH_REQUIRED",
                    "needs": "watch_commander",
                },
                timeline=False,
            )
            continue
        target = eng.world.point(inc["at"])
        if launch_held(eng, t, iid, [aim for _m, aim in _plan(eng, inc, target)]):
            continue  # SEPARATION hold: stays queued, retried once it lifts
        eng.pending_auto.remove((due, iid))
        eng.auto_waiting.discard(iid)
        if any(
            d.state == "airborne" and d.incident == iid and d.mission in EYES_MISSIONS
            for d in eng.drones.values()
        ):
            continue  # an eyes-on drone already covers it (perimeter/corridor drones don't)
        _auto_launch(eng, t, iid, inc, target)


def launch_held(eng, t: float, what: str, targets: list) -> bool:
    """A policy launch toward a sector under a SEPARATION hold waits. Logged once per hold."""
    held = eng.intruded()
    hit = sorted({sector_of(p) for p in targets} & set(held))
    if hit and what not in eng.held_launches:
        eng.held_launches.add(what)
        eng.emit(
            t,
            "launch_held",
            f"policy launch for {what} held: SEPARATION hold in {hit} until {held[hit[0]]}",
            {"for": what, "sectors": hit, "until": held[hit[0]], "code": "SEPARATION"},
            timeline=False,
        )
    return bool(hit)


def _plan(eng, inc: dict, target) -> list:
    """[(mission, aim)]: one incident drone, or a search pair either side of the point."""
    if inc.get("search_pair"):
        off = eng.world.policies["search_pair_offset_km"]
        return [("search", [target[0] - off, target[1]]), ("search", [target[0] + off, target[1]])]
    return [("incident", target)]


def _auto_launch(eng, t: float, iid: str, inc: dict, target) -> None:
    """Launch the plan's drones. A route into a no-entry zone is never auto-flown."""
    thermal_ok = inc["kind"] == "fire" or inc.get("thermal_preferred")
    gate = eng.M["launch_gate_pct"]["incident"]
    for mission, aim in _plan(eng, inc, target):
        ready = [
            d
            for d in eng.ready_candidates(aim, gate, avoid_thermal=not thermal_ok)
            if eng.floor_allows(d)
        ]
        zones = eng.airspace.no_entry()
        cands = [d for d in ready if rules.no_fly_zone(d.id, d.pos, aim, zones).ok]
        pilot = eng.pilots.first_human_remote_pilot() or eng.pilots.pilot_for([])
        if not cands or not pilot:
            if not ready:
                why, code = "no drone", "DRONE_NOT_READY"
            elif not cands:
                why, code = "route blocked", "NO_FLY_ZONE"
            else:
                why, code = "no pilot", "PILOT_SATURATED"
            eng.emit(
                t,
                "auto_launch_unavailable",
                f"auto-launch for {iid} unavailable ({why})",
                {"incident": iid, "code": code},
                timeline=False,
            )
            return
        d = cands[0]
        eng.launch(d, t, mission, list(aim), incident=iid, pilot=pilot)
        eng.policy_launches.setdefault(iid, []).append(
            {"t": t, "drone": d.id, "pilot": pilot, "asserted": False}
        )
        eng.emit(
            t,
            "launch",
            f"🚀 {d.id} → {mission} {iid} ({d.bat:.0f}%, pilot {pilot}, basis auto_policy)",
            {
                "drone": d.id,
                "mission": mission,
                "incident": iid,
                "battery": round(d.bat, 1),
                "pilot": pilot,
                "basis": "auto_policy",
            },
        )


# ---- 2/3/8. patrol rotation, backfill, weather grounding RTB ----
def patrol_rotation(eng, t: float) -> None:
    M = eng.M
    for L in eng.loops:
        inc = [
            d
            for d in eng.drones.values()
            if d.state == "airborne" and d.loop == L and d.mission == "patrol"
        ]
        if eng.loop_grounded(L):
            for d in inc:
                eng.rtb(d, t, "WEATHER_GROUNDED")
            continue
        onst = [d for d in inc if d.on_station]
        pend = eng.patrol_relief.get(L)
        if pend and (eng.drones[pend].state != "airborne" or eng.drones[pend].loop != L):
            eng.patrol_relief[L] = pend = None
        need = (not onst) or (onst and min(x.bat for x in onst) <= M["relief_trigger_pct"])
        if need and not pend and not [d for d in inc if not d.on_station]:
            if launch_held(eng, t, f"{L} patrol", [eng.loops[L]["center"]]):
                continue  # retried every tick; launches as soon as the hold lifts
            cands = [
                d
                for d in eng.ready_candidates(
                    eng.loops[L]["center"], M["launch_gate_pct"]["patrol"]
                )
                if eng.floor_allows(d)
            ]
            if cands:
                r = cands[0]
                prefer = eng.loops[L].get("pilot_prefer", [])
                p = eng.pilot_for(prefer, t, f"{L} patrol")
                if p is None:
                    continue
                eng.launch(r, t, "patrol", eng.loops[L]["center"], loop=L, pilot=p)
                eng.patrol_relief[L] = r.id
                if onst:
                    why = "relief for " + onst[0].id + f" at {onst[0].bat:.0f}%"
                else:
                    why = "backfill — loop uncovered"
                eng.emit(
                    t,
                    "rotation",
                    f"🔄 {r.id} launches for {L} ({why})",
                    {
                        "drone": r.id,
                        "loop": L,
                        "pilot": p,
                        "relieves": onst[0].id if onst else None,
                        "battery": round(r.bat, 1),
                    },
                )
            elif not onst:
                gate = M["launch_gate_pct"]["patrol"]
                eng.emit(
                    t,
                    "loop_uncovered",
                    f"⚠ {L} uncovered: no READY drone ≥{gate}%",
                    {"loop": L},
                )


# ---- 5. track relay ----
def track_relay(eng, t: float) -> None:
    if not eng.tracker_id:
        return
    tr = eng.drones[eng.tracker_id]
    if (
        tr.mission == "track"
        and tr.bat <= eng.M["track_relief_trigger_pct"]
        and not eng.relief_pending_id
    ):
        ahead = eng.suspect.pos(t + eng.world.policies["relay_lookahead_min"])  # never served
        cands = [
            d
            for d in eng.ready_candidates(ahead, eng.M["launch_gate_pct"]["track"])
            if eng.floor_allows(d)
        ]
        if cands:
            r = cands[0]
            pilot = eng.pilots.first_human_remote_pilot() or eng.pilot_for([], t, "track relay")
            if pilot is None:
                return
            eng.launch(r, t, "relay", None, incident=tr.incident, pilot=pilot)
            eng.relief_pending_id = r.id
            eng.emit(
                t,
                "relay_planned",
                f"🤝 relay planned: {r.id} launches to relieve {tr.id} ({tr.bat:.0f}%)",
                {
                    "drone": r.id,
                    "relieves": tr.id,
                    "incident": tr.incident,
                    "tracker_battery": round(tr.bat, 1),
                },
            )


def relay_handoff(eng, d, t: float) -> None:
    """Relay drone within relay_handoff_km of the target: it takes the track, old tracker RTBs."""
    sp = eng.suspect.pos(t)
    if d.mission == "relay" and dist(d.pos, sp) < eng.world.policies["relay_handoff_km"]:
        old = eng.drones[eng.tracker_id]
        d.mission = "track"
        eng.tracker_id = d.id
        eng.relief_pending_id = None
        eng.emit(
            t,
            "handoff",
            f"🤝 HANDOFF {old.id} → {d.id} (gap 0 s); {old.id} at {old.bat:.0f}%",
            {
                "from": old.id,
                "to": d.id,
                "gap_s": 0,
                "battery": round(old.bat, 1),
                "incident": d.incident,
            },
        )
        eng.rtb(old, t, "track handed off")


# ---- 7. safety RTB ----
def safety_rtb(eng, d, t: float) -> None:
    """Projected landing at the nearest free slot ≤ reserve + safety_rtb_margin_pct → RTB now
    (a plan failure)."""
    margin = eng.world.policies["safety_rtb_margin_pct"]
    proj = eng.projected_landing(d) if d.mission != "returning" else None
    if proj is not None and proj <= eng.M["reserve_pct"] + margin:
        # L36: a safety RTB is a KPI (plan margin used up), not an invariant violation
        eng.kpi.safety_rtbs += 1
        eng.emit(
            t,
            "safety_rtb",
            f"safety RTB {d.id} ({d.bat:.0f}%, projected landing {proj:.0f}%)",
            {"drone": d.id, "battery": round(d.bat, 1), "projected_pct": round(proj, 1)},
            timeline=False,
        )
        eng.rtb(d, t, "safety")
