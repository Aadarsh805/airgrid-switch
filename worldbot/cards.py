"""World Bot message formats (04 §4.4) and chat command parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PREFIX = re.compile(r"^(?:FO|RP|WC)\s*:\s*", re.IGNORECASE)
_COMMANDS = (
    ("approve", re.compile(r"approve\s+(A-\d+)", re.IGNORECASE)),
    ("reject", re.compile(r"reject\s+(A-\d+)(?:\s+(.+))?", re.IGNORECASE | re.DOTALL)),
    ("take_control", re.compile(r"take[- ]control\s+(D-\d+)", re.IGNORECASE)),
    ("release_control", re.compile(r"release[- ]control\s+(D-\d+)", re.IGNORECASE)),
)
_CITY = re.compile(
    r"(?:@[\w-]+\s+)?(leave|stay|transport|treat)(?:\s+(INC-\w+|[a-z][\w-]*(?:\s+[a-z][\w-]*)?))?"
    r"(?:\s+(\d+)\s*(?:more\s+)?(?:mins?|minutes?))?[.!]?",
    re.IGNORECASE,
)
HUMAN_ACTION_ROLE = {"take_control": "remote_pilot", "release_control": "remote_pilot"}
# City orders (L67), the whole message: `watch market`, `watch market 30 min`, `stop watching
# market`, `dock D-4`, `release d04`. Minutes are clock minutes, as shown on the dashboard.
_ORDERS = (
    (
        "watch",
        re.compile(
            r"(?:@[\w-]+\s+)?(?:watch|increase surveillance on|surveil)\s+([a-z][\w -]*?)"
            r"(?:\s+(?:for\s+)?(\d+)\s*(?:mins?|minutes?)?)?[.!]?",
            re.IGNORECASE,
        ),
    ),
    (
        "unwatch",
        re.compile(
            r"(?:@[\w-]+\s+)?(?:stop\s+watch(?:ing)?|unwatch)\s+([a-z][\w -]*?)[.!]?",
            re.IGNORECASE,
        ),
    ),
    ("dock", re.compile(r"(?:@[\w-]+\s+)?dock\s+(d-?\d{1,2})[.!]?", re.IGNORECASE)),
    ("release", re.compile(r"(?:@[\w-]+\s+)?release\s+(d-?\d{1,2})[.!]?", re.IGNORECASE)),
)
ORDER_ROLE = "watch_commander"


@dataclass(frozen=True)
class Command:
    verb: str  # approve | reject | take_control | release_control
    target: str  # A-0007 | D-04
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{self.verb.replace('_', '-')} {self.target}"


def parse_command(text: str) -> Command | None:
    """The whole message must be the command, so a chat line that merely mentions one is not.
    FO:/RP: prefixes are dropped: approvals map to the human's role, not the prefix."""
    body = _PREFIX.sub("", text.strip())
    for verb, rx in _COMMANDS:
        m = rx.fullmatch(body)
        if m:
            reason = m.group(2).strip() if verb == "reject" and m.group(2) else ""
            return Command(verb, m.group(1).upper(), reason)
    return None


def parse_city_command(text: str) -> Command | None:
    """City mode, the whole message: `leave`, `stay`, `leave market`, `stay 2 more minutes`.
    `target` is the place word, empty if none; `reason` holds any minutes asked for. Whether the
    word means an open call is decided against the open calls, so `stay safe` stays chat."""
    m = _CITY.fullmatch(_PREFIX.sub("", text.strip()))
    if not m:
        return None
    return Command(m.group(1).lower(), m.group(2) or "", m.group(3) or "")


def parse_order(text: str) -> Command | None:
    """A city order. `target` is the place or the drone (D-04); `reason` any minutes asked for."""
    body = _PREFIX.sub("", text.strip())
    for verb, rx in _ORDERS:
        m = rx.fullmatch(body)
        if not m:
            continue
        what = m.group(1).strip()
        if verb in ("dock", "release"):
            what = f"D-{int(what.upper().removeprefix('D').lstrip('-')):02d}"
        minutes = m.group(2) if verb == "watch" and m.lastindex and m.lastindex >= 2 else ""
        return Command(verb, what, minutes or "")
    return None


def _feed_text(event: dict) -> str:
    """A feed fact in words; the sim's text is for logs."""
    p = event.get("payload") or {}
    pairs = " · ".join(f"{k.replace('_', ' ')}: {v}" for k, v in (p.get("fact") or {}).items())
    return f"📋 {p.get('incident')} feed | {pairs} | {p.get('confidence')}"


def event_alert(event: dict, room: str, agent: str, mention: bool = True) -> str:
    """Only a mention wakes an agent, so `mention` goes on events the owner must answer."""
    kind = str(event.get("type", ""))
    what = _feed_text(event) if kind == "feed_fact" else str(event.get("text", "")).strip()
    return f"📣 {kind.upper()} · {what} · {room}" + (f" · @{agent}" if mention else "")


def approval_card(approval: dict, needs: str) -> str:
    aid, type_ = approval["id"], approval["type"]
    return (
        f"🟡 APPROVAL {aid} · {type_} · by @{approval.get('created_by')} · needs: {needs}\n"
        f"   {approval.get('summary') or type_} · Reply: approve {aid} | reject {aid} <reason>"
    )


def approved_line(approval_id: str, by: str, result: str) -> str:
    return f"✅ APPROVED {approval_id} by @{by} · {result}"


def rejected_line(approval_id: str, by: str, reason: str) -> str:
    return f"❌ REJECTED {approval_id} by @{by}: {reason}"


def refusal_line(action: str, code: str, instead: str) -> str:
    return f"❌ REJECTED | action: {action} | reason: {code} | instead: {instead}"


def control_line(command: Command, by: str, result: str) -> str:
    return f"🕹 {command.label} by @{by} · {result}"


def error_line(action: str, reason: str) -> str:
    return f"⚠ {action} not done: {reason}. Nothing changed in the sim; say it again."
