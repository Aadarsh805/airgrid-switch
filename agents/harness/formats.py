"""One regex per room-message format in docs/04-CONTRACTS.md §4.4, plus the checks that only need
text: reason codes, number provenance and role-allowed CLI calls. No LLM, no sim.

Formats are fixed, the wording inside a field may vary: fields are `[^|]+`, the frame is literal.
"""

from __future__ import annotations

import re

CODES = (
    "BATTERY_RESERVE",
    "DRONE_NOT_READY",
    "NO_FLY_ZONE",
    "WEATHER_GROUNDED",
    "DOCK_FULL",
    "DOCK_OFFLINE",
    "PILOT_SATURATED",
    "SEPARATION",
    "OUT_OF_RANGE",
    "AUTH_REQUIRED",
    "PRIVACY_POLICY",
    "MISSION_EXPIRED",
    "FORBIDDEN_ROLE",
    "NOT_FOUND",
)

_F = r"[^|\n]+?"  # one field
_DRONE = r"D-\d+"
_INC = r"INC-\d+"
_APPROVAL = r"A-\d+"
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?"
_MSS = r"\d+:\d{2}"
_NUM = r"\d+(?:\.\d+)?"
_VS = "️?"  # some keyboards and models add the emoji variation selector
_CODE = "(?:" + "|".join(CODES) + ")"


def _rx(*fields: str) -> re.Pattern:
    return re.compile(r" \| ".join(fields))


FORMATS: dict[str, re.Pattern] = {
    # Dispatch
    "INCIDENT": _rx(r"🚨 INCIDENT", rf"{_INC} [\w-]+", _F, "P[1-3]", rf"report: {_F}"),
    "LAUNCH": _rx(
        "🚀 LAUNCH",
        rf"{_DRONE} → {_INC} \w+",
        rf"ETA {_MSS}",
        rf"battery {_NUM}%",
        r"pilot [\w-]+",
        rf"basis: (?:auto-policy|approval {_APPROVAL} @\w+)",
    ),
    "EYES_ON": _rx(f"👁{_VS} EYES-ON", _INC, rf"{_DRONE} on station {_TIME} \(\d+ s after alert\)"),
    # Fleet
    "FLEET": _rx(
        "🔋 FLEET",
        r"airborne \d+ · ready \d+ · charging \d+ · slots free \d+/18",
        rf"tightest: {_DRONE} {_NUM}%",
    ),
    "ROTATION": _rx(
        "🔄 ROTATION",
        rf"{_DRONE} takes [A-Z]+ from {_DRONE} \({_NUM}%\)",
        "relief on station before RTB",
        "coverage continuous",
    ),
    "SLOT": _rx(
        f"🅿{_VS} SLOT",
        rf"{_DRONE} → [A-Z0-9-]+",
        r"(?:(?:DOCK_FULL|DOCK_OFFLINE) at [^|\n]+?|home)",
        rf"lands ~{_TIME} at ~{_NUM}%",
    ),
    # Airspace
    "ROUTE": _rx(
        f"🧭{_VS} ROUTE",
        _DRONE,
        rf"{_F} → {_F}",
        r"zones: (?:clear|NR-HOSPITAL transit, recording OFF)",
        rf"ETA {_MSS}",
    ),
    "ROUTE_DENIED": _rx(
        f"⛔{_VS} ROUTE DENIED",
        _DRONE,
        "crosses RED-AIRFIELD",
        "reason: NO_FLY_ZONE",
        "holding at boundary",
        rf"alternative: {_F}",
    ),
    "WEATHER": _rx(
        f"🌬{_VS} WEATHER",
        rf"{_F} wind {_NUM} m/s \(limit 12\)",
        rf"grounded until {_TIME}",
        "reason: WEATHER_GROUNDED",
        rf"coverage gap: {_F}",
    ),
    # Intel
    "SITREP": _rx("📋 SITREP", _INC, _F, "confidence: (?:direct|partial)"),
    "EVIDENCE": _rx(
        "🎥 EVIDENCE",
        _INC,
        rf"clip {_F}",
        _F,
        "inside geofence ✓",
        r"persons identified: none \(capability absent\)",
    ),
    # Relay
    "TRACK": _rx(
        "🎯 TRACK",
        _INC,
        rf"target: (?!unknown\b){_F}",
        rf"held by {_DRONE} \({_NUM}%\)",
        rf"auth {_APPROVAL} @\w+, expires {_TIME}",
    ),
    "HANDOFF": _rx(
        "🤝 HANDOFF", rf"{_DRONE} → {_DRONE}", rf"reason: {_DRONE} at {_NUM}%", "gap 0 s"
    ),
    # Privacy
    "AUTH": _rx(
        "🔐 AUTH", rf"{_F} approved by @\w+", rf"scope: {_F}", rf"expires (?:{_TIME}|n/a)", "logged"
    ),
    "PRIVACY_REFUSAL": _rx(
        f"⛔{_VS} PRIVACY REFUSAL",
        rf"request: {_F}",
        rf"by: {_F}",
        "reason: PRIVACY_POLICY",
        rf"lawful alternative: {_F}",
    ),
    "FOOTAGE": _rx(
        "📼 FOOTAGE",
        rf"{_INC} clip {_F}",
        rf"release to {_F}",
        rf"status: (?:pending {_APPROVAL}|released|denied)",
    ),
    # Comms
    "UNIT": _rx("🚓 UNIT", rf"\w+ → {_INC}", rf"ETA {_MSS}", "(?:en route|on scene)"),
    "RELEASED": _rx("✅ RELEASED", rf"\w+ from {_INC}", rf"evidence: {_F}", rf"saves ~{_NUM} min"),
    "ADVISORY": _rx("📣 ADVISORY", _F, _F, rf"until {_TIME}"),
    "SHIFT_REPORT": re.compile(
        r"📊 SHIFT REPORT \| "
        rf"flights \d+ · eyes-on \d+ s · units released \d+ · coverage {_NUM}% · "
        r"reserve breaches 0 · red-zone 0 · refusals \d+"
    ),
    # Any
    "REJECTED": _rx("❌ REJECTED", rf"action: {_F}", rf"reason: {_CODE}", rf"instead: {_F}"),
    # World Bot (the §4.4 room line, then the @owner mention worldbot/cards.py adds)
    "BOT_EVENT": re.compile(r"📣 [A-Z_]+ · .+ · #[\w-]+(?: · @\w+)?"),
    "BOT_APPROVAL": re.compile(
        rf"🟡 APPROVAL {_APPROVAL} · [\w/-]+ · by @\w+ · needs: \w+\n"
        rf"\s+.+ · Reply: approve {_APPROVAL} \| reject {_APPROVAL} <reason>"
    ),
    "BOT_APPROVED": re.compile(rf"✅ APPROVED {_APPROVAL} by @\w+ · .+"),
    "BOT_REJECTED": re.compile(rf"❌ REJECTED {_APPROVAL} by @\w+: .+"),
    "BOT_PILOT": re.compile(r'🛩 DP-\d+ .+ \(duty pilot, simulated\): ".+"'),
    "BOT_RADIO": re.compile(r'📻 .+ \(simulated\): ".+"'),
}


QUIET = "NO_REPLY"


PREFIXES = {
    "INCIDENT": "🚨 INCIDENT",
    "LAUNCH": "🚀 LAUNCH",
    "EYES_ON": "👁 EYES-ON",
    "FLEET": "🔋 FLEET",
    "ROTATION": "🔄 ROTATION",
    "SLOT": "🅿 SLOT",
    "ROUTE": "🧭 ROUTE",
    "ROUTE_DENIED": "⛔ ROUTE DENIED",
    "WEATHER": "🌬 WEATHER",
    "SITREP": "📋 SITREP",
    "EVIDENCE": "🎥 EVIDENCE",
    "TRACK": "🎯 TRACK",
    "HANDOFF": "🤝 HANDOFF",
    "AUTH": "🔐 AUTH",
    "PRIVACY_REFUSAL": "⛔ PRIVACY REFUSAL",
    "FOOTAGE": "📼 FOOTAGE",
    "UNIT": "🚓 UNIT",
    "RELEASED": "✅ RELEASED",
    "ADVISORY": "📣 ADVISORY",
    "SHIFT_REPORT": "📊 SHIFT REPORT",
    "REJECTED": "❌ REJECTED",
}


def malformed_lines(reply: str) -> list[str]:
    """Lines that start like a §4.4 format and are not that format."""
    out = []
    for raw in reply.splitlines():
        line = raw.strip()
        bare = line.replace("\ufe0f", "")
        for name, prefix in PREFIXES.items():
            if bare.startswith(prefix) and not FORMATS[name].fullmatch(line):
                out.append(line)
                break
    return out


def matches(name: str, text: str) -> bool:
    """The whole message is exactly this format."""
    return FORMATS[name].fullmatch(text.strip()) is not None


def lines_matching(name: str, reply: str) -> list[str]:
    """Lines of a reply that are this format (agents add an @handoff line after the formatted one).
    Multi-line formats are tried on the whole reply too."""
    rx = FORMATS[name]
    hits = [ln.strip() for ln in reply.splitlines() if rx.fullmatch(ln.strip())]
    if not hits and rx.fullmatch(reply.strip()):
        hits = [reply.strip()]
    return hits


def is_quiet(reply: str) -> bool:
    """`NO_REPLY` is an agent staying quiet (L45): silence, not a format failure."""
    return reply.strip() == QUIET


def which(text: str) -> str | None:
    """Name of the format this whole message is, if any."""
    return next((n for n in FORMATS if matches(n, text)), None)


def codes_in(text: str) -> list[str]:
    """§4.2 codes quoted verbatim in a text."""
    return [c for c in CODES if re.search(rf"\b{c}\b", text)]


def unknown_codes(text: str) -> list[str]:
    """SHOUTY_SNAKE words that look like reason codes and are not in §4.2."""
    return sorted({w for w in re.findall(r"\b[A-Z]{3,}(?:_[A-Z]{3,})+\b", text) if w not in CODES})


# ---------------------------------------------------------------- number provenance
# §4.4 literals: part of the frame, not a claim. Everything else must be sourced.
_FRAME_LITERALS = (
    re.compile(r"\(limit 12\)"),
    re.compile(r"(?<=slots free )(\d+)/18"),
    re.compile(r"gap 0 s"),
    re.compile(r"reserve breaches 0 · red-zone 0"),
)
_ID = re.compile(r"\b(?:INC|DP|D|A)-\d+\b")
_ROOM = re.compile(r"#[a-z-]+-?\d*")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[:.]\d+)*(?![\w])")


def _strip_frame(text: str) -> str:
    for rx in _FRAME_LITERALS:
        text = rx.sub(lambda m: m.group(1) if m.groups() else " ", text)
    return text


def numbers_in(text: str) -> list[str]:
    """Identifiers (D-13, INC-2, A-0003) and bare numbers/times, in order; rooms and frame
    literals dropped. Letter-glued tokens such as `P9`, `J4`, `5h` are read for their digits."""
    text = _ROOM.sub(" ", _strip_frame(text))
    out = _ID.findall(text)
    text = _ID.sub(" ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    return out + _NUMBER.findall(text)


def _corpus(sources: list[str]) -> tuple[str, set[str]]:
    text = "\n".join(sources)
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    return text, set(re.findall(r"\d+(?:[:.]\d+)*", spaced))


def _proven(token: str, text: str, nums: set[str]) -> bool:
    if _ID.fullmatch(token):
        return re.search(rf"(?<![\w-]){re.escape(token)}(?!\d)", text) is not None
    if token in nums:
        return True
    if ":" in token:  # 17:42 for a CLI 17:42:00
        return any(n.startswith(token + ":") for n in nums)
    # Same value, other spelling: 60 for a CLI 60.0. Nothing is derived: no rounding, no sums.
    return any(":" not in n and n.count(".") <= 1 and float(n) == float(token) for n in nums)


_WORDS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "ninety": 90,
    "hundred": 100,
}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)


def unproven_numbers(reply: str, sources: list[str]) -> list[str]:
    """Numbers in the reply that are in none of the sources (that turn's CLI output and the
    transcript). Empty means every number has provenance. A spelled-out number needs its value or
    its word in a source.

    Known limit: a match is on value only, anywhere in the sources, so a real number quoted in the
    wrong field (a battery as an ETA) passes. Field-level checks would need per-format parsing."""
    text, nums = _corpus(sources)
    # `~` marks an approximate field: ~62 is proven by a CLI 62.2, and only by rounding (L48)
    rounded = {str(round(float(n))) for n in re.findall(r"\d+\.\d+", text)}
    tilde = set(re.findall(r"~(\d+)(?![\d.:])", reply))
    bad = [
        t
        for t in numbers_in(reply)
        if not _proven(t, text, nums) and not (t in tilde and t in rounded)
    ]
    for word in _WORD.findall(reply):
        known = word.lower() in text.lower() or _proven(str(_WORDS[word.lower()]), text, nums)
        if not known:
            bad.append(word)
    return bad


# ---------------------------------------------------------------- role-allowed CLI calls (§4.1)
GROUPS = {"incident", "propose", "plan", "route", "evidence", "track", "relay", "privacy", "unit"}
READS = {
    "status",
    "fleet",
    "docks",
    "pilots",
    "incidents",
    "airspace",
    "units",
    "events",
    "approvals",
    "privacy-log",
    "kpi",
    "decisions",
    # listed under a role in §4.1, read-only in §4.3: the sim gives them to everyone
    "feed",
    "plan rotation",
    "route check",
    "track status",
    "relay plan",
}
ANY_WRITES = {"log-refusal", "simulate"}
WRITES = {
    "dispatch": {
        "incident open",
        "incident close",
        "launch",
        "divert",
        "track assign",
        "propose standing-policy",
        "propose track",
        "propose broadcast",
    },
    "fleet": {"rtb", "propose out-of-service"},
    "airspace": {"hold", "corridor", "propose zone"},
    "intel": {"evidence tag"},
    "relay": set(),
    "privacy": {"propose footage-release", "privacy check", "set-recording", "release-footage"},
    "comms": {"unit task", "unit release", "advisory"},
}
HUMAN_ONLY = {"approve", "reject", "take-control", "release-control"}


def command_of(argv: list[str]) -> str:
    """`['propose', 'track', '--incident', 'INC-2']` → `propose track`."""
    words = [a for a in argv if not a.startswith("-")][:2] if argv else []
    if not words:
        return ""
    first = words[0]
    if first in GROUPS and len(words) > 1 and argv[0] == first and argv[1] == words[1]:
        return f"{first} {words[1]}"
    return argv[0] if not argv[0].startswith("-") else ""


def allowed_commands(agent: str) -> set[str]:
    return READS | ANY_WRITES | WRITES[agent]


def forbidden_calls(agent: str, argvs: list[list[str]]) -> list[list[str]]:
    """Calls outside the role. Help lookups and no command are harmless and allowed."""
    ok = allowed_commands(agent) | {""}
    return [a for a in argvs if "--help" not in a and command_of(a) not in ok]
