"""Pure validators: each returns OK or Rejection(code, reason, hint) with a §4.2 code only.

No validator touches engine state; callers pass plain values. Hints name the lawful alternative
CLI, as in the 04-CONTRACTS §4.2 example.
"""

from __future__ import annotations

from dataclasses import dataclass

CODES = (
    "BATTERY_RESERVE",
    "DRONE_NOT_READY",
    "NO_FLY_ZONE",
    "WEATHER_GROUNDED",
    "DOCK_FULL",
    "DOCK_OFFLINE",
    "PILOT_SATURATED",
    "SEPARATION",
    "OUT_OF_RANGE",
    "AUTH_REQUIRED",
    "PRIVACY_POLICY",
    "MISSION_EXPIRED",
    "FORBIDDEN_ROLE",
    "NOT_FOUND",
)
ACCESS_CODES = ("FORBIDDEN_ROLE", "NOT_FOUND")  # access errors, not rule refusals


@dataclass(frozen=True)
class Rejection:
    code: str
    reason: str
    hint: str
    ok = False

    def __post_init__(self):
        if self.code not in CODES:
            raise ValueError(f"{self.code} is not a §4.2 reason code")

    def body(self) -> dict:
        return {"ok": False, "code": self.code, "reason": self.reason, "hint": self.hint}


class _Ok:
    ok = True

    def __repr__(self) -> str:
        return "OK"


OK = _Ok()


def first(*results):
    """First Rejection among already-evaluated results, else OK."""
    for r in results:
        if not r.ok:
            return r
    return OK


def _xy(p) -> str:
    return f"{p[0]:g},{p[1]:g}"


# ---------------------------------------------------------------- geometry helper (pure)
def segment_hits_rect(a, b, rect) -> bool:
    """Liang-Barsky: does the segment a→b touch the closed rectangle?"""
    x0, x1 = rect["x"]
    y0, y1 = rect["y"]
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


# ---------------------------------------------------------------- one validator per code
def battery_reserve(drone: str, projected_pct: float, reserve_pct: float):
    if projected_pct < reserve_pct:
        return Rejection(
            "BATTERY_RESERVE",
            f"{drone} would land at {projected_pct:.0f}% (< {reserve_pct:g}% reserve)",
            f"airgrid fleet  # pick a drone with more battery, or shorten --for-min; "
            f"airgrid rtb --drone {drone}",
        )
    return OK


def drone_not_airborne(drone: str, state: str):
    """Rejection for commands that only make sense in the air (hold, rtb, take-control)."""
    return Rejection(
        "DRONE_NOT_READY",
        f"{drone} is {state}, not airborne",
        "airgrid fleet  # choose an airborne drone",
    )


def drone_not_ready(
    drone: str,
    state: str,
    battery: float,
    gate: float,
    dock_online: bool,
    out_of_service: bool = False,
):
    why = None
    if out_of_service:
        why = "is out of service"
    elif state != "docked":
        why = f"is {state}"
    elif not dock_online:
        why = "sits on an offline dock"
    elif battery < gate:
        why = f"is at {battery:.0f}% (< {gate:g}% launch gate)"
    if why:
        return Rejection(
            "DRONE_NOT_READY",
            f"{drone} {why}",
            "airgrid fleet  # choose a READY drone (docked, dock online, above the gate)",
        )
    return OK


def no_fly_zone(drone: str, start, target, zones: list[dict]):
    """zones: the zones that forbid entry now (RED always, VIP only while active)."""
    for z in zones:
        if segment_hits_rect(start, target, z["rect"]):
            where = "ends inside" if segment_hits_rect(target, target, z["rect"]) else "crosses"
            return Rejection(
                "NO_FLY_ZONE",
                f"Route {drone} → ({_xy(target)}) {where} {z['id']} (no override)",
                f"airgrid hold --drone {drone} --at-boundary {z['id']}; "
                f"airgrid route check --drone {drone} --to <point outside {z['id']}>",
            )
    return OK


def weather_grounded(drone: str, sectors: list[str], grounded: set[str]):
    hit = sorted(set(sectors) & set(grounded))
    if hit:
        return Rejection(
            "WEATHER_GROUNDED",
            f"{drone}: wind over limit, sector(s) {hit} grounded",
            "airgrid airspace  # wait for the reopen, or use a drone outside the grounded sectors",
        )
    return OK


def dock_available(dock: str, online: bool, free_slots: int):
    if not online:
        return Rejection(
            "DOCK_OFFLINE",
            f"dock {dock} is offline",
            "airgrid docks; airgrid rtb --drone <id>  # the sim books the nearest free slot",
        )
    if free_slots <= 0:
        return Rejection(
            "DOCK_FULL",
            f"dock {dock} has no free slot",
            "airgrid docks; airgrid rtb --drone <id>  # the sim books the nearest free slot",
        )
    return OK


def pilot_saturated(
    pilot: str, drones: list[str], max_drones: int, drone: str, retry: str | None = None
):
    """`retry`: a duty pilot with capacity, named in the hint (launches pass it)."""
    if drone not in drones and len(drones) >= max_drones:
        example = f"--pilot {retry}" if retry else "e.g. --pilot DP-2"
        return Rejection(
            "PILOT_SATURATED",
            f"{pilot} already supervises {len(drones)}/{max_drones} {sorted(drones)}; "
            f"{drone} would be drone #{len(drones) + 1}",
            f"airgrid pilots  # assign a duty pilot with capacity ({example})",
        )
    return OK


def separation(drone: str, target, band, others: list[tuple[str, list, object]], min_km: float):
    """others: (drone, target, band) of airborne drones with an explicit band."""
    if band is None:
        return OK
    for other, ot, ob in others:
        if other != drone and ob == band and ot is not None:
            if ((ot[0] - target[0]) ** 2 + (ot[1] - target[1]) ** 2) ** 0.5 < min_km:
                return Rejection(
                    "SEPARATION",
                    f"{drone} target within {min_km:g} km of {other} in band {band}",
                    "airgrid launch ... --band <other band>  # or drop --band and let the sim "
                    "offset bands",
                )
    return OK


def airspace_intrusion(drone: str, sectors: list[str], held: dict[str, str]):
    """held: sector -> 'HH:MM' end of an unauthorised-drone SEPARATION hold."""
    hit = sorted(set(sectors) & set(held))
    if hit:
        return Rejection(
            "SEPARATION",
            f"{drone}: unauthorised drone in sector(s) {hit}; SEPARATION hold until {held[hit[0]]}",
            "airgrid airspace  # wait for the hold to lift, or launch outside the held sector",
        )
    return OK


def release_evidence(unit: str, incident: str, quoted: bool):
    """Early release of a ground unit must quote a revealed feed fact of that incident."""
    if not quoted:
        return Rejection(
            "AUTH_REQUIRED",
            f"early release of {unit} from {incident} needs evidence quoting a revealed feed fact",
            f"airgrid incidents --id {incident}  # quote a whole revealed feed fact in --evidence",
        )
    return OK


def out_of_range(drone: str, origin, target, radius_km: float):
    d = ((origin[0] - target[0]) ** 2 + (origin[1] - target[1]) ** 2) ** 0.5
    if d > radius_km:
        return Rejection(
            "OUT_OF_RANGE",
            f"{drone} target ({_xy(target)}) is {d:.1f} km from its dock (max {radius_km:g})",
            "airgrid fleet  # launch from a dock nearer the target",
        )
    return OK


AUTH_HINTS = {
    "track": "airgrid propose track --incident <INC> --scope '...'",
    "broadcast": "airgrid propose broadcast --drone <id> --text '...'",
    "footage_release": "airgrid propose footage-release --incident <INC> --clip <C> --to '...'",
    "zone": "airgrid propose zone --id <ZONE> --until HH:MM",
    "divert_off_incident": "ask @kazum for a divert_off_incident approval",
    "standing_policy": "ask @kazum to approve the standing auto-launch policy",
}


def auth_required(what: str, approved: bool, subject: str, used: str | None = None):
    """`used`: id of an approval that existed but was already consumed (one approval, one use)."""
    if not approved and used:
        return Rejection(
            "AUTH_REQUIRED",
            f"{what} for {subject}: approval {used} was already used (one approval, one use)",
            "propose again: " + AUTH_HINTS.get(what, "airgrid approvals --status pending"),
        )
    if not approved:
        return Rejection(
            "AUTH_REQUIRED",
            f"{what} for {subject} needs a watch_commander approval on record",
            AUTH_HINTS.get(what, "airgrid approvals --status pending"),
        )
    return OK


def privacy_face_id(request: str):
    """Face identification does not exist (L19). Always rejected."""
    return Rejection(
        "PRIVACY_POLICY",
        f"face identification is not a capability of this system: '{request}'",
        "describe vehicles/clothing instead: airgrid feed --incident <INC>; "
        "airgrid log-refusal --code PRIVACY_POLICY",
    )


def privacy_recording(drone: str, in_open_geofence: bool, in_no_record: str | None):
    if in_no_record:
        return Rejection(
            "PRIVACY_POLICY",
            f"{drone} is inside {in_no_record}: recording forced off",
            "transit only; recording resumes automatically inside the incident geofence",
        )
    if not in_open_geofence:
        return Rejection(
            "PRIVACY_POLICY",
            f"{drone} is outside every open incident geofence: recording stays off",
            "airgrid incidents  # recording is derived: inside an open incident geofence only",
        )
    return OK


def privacy_track_needs_incident(incident: str | None):
    if not incident:
        return Rejection(
            "PRIVACY_POLICY",
            "tracking without an open incident is not allowed",
            "airgrid incident open --from-alert <CAD>; airgrid propose track --incident <INC>",
        )
    return OK


def mission_expired(incident: str, status: str, auth_expired: bool = False):
    if status == "closed":
        return Rejection(
            "MISSION_EXPIRED",
            f"{incident} is closed; no further tasking on it",
            "airgrid rtb --drone <id>  # open a new incident if there is a new need",
        )
    if auth_expired:
        return Rejection(
            "MISSION_EXPIRED",
            f"the authorisation for {incident} has expired",
            f"airgrid propose track --incident {incident} --scope '...'  # ask for a new one",
        )
    return OK


def shift_over(what: str):
    return Rejection(
        "MISSION_EXPIRED",
        f"the shift has ended; {what} cannot start",
        "airgrid status; load a scenario to run a new shift",
    )


def forbidden_role(who: str, action: str):
    return Rejection(
        "FORBIDDEN_ROLE",
        f"{who} may not perform {action}",
        "check config/roles.yaml; human-only actions go through the World Bot",
    )


def not_found(kind: str, ident) -> Rejection:
    return Rejection("NOT_FOUND", f"no {kind} {ident}", f"airgrid status  # list valid {kind}s")


def decided_already(approval_id: str, status: str) -> Rejection:
    return Rejection(
        "MISSION_EXPIRED",
        f"{approval_id} was already {status}",
        "airgrid approvals --status pending",
    )
