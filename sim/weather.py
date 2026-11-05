"""Wind per sector, grounding (checker semantics)."""

from sim.geo import EPS, tmin


class Weather:
    def __init__(self, wind_limit_ms: float, sectors):
        self.limit = wind_limit_ms
        self.sectors = list(sectors)
        self.grounded: set[str] = set()
        self.ends: list[tuple[float, list[str]]] = []
        self.wind: dict[str, float] = {}

    def ground(self, sectors: list[str], wind_ms: float, until: str) -> None:
        for s in sectors:
            self.grounded.add(s)
            self.wind[s] = wind_ms
        self.ends.append((tmin(until), sectors))

    def expire(self, t: float) -> list[list[str]]:
        """Reopen sectors whose grounding ended at or before t; returns the reopened lists."""
        reopened = []
        for te, secs in list(self.ends):
            if te <= t + EPS:
                for s in secs:
                    self.grounded.discard(s)
                    self.wind.pop(s, None)
                self.ends.remove((te, secs))
                reopened.append(secs)
        return reopened

    def view(self) -> dict:
        until = {}
        for te, secs in self.ends:
            for s in secs:
                until[s] = te
        return {
            s: {
                "wind_ms": self.wind.get(s),
                "grounded": s in self.grounded,
                "until_min": until.get(s),
            }
            for s in self.sectors
        }
