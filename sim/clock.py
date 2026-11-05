"""Clock: how many 0.1-min ticks are due per real second (L10).

Modes
- demo: 0.5 sim-min per real s (speed 0.5 | 1 | 2). Holds while ANY hold is registered;
  agent-beat holds expire after at most 45 real s. Skip = 10 sim-min/s to the next event, only
  while nothing is held.
- live: runs at the set speed; only approval holds stop it.
- game: like demo, but sim/game.py sets the skips and agent holds are capped (L54).
- headless: as fast as possible (no holds).

The time source is injectable so tests never sleep.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sim.geo import DT, EPS

DEFAULT_RATE = 0.5
SPEEDS = (0.5, 1.0, 2.0)
SKIP_RATE = 10.0
MAX_BEAT_S = 45.0
HEADLESS_BATCH = 600  # ticks per runner iteration in headless mode


class Clock:
    def __init__(self, mode: str = "demo", time_source: Callable[[], float] = time.monotonic):
        if mode not in ("demo", "live", "headless", "game", "city"):
            raise ValueError(f"unknown clock mode {mode}")
        self.mode = mode
        self.time = time_source
        self.rate = DEFAULT_RATE
        self.running = False
        self._last: float | None = None
        self._budget = 0.0
        self._holds: dict[str, tuple[float | None, bool]] = {}  # key -> (expiry, is_approval)
        self.skip_target: float | None = None

    # --- run state ---
    def start(self) -> None:
        self.running = True
        self._last = self.time()

    def pause(self) -> None:
        self.running = False

    def resume(self) -> None:
        self.start()

    def set_speed(self, sim_min_per_s: float) -> None:
        v = float(sim_min_per_s)
        if v not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self.rate = v

    # --- holds ---
    def hold(self, key: str, max_real_s: float | None = MAX_BEAT_S, approval: bool = False):
        now = self.time()
        if approval:
            expiry = None if max_real_s is None else now + max_real_s
        else:
            expiry = now + min(MAX_BEAT_S if max_real_s is None else max_real_s, MAX_BEAT_S)
        self._holds[key] = (expiry, approval)

    def release(self, key: str) -> None:
        self._holds.pop(key, None)

    def active_holds(self) -> list[str]:
        now = self.time()
        for k, (exp, _a) in list(self._holds.items()):
            if exp is not None and now >= exp:
                del self._holds[k]
        if self.mode == "headless":
            return []
        if self.mode == "live":
            return sorted(k for k, (_e, a) in self._holds.items() if a)
        return sorted(self._holds)

    @property
    def held(self) -> bool:
        return bool(self.active_holds())

    # --- skip ---
    def skip_to(self, target_min: float) -> None:
        self.skip_target = target_min

    # --- tick budget ---
    def ticks_due(self, next_tick_t: float) -> int:
        """Ticks to process now. `next_tick_t` = sim time of the engine's next tick."""
        now = self.time()
        elapsed = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        if not self.running:
            return 0
        if self.mode == "headless":
            return HEADLESS_BATCH
        if self.held:
            self._budget = 0.0
            return 0
        if self.skip_target is not None:
            remaining = int((self.skip_target - next_tick_t) / DT + EPS) + 1
            if remaining <= 0:
                self.skip_target = None
                self._budget = 0.0
            else:
                self._budget += elapsed * SKIP_RATE
                n = min(int(self._budget / DT + EPS), remaining)
                self._budget -= n * DT
                if n == remaining:
                    self.skip_target = None
                    self._budget = 0.0
                return n
        self._budget += elapsed * self.rate
        n = int(self._budget / DT + EPS)
        self._budget -= n * DT
        return n

    def view(self) -> dict:
        return {
            "mode": self.mode,
            "running": self.running,
            "sim_min_per_s": self.rate,
            "held": self.active_holds(),
            "skipping": self.skip_target is not None,
        }
