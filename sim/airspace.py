"""Zones and boundary points (checker semantics)."""

from sim.geo import dist, in_rect
from sim.rules import segment_hits_rect

EXIT_MARGIN_KM = 0.05  # same step-outside margin as boundary_point


class Airspace:
    def __init__(self, zones: list[dict]):
        self.zones = [dict(z) for z in zones]
        red = [z for z in self.zones if z["kind"] == "red"][0]
        self.red, self.red_id = red["rect"], red["id"]

    def in_zone(self, p, kind: str = "red") -> str | None:
        for z in self.zones:
            if z["kind"] == kind and (kind != "vip" or z.get("active")):
                if in_rect(p, z["rect"]):
                    return z["id"]
        return None

    def boundary_point(self, p, rect=None) -> list[float]:
        """Nearest point just outside the zone rectangle (checker rule: step west of it)."""
        r = rect or self.red
        x = min(p[0], r["x"][0] - 0.05) if r["y"][0] <= p[1] <= r["y"][1] else p[0]
        return [x, p[1]]

    def no_entry(self) -> list[dict]:
        """Zones that forbid entry now: RED always, VIP only while active."""
        return [z for z in self.zones if z["kind"] == "red" or self.active_vip(z)]

    @staticmethod
    def active_vip(z: dict) -> bool:
        return z["kind"] == "vip" and bool(z.get("active"))

    def vip_zones(self) -> list[dict]:
        return [z for z in self.zones if self.active_vip(z)]

    def vip_at(self, p) -> dict | None:
        return next((z for z in self.vip_zones() if in_rect(p, z["rect"])), None)

    @staticmethod
    def exit_point(p, rect) -> list[float]:
        """Nearest point just outside the rectangle, inside the district (`rect` touches its
        edge, so only the sides facing the district are exits)."""
        x0, x1, y0, y1 = *rect["x"], *rect["y"]
        c = []
        if x0 > 0:
            c.append([x0 - EXIT_MARGIN_KM, p[1]])
        if y0 > 0:
            c.append([p[0], y0 - EXIT_MARGIN_KM])
        if x1 < 6:
            c.append([x1 + EXIT_MARGIN_KM, p[1]])
        if y1 < 6:
            c.append([p[0], y1 + EXIT_MARGIN_KM])
        return min(c, key=lambda q: dist(p, q))

    def detour(self, pos, target):
        """Where a drone at `pos` should fly to reach `target` without entering an active VIP
        zone: `target` itself if the way is clear, a corner round the zone, or `pos` (hold) if
        `target` is inside the zone. Red-zone geometry is untouched (checker semantics)."""
        for z in self.vip_zones():
            r = z["rect"]
            if not segment_hits_rect(pos, target, r):
                continue
            if in_rect(target, r):
                return pos
            m = EXIT_MARGIN_KM
            corners = [
                [x, y]
                for x in (r["x"][0] - m, r["x"][1] + m)
                for y in (r["y"][0] - m, r["y"][1] + m)
            ]
            inside_map = [c for c in corners if 0 <= c[0] <= 6 and 0 <= c[1] <= 6]
            clear = [c for c in inside_map if not segment_hits_rect(pos, c, r)]
            return min(clear or [pos], key=lambda c: dist(pos, c) + dist(c, target))
        return target

    def view(self) -> list[dict]:
        return [
            {
                "id": z["id"],
                "kind": z["kind"],
                "rect": z["rect"],
                "active": bool(z.get("active", True)),
                "until": z.get("until"),
            }
            for z in self.zones
        ]
