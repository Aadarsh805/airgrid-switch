"""Pilots and caps (max 4 each, no auto pool)."""


class Pilot:
    def __init__(self, pid: str, spec: dict):
        self.id = pid
        self.kind = spec["kind"]
        self.name = spec.get("name", pid)
        self.roles = list(spec.get("roles", []))
        self.max_drones = spec["max_drones"]
        self.drones: set[str] = set()

    def has_capacity(self) -> bool:
        return len(self.drones) < self.max_drones

    def view(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "max_drones": self.max_drones,
            "drones": sorted(self.drones),
            "load": f"{len(self.drones)}/{self.max_drones}",
        }


class Pilots(dict):
    """id -> Pilot, in world order."""

    @classmethod
    def from_world(cls, spec: dict) -> "Pilots":
        return cls({k: Pilot(k, v) for k, v in spec.items()})

    def pilot_for(self, prefer: list[str]) -> str | None:
        """Preferred pilots first, then any simulated duty pilot with capacity (checker order)."""
        for p in prefer:
            if self[p].has_capacity():
                return p
        for p in self:
            if self[p].kind == "simulated" and self[p].has_capacity():
                return p
        return None

    def first_human_remote_pilot(self) -> str | None:
        for p in self.values():
            if p.kind == "human" and "remote_pilot" in p.roles and p.has_capacity():
                return p.id
        return None

    def human_remote_pilot(self) -> str | None:
        """The first human remote pilot, saturated or not (an agent's launch is refused rather
        than silently reassigned; policy launches use first_human_remote_pilot)."""
        return next(
            (p.id for p in self.values() if p.kind == "human" and "remote_pilot" in p.roles), None
        )

    def least_loaded_duty_pilot(self) -> str | None:
        duty = [p for p in self.values() if p.kind == "simulated" and p.has_capacity()]
        return min(duty, key=lambda p: len(p.drones)).id if duty else None

    def assign(self, drone, pid: str | None) -> None:
        if drone.pilot:
            self[drone.pilot].drones.discard(drone.id)
        drone.pilot = pid
        if pid:
            self[pid].drones.add(drone.id)
