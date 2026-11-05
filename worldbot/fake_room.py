"""FakeRoom: terminal transport. Prints `[#room] text`, reads stdin lines `author: text`
(optionally `#room author: text`), or plays a script of (sim_time, author, room, text)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable

from worldbot.transport import RoomMessage, RoomTransport

DEFAULT_ROOM = "#dispatch"


def _minutes(t) -> float | None:
    if t is None:
        return None
    if isinstance(t, int | float):
        return float(t)
    h, m, *s = str(t).split(":")
    return int(h) * 60 + float(m) + (int(s[0]) / 60 if s else 0.0)


def parse_line(line: str, room: str = DEFAULT_ROOM) -> RoomMessage | None:
    """`kazum: approve A-0001` → RoomMessage. Blank or author-less lines → None."""
    line = line.strip()
    if line.startswith("#") and " " in line:
        room, line = line.split(" ", 1)
    author, sep, text = line.partition(":")
    if not sep or not author.strip() or not text.strip():
        return None
    return RoomMessage(room, author.strip(), text.strip())


class FakeRoom(RoomTransport):
    def __init__(
        self,
        script: list | None = None,
        stdin=None,
        out=None,
        interactive: bool | None = None,
        on_post: Callable[[str, str], None] | None = None,
    ):
        self.posted: list[tuple[str, str]] = []  # (room, text), in order
        self.said = 0  # messages put into the room by say()
        self._script = sorted(
            ((_minutes(t), i, a, r, x) for i, (t, a, r, x) in enumerate(script or [])),
            key=lambda s: (s[0] is not None, s[0] or 0, s[1]),
        )
        self._out = out
        self._on_post = on_post
        self._inbox: asyncio.Queue[RoomMessage | None] = asyncio.Queue()
        self._now: float | None = None
        self._closed = False
        self._stdin = stdin
        self._read_stdin = stdin is not None or (
            script is None if interactive is None else interactive
        )
        self._keep_open = interactive is False and script is None

    # --- RoomTransport ---
    async def post(self, room: str, text: str) -> str:
        self.posted.append((room, text))
        print(f"[{room}] {text}", file=self._out or sys.stdout, flush=True)
        if self._on_post:
            self._on_post(room, text)
        return f"fake-{len(self.posted)}"

    async def messages(self) -> AsyncIterator[RoomMessage]:
        reader = asyncio.create_task(self._stdin_loop()) if self._read_stdin else None
        self._release()
        try:
            while True:
                if self._done():
                    return
                item = await self._inbox.get()
                if item is None:
                    if self._done():
                        return
                    continue
                yield item
        finally:
            if reader:
                reader.cancel()

    async def on_sim_time(self, sim_min: float) -> None:
        self._now = sim_min
        self._release()

    # --- test and harness side ---
    def say(self, author: str, room: str, text: str, echo: bool = False) -> None:
        """A message into the room from someone else; `echo` adds it to the transcript."""
        self.said += 1
        if echo:
            line = f"{author}: {text}"
            self.posted.append((room, line))
            print(f"[{room}] {line}", file=self._out or sys.stdout, flush=True)
        self._inbox.put_nowait(RoomMessage(room, author, text))

    def close(self) -> None:
        self._closed = True
        self._inbox.put_nowait(None)

    def texts(self, room: str | None = None) -> list[str]:
        return [t for r, t in self.posted if room is None or r == room]

    # --- internals ---
    def _done(self) -> bool:
        if not self._inbox.empty():
            return False
        if self._closed:
            return True
        return not self._keep_open and not self._read_stdin and not self._script

    def _release(self) -> None:
        """Queue script lines that are due. With no clock driving the room, all of them are."""
        while self._script:
            t, _i, author, room, text = self._script[0]
            if t is not None and self._now is not None and t > self._now + 1e-9:
                return
            self._script.pop(0)
            self.say(author, room, text)
        self._inbox.put_nowait(None)  # wake messages(): the script may be finished

    async def _stdin_loop(self) -> None:
        src = self._stdin or sys.stdin
        while True:
            try:
                line = await asyncio.to_thread(src.readline)
            except (OSError, ValueError):
                line = ""
            if not line:  # EOF: the room stays usable for scripts, then ends
                self._read_stdin = False
                self._inbox.put_nowait(None)
                return
            msg = parse_line(line)
            if msg:
                self._inbox.put_nowait(msg)
