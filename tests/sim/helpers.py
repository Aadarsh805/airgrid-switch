"""Test-side helpers: independent of sim internals (only canonical files + public outputs)."""

import math
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EPS = 1e-6

# "17:08:06  msg" / "   17:12  msg" (checker format: f"{fmt(t):>8}  {msg}")
LINE_RE = re.compile(r"^\s*(\d{2}):(\d{2})(?::(\d{2}))?\s{2,}(\S.*?)\s*$")


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def load_yaml(rel: str) -> dict:
    return yaml.safe_load(read_text(rel))


def tmin(hhmm: str) -> float:
    parts = hhmm.split(":")
    m = int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        m += int(parts[2]) / 60
    return m


def parse_line(line: str):
    m = LINE_RE.match(line)
    if not m:
        return None
    h, mm, ss, msg = m.groups()
    return int(h) * 60 + int(mm) + (int(ss) / 60 if ss else 0.0), msg


def event_lines(lines) -> list[tuple[float, str]]:
    """Parse checker-format lines (iterable of strings or one text blob); drop non-event lines."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    out = []
    for ln in lines:
        p = parse_line(ln)
        if p:
            out.append(p)
    return out


def golden_expected() -> list[tuple[float, str]]:
    return event_lines(read_text("docs/golden-timeline.txt"))


def dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def in_rect(p, rect) -> bool:
    return rect["x"][0] <= p[0] <= rect["x"][1] and rect["y"][0] <= p[1] <= rect["y"][1]


def north(p) -> bool:
    """Checker sector rule: y > 3.0 is NW/NE."""
    return p[1] > 3.0


def frame_at_or_after(frames, t):
    for f in frames:
        if f["t"] >= t - EPS:
            return f
    return None


def frame_at_or_before(frames, t):
    best = None
    for f in frames:
        if f["t"] <= t + EPS:
            best = f
    return best


def find(lines, pattern):
    """All (t, msg, match) whose msg matches regex `pattern`."""
    rx = re.compile(pattern)
    return [(t, msg, m) for t, msg in lines if (m := rx.search(msg))]


# ---------- Phase 03 auth fixtures (written to temp files by conftest) ----------

AGENT_ROLES = [
    "dispatch",
    "fleet",
    "airspace",
    "intel",
    "relay",
    "privacy",
    "comms",
    "fire-station",
    "hospital",
    "police",
]
TOKENS_DOC = {
    "agents": {r: f"test-agent-{r}-token" for r in AGENT_ROLES},
    "humans": {"kazum": "test-human-kazum-token", "t2": "test-human-t2-token"},
}
HUMANS_DOC = {
    "humans": [
        {"discord": "kazum", "roles": ["watch_commander", "remote_pilot", "field_officer"]},
        {"discord": "t2", "roles": ["field_officer"]},
    ]
}
