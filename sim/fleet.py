"""Drones: battery, movement, launch delay (arithmetic identical to the checker)."""

from sim.geo import DT, dist


class Drone:
    def __init__(self, did: str, spec: dict):
        self.id = did
        self.home = spec["home"]
        self.thermal = bool(spec.get("thermal", False))
        self.state = "docked"
        self.slot: str | None = None
        self.pos: list[float] | None = None
        self.bat = 100.0
        self.mission: str | None = None
        self.loop: str | None = None
        self.target = None
        self.incident: str | None = None
        self.pilot: str | None = None
        self.launch_wait = 0.0
        self.on_station = False
        self.flights = 0
        self.manual = False
        self.min_landing = 101.0
        self.band = None  # explicit altitude band (SEPARATION only applies when set)
        self.recording = False  # derived each tick (sim/privacy.py), never set by agents
        self.recording_off = False  # set_recording off override
        self.out_of_service = False
        self.fault: str | None = None  # drone_fault trigger: grounded until the card is decided
        self.hold_until: float | None = None  # SEPARATION hold (unauthorised drone) end
        self.exit_point: list[float] | None = None  # leaving a zone that just became active

    def view(self) -> dict:
        v = {
            "id": self.id,
            "home": self.home,
            "thermal": self.thermal,
            "state": self.state,
            "battery": round(self.bat, 1),
            "pos": [round(c, 3) for c in self.pos],
            "mission": self.mission,
            "incident": self.incident,
            "loop": self.loop,
            "pilot": self.pilot,
            "slot": self.slot,
            "booked_slot": self.target if self.mission == "returning" else None,
            "on_station": self.on_station,
            "manual": self.manual,
            "flights": self.flights,
            "recording": self.recording,
            "out_of_service": self.out_of_service,
            "fault": self.fault,
            "held": self.hold_until is not None,
        }
        # fixed targets only; track/relay targets derive from the suspect and are never served
        if self.mission in ("incident", "perimeter", "corridor", "hold", "search") and self.target:
            v["target"] = [round(c, 3) for c in self.target]
        return v


def move(d: Drone, target, speed: float) -> bool:
    dd = dist(d.pos, target)
    step = speed * DT
    if dd <= step:
        d.pos = list(target)
        return True
    d.pos = [
        d.pos[0] + (target[0] - d.pos[0]) * step / dd,
        d.pos[1] + (target[1] - d.pos[1]) * step / dd,
    ]
    return False


def drain(d: Drone, rate_pct_per_min: float) -> None:
    d.bat -= rate_pct_per_min * DT


def charge(d: Drone, rate_pct_per_min: float, cap: float) -> None:
    d.bat = min(cap, d.bat + rate_pct_per_min * DT)
