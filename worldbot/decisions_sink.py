"""Decision sinks. FakeSink records to memory and runs/. SwitchSink also appends to the room
document "Flight authorisation log" in #dispatch (L26, L40: the bot is its one writer). If the
document can't be written, the one-line summary goes in-room and the failure is logged (L14, L26).
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from sim.geo import fmt
from worldbot.rooms import DISPATCH
from worldbot.transport import SwitchError

log = logging.getLogger("worldbot.decisions")
RUNS = Path(__file__).resolve().parent.parent / "runs"
DOC_NAME = "Flight authorisation log"
DOC_DESCRIPTION = "Approvals, rejections and refusals of the simulated AirGrid shift, by sim time."
DOC_INSTRUCTIONS = (
    "Read it for the shift report and cross-check with `airgrid decisions`: the sim wins a "
    "mismatch. The World Bot is the only writer. Simulated training data."
)
DOC_HEADER = "Flight authorisation log (AirGrid, simulated)"
CONNECT_WAIT_S = 15


class DecisionSink(ABC):
    @abstractmethod
    def record(self, entry: dict) -> None:
        """Called on every approval, rejection and refusal."""


class FakeSink(DecisionSink):
    def __init__(self, path: Path | str | None = None):
        self.entries: list[dict] = []
        self.path = (
            Path(path) if path else RUNS / f"decisions-fake-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
        )

    def record(self, entry: dict) -> None:
        self.entries.append(dict(entry))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def summary_line(entry: dict) -> str:
    """`17:41 · approved · approve A-0003 · by kazum · track`, always one line."""
    parts = [
        fmt(entry.get("sim_min") or 0),
        entry.get("kind"),
        f"{entry.get('action')} {entry.get('target')}",
        f"by {entry.get('actor')}",
        entry.get("code"),
        entry.get("detail"),
    ]
    return " · ".join(" ".join(str(p).split()) for p in parts if p)


class SwitchSink(FakeSink):
    """`record` stays synchronous for the bot: one worker task does all the writing, in order."""

    def __init__(
        self,
        room,
        path: Path | str | None = None,
        connect_wait_s=CONNECT_WAIT_S,
        room_name: str = DISPATCH,
        post_fallback: bool = True,
    ):
        super().__init__(path or RUNS / f"decisions-switch-{datetime.now():%Y%m%d-%H%M%S}.jsonl")
        self.room_name = room_name
        self.post_fallback = post_fallback
        self.room = room
        self.document_id: str | None = None
        self.lines: list[str] = []
        self.fallbacks = 0
        self._base = DOC_HEADER
        self._connect_wait_s = connect_wait_s
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def record(self, entry: dict) -> None:
        super().record(entry)
        self._queue.put_nowait(dict(entry))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())

    async def drain(self) -> None:
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            entry = await self._queue.get()
            try:
                await self._write(summary_line(entry))
            finally:
                self._queue.task_done()

    async def _open(self) -> None:
        await self.room.wait_connected(self._connect_wait_s)
        self.document_id, existing = await self.room.open_document(
            self.room_name, DOC_NAME, DOC_DESCRIPTION, DOC_INSTRUCTIONS, DOC_HEADER
        )
        self._base = existing.rstrip("\n") or DOC_HEADER

    async def _write(self, line: str) -> None:
        # A failed line stays in `lines`, so the document catches up on the next good write.
        self.lines.append(line)
        try:
            if self.document_id is None:
                await self._open()
            text = "\n".join([self._base, *self.lines]) + "\n"
            await self.room.update_document(self.document_id, text)
        except SwitchError as e:
            self.fallbacks += 1
            room = self.room_name
            log.error("decision log not written (%s); posted in %s: %s", e, room, line)
            if self.post_fallback:
                await self.room.post(room, f"📣 DECISION · {line} · {room}")
