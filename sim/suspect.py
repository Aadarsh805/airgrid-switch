"""Suspect path interpolation. Internal only: the API serves the current position, and only
while a tracking drone has it in view (L29c). Never the path, never ahead of now."""

from sim.geo import dist, tmin


class SuspectPath:
    def __init__(self, path: list[dict]):
        self._pts = sorted((tmin(x["t"]), x["at"]) for x in path)

    def __bool__(self) -> bool:
        return bool(self._pts)

    def pos(self, t: float):
        s = self._pts
        if not s:
            return None
        if t <= s[0][0]:
            return s[0][1]
        for (ta, a), (tb, b) in zip(s, s[1:], strict=False):
            if ta <= t <= tb:
                f = (t - ta) / (tb - ta)
                return [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])]
        return s[-1][1]


def visible_position(engine) -> list[float] | None:
    """Current suspect position if a drone on `track` is within suspect_view_km and the suspect
    is outside RED-AIRFIELD; otherwise None."""
    if not engine.suspect:
        return None
    sp = engine.suspect.pos(engine.now)
    if engine.airspace.in_zone(sp):
        return None
    view_km = engine.world.policies["suspect_view_km"]
    for d in engine.drones.values():
        if d.state == "airborne" and d.mission == "track" and dist(d.pos, sp) <= view_km:
            return [round(v, 3) for v in sp]
    return None
