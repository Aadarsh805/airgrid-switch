"""Geometry and time helpers (km grid, sectors, rects). Arithmetic mirrors tools/fleet_check.py."""

import math

DT = 0.1  # sim-min per tick
EPS = 1e-9


def tmin(s: str | int | float) -> float:
    """'17:12.5' / '17:12' / '17:12:30' -> minutes since 00:00."""
    if isinstance(s, int | float):
        return float(s)
    parts = str(s).split(":")
    m = int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        m += int(parts[2]) / 60
    return m


def fmt(t: float) -> str:
    """Minutes -> 'HH:MM' or 'HH:MM:SS' (identical to the checker)."""
    h = int(t // 60)
    m = t - h * 60
    s = int(round((m - int(m)) * 60))
    mm = int(m)
    if s == 60:
        mm, s = mm + 1, 0
    return f"{h:02d}:{mm:02d}" + (f":{s:02d}" if s else "")


def hhmm(t: float) -> str:
    """Minutes -> 'HH:MM' (seconds dropped)."""
    return fmt(t)[:5]


def mmss(minutes: float) -> str:
    """Minutes -> 'm:ss'. An ETA is never rounded down."""
    secs = math.ceil(minutes * 60 - EPS)
    return f"{secs // 60}:{secs % 60:02d}"


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def sector_of(p) -> str:
    return ("N" if p[1] > 3.0 else "S") + ("E" if p[0] >= 3.0 else "W")


def in_rect(p, r) -> bool:
    return r["x"][0] <= p[0] <= r["x"][1] and r["y"][0] <= p[1] <= r["y"][1]
