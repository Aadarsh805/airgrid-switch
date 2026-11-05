"""Recording is derived state (L19) + the append-only privacy log.

recording = drone on an incident mission AND (inside that open incident's geofence
            (policies.geofence_km) OR covered by an active track authorisation)
            AND NOT inside a no_record zone AND not switched off by set_recording.
Patrols never record. Every on/off transition is a privacy_log row (never a timeline line).
"""

from __future__ import annotations

from sim.geo import dist, fmt

INCIDENT_MISSIONS = {"incident", "perimeter", "corridor", "search", "track", "relay", "hold"}
TRACK_MISSIONS = {"track", "relay", "hold", "corridor"}  # the authorised track corridor


class PrivacyLog:
    """Append-only: there is no update or delete path."""

    def __init__(self):
        self._rows: list[dict] = []

    def append(self, t: float, drone, incident, kind: str, detail: str, actor: str) -> dict:
        row = {
            "seq": len(self._rows) + 1,
            "sim_min": round(t, 4),
            "clock": fmt(t),
            "drone": drone,
            "incident": incident,
            "kind": kind,
            "detail": detail,
            "actor": actor,
        }
        self._rows.append(row)
        return dict(row)

    def rows(self, since: float | None = None) -> list[dict]:
        return [dict(r) for r in self._rows if since is None or r["sim_min"] >= since - 1e-9]

    def __len__(self) -> int:
        return len(self._rows)


def in_geofence(eng, d) -> bool:
    inc = eng.incidents.get(d.incident) if d.incident else None
    if not inc or inc.get("status") != "open":
        return False
    return dist(d.pos, eng.world.point(inc["at"])) <= eng.world.policies["geofence_km"]


def derive(eng, d) -> tuple[bool, str]:
    if d.state != "airborne" or d.mission not in INCIDENT_MISSIONS or not d.incident:
        return False, "not on an incident mission"
    inc = eng.incidents.get(d.incident)
    if not inc or inc.get("status") != "open":
        return False, f"{d.incident} not open"
    nr = eng.airspace.in_zone(d.pos, "no_record")
    if nr:
        return False, f"inside {nr}"
    if d.recording_off:
        return False, "switched off by set_recording"
    if in_geofence(eng, d):
        return True, f"inside {d.incident} geofence"
    if d.mission in TRACK_MISSIONS and eng.approvals.active_auth(d.incident, "track"):
        return True, f"authorised track on {d.incident}"
    return False, "outside geofence"


def update(eng, t: float) -> None:
    for d in eng.drones.values():
        on, why = derive(eng, d)
        if on != d.recording:
            d.recording = on
            eng.privacy.append(
                t, d.id, d.incident, "recording_on" if on else "recording_off", why, "sim"
            )


def check(eng, t: float) -> None:
    """Invariant: a recording drone is inside an open geofence or an authorised track,
    and never inside a no_record zone."""
    for d in eng.drones.values():
        if not d.recording:
            continue
        ok_area = in_geofence(eng, d) or (
            d.mission in TRACK_MISSIONS and eng.approvals.active_auth(d.incident, "track")
        )
        if d.state != "airborne" or not ok_area or eng.airspace.in_zone(d.pos, "no_record"):
            eng.violation(t, f"{d.id} recording outside a geofence")
