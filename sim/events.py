"""Event log. Every event has a type, a JSON payload and the checker-format text.

`timeline=True` events are exactly the lines the checker prints; internal events
(approval_needed, shift_brief, auto_launch_unavailable, ...) never render as timeline lines.
Per-tick state diffs are not events: the SSE layer computes them from state_view().
"""

import hashlib
import json
from dataclasses import dataclass, field

from sim.geo import fmt


@dataclass
class Event:
    seq: int
    t: float
    type: str
    text: str
    payload: dict = field(default_factory=dict)
    timeline: bool = True

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "t": round(self.t, 4),
            "clock": fmt(self.t),
            "type": self.type,
            "text": self.text,
            "payload": self.payload,
            "timeline": self.timeline,
        }

    def line(self) -> str:
        return f"{fmt(self.t):>8}  {self.text}"


class EventLog:
    def __init__(self):
        self.items: list[Event] = []

    def append(self, t: float, type_: str, text: str, payload=None, timeline=True) -> Event:
        e = Event(len(self.items) + 1, t, type_, text, dict(payload or {}), timeline)
        self.items.append(e)
        return e

    def __len__(self) -> int:
        return len(self.items)

    def since(self, t: float | None = None, seq: int | None = None) -> list[Event]:
        out = self.items
        if seq is not None:
            out = [e for e in out if e.seq > seq]
        if t is not None:
            out = [e for e in out if e.t >= t - 1e-9]
        return list(out)

    def timeline_lines(self) -> list[str]:
        return [e.line() for e in self.items if e.timeline]

    def canonical_json(self) -> str:
        return json.dumps(
            [e.to_dict() for e in self.items],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
