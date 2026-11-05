"""Ground units (simulated): patrol cars, ambulances, fire engines.

Manhattan movement (x first, then y) at ground_units.road_speed_km_per_min. A tasked unit rolls
after dispatch_delay_min, drives to the scene, stays on scene for on_scene_min[incident kind],
then drives back to its start position. States are a pure function of time (no float drift):
available → en_route → on_scene → returning → available.

`release` is an early release (Comms, with feed evidence, checked in sim/actions.py): the unit
leaves before its on-scene timer ends; the minutes it would still have spent are "saved".
The same model runs in the managed run and in the baseline (sim/baseline.py).
"""

from __future__ import annotations

from sim.geo import EPS


def manhattan(a, b) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def unit_kind(uid: str) -> str:
    return uid.rsplit("_", 1)[0]


def along(a, b, km: float) -> list[float]:
    """Point `km` along the Manhattan path a→b (x leg first, then y leg)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if km <= abs(dx):
        return [a[0] + (km if dx >= 0 else -km), a[1]]
    rest = min(km - abs(dx), abs(dy))
    return [b[0], a[1] + (rest if dy >= 0 else -rest)]


class Unit:
    def __init__(self, uid: str, home):
        self.id = uid
        self.kind = unit_kind(uid)
        self.home = list(home)
        self.pos = list(home)
        self.status = "available"
        self.incident: str | None = None
        self.scene = None
        self.origin = list(home)  # where the current leg started
        self.tasked_t = self.roll_t = self.arrive_t = self.clear_t = self.back_t = None
        self.leg_t = None  # time the current leg started moving
        self.on_scene_t: float | None = None  # first arrival on scene for the current task

    def view(self, now: float) -> dict:
        eta = None
        if self.status == "en_route":
            eta = round(max(0.0, self.arrive_t - now), 2)
        elif self.status == "returning":
            eta = round(max(0.0, self.back_t - now), 2)
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "pos": [round(c, 3) for c in self.pos],
            "incident": self.incident,
            "eta_min": eta,
            "simulated": True,
        }


class GroundUnits:
    def __init__(self, world_raw: dict):
        g = world_raw["ground_units"]
        self.speed = g["road_speed_km_per_min"]
        self.delay = g["dispatch_delay_min"]
        self.on_scene = g["on_scene_min"]
        self.units: dict[str, Unit] = {k: Unit(k, v) for k, v in g["start"].items()}
        self.saved_min = 0.0  # unit-minutes saved by early releases
        self.released = 0
        self.arrivals: dict[str, float] = {}  # incident -> first unit on scene

    def __contains__(self, uid) -> bool:
        return uid in self.units

    def on_scene_min(self, incident_kind: str) -> float:
        return self.on_scene.get(incident_kind, self.on_scene["default"])

    def task(self, t: float, uid: str, incident: str, scene, incident_kind: str) -> Unit:
        u = self.units[uid]
        self._settle(u, t)  # freeze the current position before re-routing
        u.status, u.incident, u.scene = "en_route", incident, list(scene)
        u.origin = list(u.pos)
        u.tasked_t = t
        u.roll_t = t + self.delay
        u.leg_t = u.roll_t
        u.arrive_t = u.roll_t + manhattan(u.origin, scene) / self.speed
        u.clear_t = u.arrive_t + self.on_scene_min(incident_kind)
        u.on_scene_t = None
        return u

    def release(self, t: float, uid: str) -> float:
        """Early release: returns the planned on-scene minutes saved."""
        u = self.units[uid]
        saved = max(0.0, u.clear_t - t)
        self.saved_min += saved
        self.released += 1
        self._return(u, t)
        return saved

    def stand_down(self, t: float, incident: str) -> list[str]:
        """Incident closed: its units go home (a normal stand-down, not an early release)."""
        out = []
        for u in self.units.values():
            if u.incident == incident and u.status in ("en_route", "on_scene"):
                self._return(u, t)
                out.append(u.id)
        return out

    def _return(self, u: Unit, t: float) -> None:
        self._settle(u, t)
        u.status, u.incident, u.scene = "returning", None, None
        u.origin, u.leg_t = list(u.pos), t
        u.back_t = t + manhattan(u.pos, u.home) / self.speed

    def _settle(self, u: Unit, t: float) -> None:
        if u.status == "en_route" and t > u.leg_t:
            u.pos = along(u.origin, u.scene, (t - u.leg_t) * self.speed)
        elif u.status == "returning" and t > u.leg_t:
            u.pos = along(u.origin, u.home, (t - u.leg_t) * self.speed)

    def tick(self, t: float) -> list[tuple]:
        """Advance every unit to time t. Returns transitions [(event, unit, incident)]."""
        out = []
        for u in self.units.values():
            if u.status == "en_route":
                if t >= u.arrive_t - EPS:
                    u.pos, u.status = list(u.scene), "on_scene"
                    u.on_scene_t = t
                    self.arrivals.setdefault(u.incident, t)
                    out.append(("unit_on_scene", u, u.incident))
                else:
                    self._settle(u, t)
            if u.status == "on_scene" and t >= u.clear_t - EPS:
                inc = u.incident
                self._return(u, t)
                out.append(("unit_cleared", u, inc))
            if u.status == "returning":
                if t >= u.back_t - EPS:
                    u.pos, u.status = list(u.home), "available"
                    out.append(("unit_available", u, None))
                else:
                    self._settle(u, t)
        return out

    def nearest_available(self, kind: str, pos, exclude=()) -> Unit | None:
        c = [
            (manhattan(u.pos, pos), u.id, u)
            for u in self.units.values()
            if u.kind == kind and u.status == "available" and u.id not in exclude
        ]
        return min(c)[2] if c else None

    def of_incident(self, incident: str) -> list[str]:
        return sorted(
            u.id
            for u in self.units.values()
            if u.incident == incident and u.status in ("en_route", "on_scene")
        )

    def view(self, now: float) -> list[dict]:
        return [u.view(now) for u in self.units.values()]
