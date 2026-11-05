"""`--profile game`: the quiet Discord of the 3-minute game (docs/game-mode.md, L54).

Rooms #ops and #privacy-log. Only the game's moments post, about 15 a shift, each at most once.
Mentions go only to the four game agents. A beat means the owner may hold the clock while it
narrates: the sim caps that at policies.game_clock.agent_hold_s (8 s), and only clock-running
moments are beats, so offline agents cost seconds, never the game.

The bot has no operator key: everything here comes from the public event log.
"""

from __future__ import annotations

from dataclasses import dataclass

from worldbot.rooms import PRIVACY_LOG

OPS = "#ops"
ROOMS = (OPS, PRIVACY_LOG)
AGENTS = ("dispatch", "intel", "airspace", "privacy")
ALERTS = 2  # crash and robbery; later alerts belong to the shift the game fast-forwards
# The approval card is the robbery prompt: it carries the id for a typed `approve A-000n`.
CARD_BEATS = {"robbery"}
# Traps speak through their refusal; `finale_no_track` has only one answer. An approved track
# speaks through its authorisation line in #privacy-log.
CHOICE_BEATS = {"crash", "robbery", "finale"}
REFUSALS = {"PRIVACY_POLICY": (PRIVACY_LOG, "privacy"), "NO_FLY_ZONE": (OPS, "airspace")}


@dataclass(frozen=True)
class Moment:
    key: tuple
    room: str = OPS
    agent: str | None = None
    beat: bool = False
    ends: bool = False
    text: str = ""  # the whole line, when the profile composed it already


class GameProfile:
    rooms = ROOMS
    home_room = OPS
    decides = False

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.seen: set[tuple] = set()
        self.alerts: list[str] = []
        self.over = False

    def _choose(self, kind: str, p: dict, event: dict) -> Moment | None:
        beat = p.get("beat")
        if kind == "cad_alert":
            if len(self.alerts) >= ALERTS:
                return None
            self.alerts.append(p.get("incident"))
            return Moment((kind, p.get("incident")), agent="dispatch", beat=True)
        if kind in ("launch", "eyes_on"):
            if p.get("incident") != (self.alerts or [None])[0]:
                return None
            agent = "intel" if kind == "eyes_on" else None
            return Moment((kind,), agent=agent, beat=kind == "eyes_on")
        if kind == "game_prompt" and beat not in CARD_BEATS:
            return Moment((kind, beat))
        if kind == "game_choice" and beat in CHOICE_BEATS and p.get("choice") != "approve":
            return Moment((kind, beat))
        if kind == "approved":
            return Moment((kind, event.get("text")), PRIVACY_LOG, "privacy")
        if kind == "refusal" and p.get("code") in REFUSALS:
            room, agent = REFUSALS[p["code"]]
            return Moment((kind, p["code"]), room, agent)
        if kind == "game_blocked":
            return Moment((kind,), agent="airspace")
        if kind == "game_catch":
            return Moment((kind,), agent="dispatch", ends=True)
        if kind == "incident_closed" and p.get("incident") == (self.alerts + [None] * 2)[1]:
            return Moment((kind,), agent="dispatch", ends=True)
        return None

    def moment(self, event: dict) -> Moment | None:
        """The moment this event is, or None. Past the chase the game fast-forwards: all silent."""
        if self.over:
            return None
        m = self._choose(event.get("type"), event.get("payload") or {}, event)
        if m is None or m.key in self.seen:
            return None
        self.seen.add(m.key)
        self.over = m.ends
        return m

    def line(self, event: dict, moment: Moment) -> str:
        text = " ".join(str(event.get("text", "")).split())
        if event.get("type") == "game_prompt":
            text += " (simulated)"
        return f"{text} · @{moment.agent}" if moment.agent else text
