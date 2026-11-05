"""RoomTransport interface: everything the World Bot needs from a chat system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


class SwitchError(RuntimeError):
    """A Switch call failed; the message is the exact reason and never holds a key."""


@dataclass(frozen=True)
class RoomMessage:
    room: str
    author: str  # the mapped human, or the agent. Approvals authorise on this.
    text: str
    sender: str = ""  # transport's stable sender id, opaque; empty on FakeRoom
    sender_name: str = ""  # display text as sent: never used to authorise


class RoomTransport(ABC):
    @abstractmethod
    async def post(self, room: str, text: str) -> str:
        """Post to a room; returns the message id."""

    @abstractmethod
    def messages(self) -> AsyncIterator[RoomMessage]:
        """Human and agent messages from the rooms."""

    async def on_sim_time(self, sim_min: float) -> None:  # noqa: B027
        """The sim clock moved (FakeRoom scripts are keyed on it; real rooms ignore it)."""
