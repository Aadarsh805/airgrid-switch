"""SwitchRoom: the World Bot as a Switch custom client (always_on, one WebSocket, plus HTTP).

Protocol: docs.switchagents.ai internals/agent-protocol, agent-connection, agent-events. The
argument names of the room ops are not on those pages; they come from the public repo's
operations/definitions.py (L49) and are checked against `GET /ops` at startup.

- One connection: scope=all, filter=all (humans rarely @mention the bot), `rooms=` claims #dispatch
  so document ops have a bound room. Everything else the agent belongs to stays covered.
- Pings are answered by the socket reader, ahead of event processing (6 s lapse).
- Authorship: `sender` is the only thing that maps to a human. Display text never does.

    uv run python -m worldbot.switch_room --who [--seconds 180]   # print sender ids seen in rooms
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import sys
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlencode

import httpx
import websockets
import yaml
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from sim.auth import humans_path
from worldbot import rooms
from worldbot.transport import RoomMessage, RoomTransport, SwitchError

log = logging.getLogger("worldbot.switch")
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "switch.local.yaml"
ROOM_PREFIX = "airgrid:"
TIMEOUT_S = 10
POST_TRIES = 3
BACKOFF_S = 1.0
BACKOFF_MAX_S = 15.0
SEEN_MAX = 2000  # message ids kept to drop replays and the bot's own posts
REQUIRED_OPS = ("list_rooms", "connect_to_room", "create_room_document", "update_room_document")
DOC_ARGS = {
    "create_room_document": {"name", "description", "instructions", "content"},
    "update_room_document": {"document_id", "content"},
}
FATAL_EVICTIONS = ("taken_over", "closed")  # the docs: don't reconnect on these
FATAL_CLOSES = {
    4400: "bad connection parameters",
    4401: "API key rejected",
    4403: "a declared room can't be served",
    4409: "connection refused (taken over, room claimed, or too many connections)",
}


@dataclass(frozen=True)
class SwitchConfig:
    base_url: str
    agent_id: str
    api_key: str
    connection_id: str
    name: str = "airgrid-worldbot"

    @classmethod
    def load(cls, path: Path | None = None) -> SwitchConfig:
        p = path or CONFIG_PATH
        if not p.exists():
            raise SwitchError(
                f"no {p.name}; run scripts/register_worldbot.py (HUMAN_TODO Phase 08)"
            )
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return cls(
                base_url=str(doc["base_url"]).rstrip("/"),
                agent_id=str(doc["agent_id"]),
                api_key=str(doc["api_key"]),
                connection_id=str(doc["connection_id"]),
                name=str(doc.get("name") or "airgrid-worldbot"),
            )
        except (KeyError, TypeError, AttributeError, yaml.YAMLError):
            raise SwitchError(
                f"{p.name} is incomplete; rerun scripts/register_worldbot.py"
            ) from None


def room_key(name: str) -> str:
    """Switch room name → our room: `AirGrid: dispatch`, `dispatch`, `#dispatch` → `#dispatch`."""
    n = name.strip().lower()
    if n.startswith(ROOM_PREFIX):
        n = n[len(ROOM_PREFIX) :].strip()
    return "#" + n.lstrip("#").strip()


def load_senders(path: Path | None = None) -> dict[str, str]:
    """Stable Switch sender id → the human's `discord` name in config/humans.yaml.

    `switch_sender` is one id or a list (Discord and Console are different clients). One id on two
    humans would make approvals ambiguous, so it stops the bot.
    """
    with open(path or humans_path(), encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out: dict[str, str] = {}
    for human in doc.get("humans") or []:
        ids = human.get("switch_sender") or []
        for sender in [ids] if isinstance(ids, str) else ids:
            if out.get(sender, human["discord"]) != human["discord"]:
                raise SwitchError(f"switch_sender {sender} is on two humans in humans.yaml")
            out[sender] = human["discord"]
    return out


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    return str(body.get("detail") or body) if isinstance(body, dict) else str(body)[:200]


class SwitchRoom(RoomTransport):
    def __init__(
        self,
        config: SwitchConfig,
        senders: dict[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        backoff_s: float = BACKOFF_S,
        claim_rooms: bool = True,
        home_room: str = rooms.DISPATCH,
    ):
        self.config = config
        self.senders = {} if senders is None else senders
        self.client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=TIMEOUT_S,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
        self.rooms: dict[str, str] = {}  # our room name → room id
        self.room_names: dict[str, str] = {}  # room id → our room name
        self.ops: dict[str, dict] = {}
        self.cursor: int | None = None
        self.generation = None
        self.post_failures = 0
        self.gaps: list[dict] = []
        self._backoff = backoff_s
        self._home_room = home_room  # the one room document ops bind to
        self._claim_rooms = claim_rooms  # --who shares the agent, so it must not take a room
        self._connected = asyncio.Event()
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._reader: asyncio.Task | None = None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._own_senders: set[str] = set()
        self._own_bodies: OrderedDict[str, None] = OrderedDict()
        self._refused: str | None = None
        name = re.escape(config.name)
        self._self_mention = re.compile(rf"^\s*@{name}\b[:,]?\s*", re.IGNORECASE)

    # ------------------------------------------------------------ startup
    async def start(self, required: tuple[str, ...] = rooms.ROOMS) -> None:
        """Check the bridge, the key, the op surface and the room map. Fails with the reason."""
        await self._check_bridge()
        await self._load_ops()
        listed = await self.op("list_rooms", {})
        self.rooms = {room_key(r["name"]): r["room_id"] for r in listed}
        self.room_names = {v: k for k, v in self.rooms.items()}
        missing = [r for r in required if r not in self.rooms]
        if missing:
            raise SwitchError(
                f"{self.config.name} is not in {', '.join(missing)}; run "
                f"`!invite-agent @{self.config.name}` in each (HUMAN_TODO Phase 08)"
            )

    async def _check_bridge(self) -> None:
        base = self.config.base_url
        try:
            r = await self.client.get("/health")
        except httpx.HTTPError as e:
            raise SwitchError(f"no Switch bridge at {base}: {type(e).__name__}") from None
        if r.status_code == 401:
            raise SwitchError(f"{base} is the right host but the wrong path for the bridge")
        if r.status_code != 200:
            raise SwitchError(f"{base}/health answered HTTP {r.status_code}, not the bridge")

    async def _load_ops(self) -> None:
        try:
            r = await self.client.get(f"/agents/{self.config.agent_id}/ops")
        except httpx.HTTPError as e:
            raise SwitchError(f"GET /ops failed: {type(e).__name__}") from None
        if r.status_code == 401:
            raise SwitchError("Switch rejected the API key; rerun scripts/register_worldbot.py")
        if r.status_code != 200:
            raise SwitchError(f"GET /ops: HTTP {r.status_code}: {_detail(r)}")
        self.ops = r.json().get("operations") or {}
        absent = [op for op in REQUIRED_OPS if op not in self.ops]
        if absent:
            raise SwitchError(f"this Switch server has no op {', '.join(absent)}")
        for op, sent in DOC_ARGS.items():
            schema = self.ops[op].get("input_schema") or {}
            props, needed = schema.get("properties"), set(schema.get("required") or [])
            if props is not None and (sent - set(props) or needed - sent):
                log.error(
                    "%s no longer takes %s; the decision log will fall back", op, sorted(sent)
                )

    # ------------------------------------------------------------ HTTP
    async def op(self, name: str, args: dict, bound: bool = False):
        """An operation. `bound` sends the connection id, which is what resolves the room."""
        headers = {"X-Switch-Connection-Id": self.config.connection_id} if bound else {}
        path = f"/agents/{self.config.agent_id}/ops/{name}"
        try:
            r = await self.client.post(path, json=args, headers=headers)
        except httpx.HTTPError as e:
            raise SwitchError(f"{name}: {type(e).__name__}") from None
        if r.status_code != 200:
            raise SwitchError(f"{name}: HTTP {r.status_code}: {_detail(r)}")
        return r.json().get("result")

    async def send(self, room: str, text: str) -> str:
        """Post or raise SwitchError. Only transport faults and 5xx are retried."""
        room_id = self.rooms.get(room)
        if room_id is None:
            raise SwitchError(f"{self.config.name} is not in {room}")
        reason = "not attempted"
        path = f"/agents/{self.config.agent_id}/message"
        for attempt in range(POST_TRIES):
            if attempt:
                await asyncio.sleep(self._backoff * attempt)
            self._remember_body(text)
            try:
                r = await self.client.post(path, json={"room_id": room_id, "content": text})
            except httpx.HTTPError as e:
                reason = type(e).__name__
                continue
            if r.status_code < 300:
                event_id = str(r.json().get("event_id") or "")
                if event_id:
                    self._mark_seen(event_id)
                return event_id
            reason = f"HTTP {r.status_code}: {_detail(r)}"
            if r.status_code < 500:
                break
        raise SwitchError(f"post to {room} failed: {reason}")

    async def post(self, room: str, text: str) -> str:
        """The bot's loops must outlive a failed post: logged and counted, never raised."""
        try:
            return await self.send(room, text)
        except SwitchError as e:
            self.post_failures += 1
            log.error("%s", e)
            return ""

    # ------------------------------------------------------------ room documents
    async def wait_connected(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            raise SwitchError(f"no live connection after {timeout:g} s") from None

    async def open_document(
        self, room: str, name: str, description: str, instructions: str, content: str
    ) -> tuple[str, str]:
        """Find or create a room document; returns (id, current content). One writer: Switch lets
        only the creator update it, so a same-named document from anyone else is an error."""
        if room not in self.rooms:
            raise SwitchError(f"{self.config.name} is not in {room}")
        args = {"room_id": self.rooms[room], "include_general_instructions": False}
        ctx = await self.op("connect_to_room", args, bound=True) or {}
        for doc in ctx.get("documents") or []:
            if doc.get("name") != name:
                continue
            owner = doc.get("created_by_agent_id")
            if owner and owner != self.config.agent_id:
                raise SwitchError(f'"{name}" in {room} belongs to agent {owner}, not this bot')
            loaded = await self.op("load_internal_documents", {"ids": [doc["id"]]}, bound=True)
            return doc["id"], (loaded[0].get("content") or "") if loaded else ""
        made = await self.op(
            "create_room_document",
            {
                "name": name,
                "description": description,
                "instructions": instructions,
                "content": content,
            },
            bound=True,
        )
        return made["document_id"], content

    async def update_document(self, document_id: str, content: str) -> None:
        """Replaces the content: there is no append op, so the caller keeps the whole text."""
        await self.op(
            "update_room_document", {"document_id": document_id, "content": content}, bound=True
        )

    # ------------------------------------------------------------ the socket
    def _ws_url(self) -> str:
        query: dict[str, str] = {
            "connection_id": self.config.connection_id,
            "scope": "all",
            "filter": "all",
            "client": self.config.name,
            "start_from": "head" if self.cursor is None else str(self.cursor),
        }
        home = self.rooms.get(self._home_room)
        if self._claim_rooms and home:
            query["rooms"] = home
        if self.generation is not None:
            query["expected_generation"] = str(self.generation)
        base = re.sub(r"^http", "ws", self.config.base_url)
        return f"{base}/agents/{self.config.agent_id}/connection/ws?{urlencode(query)}"

    async def _read_loop(self) -> None:
        """Any end of this task reaches `messages()`: a reader that died quietly is a deaf bot."""
        try:
            await self._connect_forever()
        except SwitchError as e:
            self._inbox.put_nowait(e)
        except (ValueError, KeyError, TypeError) as e:  # a frame this client can't read
            self._inbox.put_nowait(SwitchError(f"unreadable frame: {type(e).__name__}: {e}"))
        finally:
            self._connected.clear()

    async def _connect_forever(self) -> None:
        delay = self._backoff
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        while True:
            try:
                async with websockets.connect(
                    self._ws_url(),
                    additional_headers=headers,
                    open_timeout=TIMEOUT_S,
                    ping_interval=None,  # Switch runs its own ping frames
                    max_size=None,
                ) as ws:
                    delay = self._backoff
                    await self._serve(ws)
                reason = "socket closed"
            except ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else None
                self._fatal_close(code)
                reason = f"socket closed ({code})"
            except (OSError, TimeoutError, InvalidHandshake) as e:
                reason = type(e).__name__
            self._connected.clear()
            log.warning("connection lost: %s; reconnecting in %g s", reason, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

    def _fatal_close(self, code: int | None) -> None:
        if code in FATAL_CLOSES:
            detail = f": {self._refused}" if self._refused else ""
            raise SwitchError(f"Switch closed the socket ({code}, {FATAL_CLOSES[code]}){detail}")

    async def _serve(self, ws) -> None:
        async for raw in ws:
            frame = json.loads(raw)
            kind, data = frame.get("event"), frame.get("data") or {}
            if kind == "ping":  # ahead of everything else, always
                await ws.send(json.dumps({"type": "pong", "cursor": self.cursor or 0}))
            elif kind == "connection_state":
                self._on_state(data)
            elif kind == "evicted":
                code, why = data.get("code"), data.get("reason") or ""
                if code in FATAL_EVICTIONS:
                    raise SwitchError(f"evicted by Switch: {code}: {why}; not reconnecting")
                log.warning("evicted: %s %s; resuming from %s", code, why, self.cursor)
                return
            elif kind == "refused":
                self._refused = f"{data.get('status')} {data.get('detail')}"
            elif kind == "gap":
                self._on_gap(data)
            elif kind in ("message", "command", "room_join"):
                self._inbox.put_nowait((frame.get("id"), kind, data))

    def _on_state(self, data: dict) -> None:
        self.generation = data.get("generation")
        cursor = data.get("cursor")
        # 0 counts: a reconnect from `head` would lose what was said while the socket was down
        if isinstance(cursor, int) and (self.cursor is None or cursor > self.cursor):
            self.cursor = cursor
        self._refused = None
        self._connected.set()
        log.info("connected: %d rooms, cursor %s", len(data.get("rooms") or []), self.cursor)

    def _on_gap(self, data: dict) -> None:
        """Events are gone. Approvals in them are lost: a human retypes (12-RUNBOOK)."""
        self.gaps.append(data)
        resumed = data.get("resumed_at")
        if isinstance(resumed, int):
            self.cursor = resumed
        log.error("event gap (%s): anything said in it was not seen", data.get("reason"))

    # ------------------------------------------------------------ incoming
    def _mark_seen(self, message_id: str) -> None:
        self._seen[message_id] = None
        while len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)

    def _remember_body(self, text: str) -> None:
        self._own_bodies[text] = None
        while len(self._own_bodies) > SEEN_MAX:
            self._own_bodies.popitem(last=False)

    def _is_own(self, sender: str, name: str, body: str) -> bool:
        """Switch doesn't document the bot's own sender id, so it is learned: the first echo of a
        text this bot just posted, under its name. A copycat gains nothing: it is only dropped."""
        if sender and sender in self._own_senders:
            return True
        if name == self.config.name and body in self._own_bodies:
            if sender:
                self._own_senders.add(sender)
            return True
        return False

    def _author(self, sender: str, name: str) -> str:
        """Only a configured sender id is a human. An unmapped sender can't take a mapped name:
        the prefix keeps it out of `humans`, and its log line shows the id, not the display text."""
        if sender in self.senders:
            return self.senders[sender]
        agent = name.strip().lower()
        if agent in rooms.ALL_AGENTS:
            return agent
        return f"unmapped:{sender or 'unknown'}"

    def _to_message(self, data: dict) -> RoomMessage | None:
        p = data.get("payload") or {}
        message_id = str(p.get("message_id") or "")
        if message_id and message_id in self._seen:
            return None
        if message_id:
            self._mark_seen(message_id)
        sender, name = str(p.get("sender") or ""), str(p.get("sender_name") or "")
        body = str(p.get("body") or "")
        if not body or self._is_own(sender, name, body):
            return None
        room_id = str(data.get("room_id") or "")
        room = self.room_names.get(room_id)
        if room is None:  # joined after startup: still answerable
            room = self.room_names[room_id] = f"#{room_id}"
            self.rooms[room] = room_id
        text = self._self_mention.sub("", body, count=1)
        return RoomMessage(room, self._author(sender, name), text, sender, name)

    async def messages(self) -> AsyncIterator[RoomMessage]:
        if self._reader is None:
            self._reader = asyncio.create_task(self._read_loop())
        while True:
            item = await self._inbox.get()
            if isinstance(item, SwitchError):
                raise item
            seq, kind, data = item
            msg = self._to_message(data) if kind == "message" else None
            if msg is not None:
                yield msg
            if isinstance(seq, int) and seq > (self.cursor or 0):
                self.cursor = seq  # processed only now, so a resume can't skip it

    async def aclose(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        await self.client.aclose()


async def who(config: SwitchConfig, senders: dict[str, str], seconds: float, out=None) -> int:
    """Print who speaks in the bot's rooms. Its own connection, so a running bot isn't taken over,
    and it claims no room. It never posts."""
    out = out or sys.stdout
    probe = replace(config, connection_id=str(uuid.uuid4()))
    room = SwitchRoom(probe, senders, claim_rooms=False)
    seen: set[str] = set()

    async def listen() -> None:
        async for m in room.messages():
            if m.sender not in seen:
                seen.add(m.sender)
                state = f"mapped to {senders[m.sender]}" if m.sender in senders else "UNMAPPED"
                print(
                    f"{m.room} | {m.sender_name} | switch_sender: {m.sender} | {state}",
                    file=out,
                    flush=True,
                )

    try:
        await room.start(required=())
        print(
            f"Listening {seconds:g} s in {len(room.rooms)} rooms. Say anything in one.",
            file=out,
            flush=True,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(listen(), seconds)
    finally:
        await room.aclose()
    print(f"{len(seen)} sender(s) seen.", file=out, flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worldbot.switch_room")
    ap.add_argument("--who", action="store_true", help="print the sender ids seen in rooms")
    ap.add_argument("--seconds", type=float, default=180)
    args = ap.parse_args(argv)
    if not args.who:
        ap.error("the only command here is --who; the bot is python -m worldbot.bot --switch")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return asyncio.run(who(SwitchConfig.load(), load_senders(), args.seconds))
    except SwitchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
