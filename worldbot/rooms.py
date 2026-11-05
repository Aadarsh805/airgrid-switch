"""Room routing: event type → room + owning agent (docs/05-AGENTS.md)."""

from __future__ import annotations

from dataclasses import dataclass

DISPATCH, FLEET_HANGAR, AIRSPACE = "#dispatch", "#fleet-hangar", "#airspace"
FIELD_UNITS, PRIVACY_LOG, SHIFT_REPORT = "#field-units", "#privacy-log", "#shift-report"
INCIDENT = "@incident"  # placeholder: the room of the event's incident
ROOMS = (
    DISPATCH,
    FLEET_HANGAR,
    AIRSPACE,
    "#incident-1",
    "#incident-2",
    FIELD_UNITS,
    PRIVACY_LOG,
    SHIFT_REPORT,
)
AGENTS = ("dispatch", "fleet", "airspace", "intel", "relay", "privacy", "comms")
CITY_AGENTS = ("fire-station", "hospital", "police")  # city mode only (L56)
ALL_AGENTS = AGENTS + CITY_AGENTS


@dataclass(frozen=True)
class Route:
    room: str
    agent: str
    beat: bool = False  # the owner is expected to post: the demo clock waits for it (max 45 s)


# Routine events the room should see but no agent answers: no mention, so no session wakes (L51).
QUIET_EVENTS = {
    "rtb",
    "landing",
    "dock_restored",
    "corridor",
    "weather_reopened",
    "relay_planned",
    "take_control",
    "release_control",
    "evidence_tag",
}

EVENT_ROUTES: dict[str, Route] = {
    # dispatch
    "shift_brief": Route(DISPATCH, "dispatch", True),  # holds until the policy card is up
    "cad_alert": Route(DISPATCH, "dispatch", True),
    "incident_opened": Route(DISPATCH, "dispatch"),
    "incident_closed": Route(DISPATCH, "dispatch", True),
    "launch": Route(DISPATCH, "dispatch", True),
    "eyes_on": Route(DISPATCH, "dispatch", True),
    "divert": Route(DISPATCH, "dispatch", True),
    "approval_needed": Route(DISPATCH, "dispatch"),
    "auto_launch_unavailable": Route(DISPATCH, "dispatch"),
    "launch_held": Route(DISPATCH, "dispatch"),
    "rejected": Route(DISPATCH, "dispatch"),
    "broadcast": Route(DISPATCH, "dispatch"),
    "room_message": Route(FIELD_UNITS, "comms"),
    # fleet
    "rotation": Route(FLEET_HANGAR, "fleet", True),
    "rtb": Route(FLEET_HANGAR, "fleet"),
    "landing": Route(FLEET_HANGAR, "fleet"),
    "slot_reroute": Route(FLEET_HANGAR, "fleet", True),
    "dock_fault": Route(FLEET_HANGAR, "fleet", True),
    "dock_restored": Route(FLEET_HANGAR, "fleet"),
    "drone_fault": Route(FLEET_HANGAR, "fleet", True),
    "safety_rtb": Route(FLEET_HANGAR, "fleet"),
    "loop_uncovered": Route(FLEET_HANGAR, "fleet"),
    "pilot_capacity": Route(FLEET_HANGAR, "fleet"),
    "take_control": Route(FLEET_HANGAR, "fleet"),
    "release_control": Route(FLEET_HANGAR, "fleet"),
    # airspace
    "weather_grounded": Route(AIRSPACE, "airspace", True),
    "weather_reopened": Route(AIRSPACE, "airspace"),
    "hold": Route(AIRSPACE, "airspace", True),
    "corridor": Route(AIRSPACE, "airspace"),
    "perimeter": Route(INCIDENT, "dispatch"),
    "zone_exit": Route(AIRSPACE, "airspace"),
    "airspace_intrusion": Route(AIRSPACE, "airspace", True),
    "airspace_intrusion_cleared": Route(AIRSPACE, "airspace"),
    # incident rooms
    "feed_fact": Route(INCIDENT, "intel", True),
    "evidence_tag": Route(INCIDENT, "intel"),
    "track_assigned": Route(INCIDENT, "relay", True),
    "relay_planned": Route(INCIDENT, "relay"),
    "handoff": Route(INCIDENT, "relay", True),
    "reacquire": Route(INCIDENT, "relay", True),
    # privacy
    "approved": Route(PRIVACY_LOG, "privacy", True),
    # comms
    "unit_tasked": Route(FIELD_UNITS, "comms", True),
    "unit_on_scene": Route(FIELD_UNITS, "comms"),
    "unit_cleared": Route(FIELD_UNITS, "comms"),
    "unit_available": Route(FIELD_UNITS, "comms"),
    "unit_released": Route(FIELD_UNITS, "comms", True),
    "unit_stood_down": Route(FIELD_UNITS, "comms"),
    "unit_radio": Route(FIELD_UNITS, "comms"),
    "advisory": Route(FIELD_UNITS, "comms"),
    "signal_retimed": Route(FIELD_UNITS, "comms"),
}
DEFAULT_ROUTE = Route(DISPATCH, "dispatch")
REFUSAL_ROUTES = {
    "PRIVACY_POLICY": Route(PRIVACY_LOG, "privacy", True),
    "NO_FLY_ZONE": Route(AIRSPACE, "airspace", True),
}
# Approval type → room of its card and result
APPROVAL_ROOMS = {
    "track": DISPATCH,
    "broadcast": DISPATCH,
    "standing_policy": DISPATCH,
    "divert_off_incident": DISPATCH,
    "footage_release": PRIVACY_LOG,
    "zone": DISPATCH,
    "out_of_service": FLEET_HANGAR,
}


def incident_room(incident: str | None) -> str:
    """INC-1 → #incident-1, INC-2 → #incident-2; there are two rooms, the rest go to #dispatch."""
    room = f"#incident-{str(incident).removeprefix('INC-')}"
    return room if room in ROOMS else DISPATCH


def needs_incident(event: dict) -> bool:
    """An incident-room event whose payload names only the drone (`reacquire`)."""
    r = EVENT_ROUTES.get(event.get("type"))
    p = event.get("payload") or {}
    return bool(r and r.room == INCIDENT and not p.get("incident") and p.get("drone"))


def route(event: dict) -> Route:
    kind, payload = event.get("type"), event.get("payload") or {}
    r = EVENT_ROUTES.get(kind, DEFAULT_ROUTE)
    if kind == "refusal":
        r = REFUSAL_ROUTES.get(payload.get("code"), DEFAULT_ROUTE)
    if kind == "room_message" and payload.get("room"):
        return Route("#" + str(payload["room"]).lstrip("#"), r.agent)
    if r.room == INCIDENT:
        return Route(incident_room(payload.get("incident")), r.agent, r.beat)
    return r


def approval_room(type_: str) -> str:
    return APPROVAL_ROOMS.get(type_, DISPATCH)
