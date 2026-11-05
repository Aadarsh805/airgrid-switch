"""Incidents: records, geofence, feed reveal, views for /incidents.

Feed facts are scenario data (`feed`), held internally and never served until revealed.
A fact is revealed when its time has come (t <= now) AND a drone of that incident has been on
station (incident/perimeter/corridor/search drone on station, or a track/relay drone with the
suspect in view). Revealed facts are structured descriptions only, never identities.
"""

from __future__ import annotations

import re

from sim.geo import dist, fmt, tmin

STATION_MISSIONS = ("incident", "perimeter", "corridor", "search")
MIN_BARE_VALUE = 5  # shorter fact values count as a quote only together with their field name


def load_feed(scenario: dict) -> list[dict]:
    """Internal sorted copy of the scenario feed (never served)."""
    rows = [
        {
            "incident": f["incident"],
            "t": tmin(f["t"]),
            "fact": dict(f["fact"]),
            "confidence": f.get("confidence", "direct"),
        }
        for f in scenario.get("feed") or []
    ]
    return sorted(rows, key=lambda r: r["t"])


def priority(eng, kind: str) -> int:
    pr = eng.world.policies["incident_priority"]
    return pr.get(kind, pr["default"])


def on_station(eng, d) -> bool:
    if d.state != "airborne" or not d.incident or d.launch_wait > 0:
        return False
    if d.mission in STATION_MISSIONS:
        return d.on_station
    if d.mission in ("track", "relay"):
        sp = eng.suspect.pos(eng.now) if eng.suspect else None
        if sp is None or eng.airspace.in_zone(sp):
            return False
        return dist(d.pos, sp) <= eng.world.policies["suspect_view_km"]
    return False


def update(eng, t: float) -> None:
    """Tick hook (after movement): mark first on-station per incident, reveal due facts."""
    for d in eng.drones.values():
        inc = eng.incidents.get(d.incident) if d.incident else None
        if inc is not None and inc.get("station_t") is None and on_station(eng, d):
            inc["station_t"] = t
    for f in eng._feed:
        if f.get("revealed") or f["t"] > t + 1e-9:
            continue
        inc = eng.incidents.get(f["incident"])
        if inc is None or inc.get("station_t") is None:
            continue
        f["revealed"] = t
        fact = public_fact(f)
        inc.setdefault("facts", []).append(fact)
        eng.emit(
            t,
            "feed_fact",
            f"📋 {f['incident']} feed: {fact['fact']} ({fact['confidence']})",
            {"incident": f["incident"], **fact},
            timeline=False,
        )


def public_fact(f: dict) -> dict:
    return {"t": fmt(f["t"]), "fact": dict(f["fact"]), "confidence": f["confidence"]}


def revealed(eng, iid: str | None) -> list[dict]:
    inc = eng.incidents.get(iid)
    return [dict(x) for x in (inc.get("facts") or [])] if inc else []


def _quoted(value: str, text: str) -> bool:
    """`value` appears in `text` as a whole value (not inside a longer word or number)."""
    return re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", text) is not None


def evidence_matches(evidence: str, facts: list[dict]) -> bool:
    """Evidence quotes a revealed fact: it contains one complete fact value, case-insensitive.
    Short values ('3', 'none') say nothing alone, so they also need their field name."""
    ev = str(evidence or "").lower()
    for f in facts:
        for key, v in f["fact"].items():
            value = str(v).lower()
            if not value.strip() or not _quoted(value, ev):
                continue
            if len(value) >= MIN_BARE_VALUE or _quoted(key.replace("_", " "), ev):
                return True
    return False


def view(eng, iid: str) -> dict:
    v = eng.incidents[iid]
    recording = sorted(d.id for d in eng.drones.values() if d.recording and d.incident == iid)
    return {
        "id": iid,
        "kind": v["kind"],
        "at": v["at"],
        "pos": list(eng.world.point(v["at"])),
        "report": v.get("note"),
        "priority": v.get("priority", priority(eng, v["kind"])),
        "status": v["status"],
        "geofence_km": eng.world.policies["geofence_km"],
        "opened": fmt(v["t"]),
        "closed": fmt(v["closed_t"]) if v.get("closed_t") is not None else None,
        "drones": sorted(
            d.id for d in eng.drones.values() if d.state == "airborne" and d.incident == iid
        ),
        "units": eng.ground.of_incident(iid),
        "eyes_on": fmt(eng.kpi.eyes_on[iid]) if iid in eng.kpi.eyes_on else None,
        "authorisations": [a.view() for a in eng.approvals.auths if a.incident == iid],
        "recording_drones": recording,
        "recording": recording,
        "facts": revealed(eng, iid),
        "simulated": True,
    }
