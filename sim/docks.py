"""Docks, slots, reservations, booking with DOCK_FULL / DOCK_OFFLINE logs."""

from sim.geo import dist


class Dock:
    def __init__(self, did: str, spec: dict):
        self.id = did
        self.kind = spec["kind"]
        self.at = list(spec["at"])
        self.slots = spec["slots"]
        self.occupants: list[str] = []
        self.reserved: list[str] = []
        self.online = True
        self.restricted: str | None = None  # id of an active no-entry zone covering the dock

    @property
    def usable(self) -> bool:
        """Online and not inside an active no-entry zone (P1 while VIP-TEMP is active, L22)."""
        return self.online and not self.restricted

    def free_slots(self) -> int:
        return self.slots - len(self.occupants) - len(self.reserved) if self.usable else 0

    def slot_states(self) -> list[dict]:
        held = self.occupants + self.reserved
        out = []
        for i in range(self.slots):
            drone = held[i] if i < len(held) else None
            if not self.usable:  # contract statuses only; `restricted` names the zone
                status = "OFFLINE"
            elif i < len(self.occupants):
                status = "OCCUPIED"
            elif drone:
                status = "RESERVED"
            else:
                status = "FREE"
            out.append({"slot": i, "drone": drone, "status": status})
        return out

    def view(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "at": self.at,
            "slots": self.slots,
            "online": self.online,
            "restricted": self.restricted,
            "occupants": list(self.occupants),
            "reserved": list(self.reserved),
            "free": self.free_slots(),
            "slot_state": self.slot_states(),
        }


def booking_order(docks: dict[str, Dock], pos, home: str) -> list[str]:
    """Docks by distance; the drone's home dock wins ties."""
    return sorted(docks, key=lambda k: (dist(pos, docks[k].at), 0 if k == home else 1))


def book_slot(docks: dict[str, Dock], drone_id: str, pos, home: str, log_radius_km: float):
    """Reserve the nearest free slot. Returns (dock_id | None, skipped) where `skipped` lists
    (dock_id, reason) for every nearer dock within log_radius_km that was unavailable
    (to be logged)."""
    skipped = []
    for k in booking_order(docks, pos, home):
        if docks[k].free_slots() > 0:
            docks[k].reserved.append(drone_id)
            return k, skipped
        if not docks[k].online:
            reason = "DOCK_OFFLINE"
        elif docks[k].restricted:
            reason = "NO_FLY_ZONE"
        else:
            reason = "DOCK_FULL"
        if dist(pos, docks[k].at) < log_radius_km:
            skipped.append((k, reason))
    return None, skipped
