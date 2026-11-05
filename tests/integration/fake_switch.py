"""A small local Switch for tests: the bridge's HTTP and WebSocket surface, as far as the World Bot
uses it. Shapes follow docs.switchagents.ai (agent-protocol, agent-connection, agent-events) and the
public repo's operations/definitions.py. Runs uvicorn in a thread; `FakeSwitch` is the test's hand.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from tests.integration.harness import free_port

REG_KEY = "reg-key-for-tests"
PING_S = 0.05
SCHEMAS = {
    "list_rooms": {"properties": {"include_archived": {}}, "required": []},
    "connect_to_room": {
        "properties": {"room_id": {}, "include_general_instructions": {}},
        "required": ["room_id"],
    },
    "create_room_document": {
        "properties": {"name": {}, "description": {}, "instructions": {}, "content": {}},
        "required": ["name", "description", "instructions", "content"],
    },
    "update_room_document": {
        "properties": {
            "document_id": {},
            "name": {},
            "description": {},
            "instructions": {},
            "content": {},
        },
        "required": ["document_id"],
    },
    "load_internal_documents": {"properties": {"ids": {}}, "required": ["ids"]},
    "update_agent_detail": {"properties": {"agent_id": {}, "addressing": {}}, "required": []},
}


class FakeSwitch:
    def __init__(self, room_names: tuple[str, ...], agent_id="agent-1", api_key="agent-key"):
        self.agent_id, self.api_key = agent_id, api_key
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.bot_sender = "@switch-agent-bot1:airgrid.local"
        self.rooms = {f"room-{i}": n for i, n in enumerate(room_names)}
        self.events: list[dict] = []  # the agent's buffer; frame ids are 1-based positions
        self.posted: list[tuple[str, str]] = []  # (room id, content)
        self.docs: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.opens: list[dict] = []  # the query of each socket open
        self.pongs: list[dict] = []
        self.generation = 0
        self.fail_docs = False
        self.fail_posts = 0  # next n posts answer 503
        self.answer_pings = True
        self.registered: dict[str, dict] = {}
        self.addressing: str | None = None
        self._commands: list[dict] = []  # control frames to write, for the live socket
        self._live: tuple | None = None  # (query, room claims)
        self._lock = threading.Lock()
        self.app = self._build()
        self._server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    # ------------------------------------------------------------ the test's hand
    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    def room_id(self, name: str) -> str:
        return next(k for k, v in self.rooms.items() if v == name)

    def say(self, room: str, sender: str, sender_name: str, body: str, addressed=False) -> None:
        self._event(self.room_id(room), sender, sender_name, body, addressed)

    def _event(self, room_id, sender, sender_name, body, addressed) -> None:
        with self._lock:
            seq = len(self.events) + 1
            self.events.append(
                {
                    "event": "message",
                    "id": seq,
                    "data": {
                        "type": "message",
                        "room_id": room_id,
                        "bridge_id": None,
                        "channel_type": "channel_public",
                        "sequence": seq,
                        "payload": {
                            "addressed": addressed,
                            "sender": sender,
                            "sender_name": sender_name,
                            "message_id": f"$ev-{seq}",
                            "body": body,
                            "timestamp": 1,
                            "thread_id": None,
                            "attachments": [],
                        },
                    },
                }
            )

    def control(self, event: str, close: int | None = None, **data) -> None:
        """Write a control frame on the live socket, then optionally close with `close`."""
        with self._lock:
            self._commands.append({"frame": {"event": event, "data": data}, "close": close})

    def drop_socket(self, code: int = 1011) -> None:
        with self._lock:
            self._commands.append({"frame": None, "close": code})

    # ------------------------------------------------------------ the server
    def _auth(self, authorization: str | None) -> None:
        if authorization != f"Bearer {self.api_key}":
            raise HTTPException(401, "invalid API key")

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/health")
        def health():
            return {"status": "ok"}

        @app.post("/agents")
        def register(body: dict, authorization: str | None = Header(None)):
            if authorization != f"Bearer {REG_KEY}":
                raise HTTPException(401, "invalid registration token")
            name = body["name"]
            if name in self.registered and not body.get("overwrite"):
                raise HTTPException(409, "already registered")
            profile = body["integration_profile"]
            for key in ("pre_invocation_mediation", "post_invocation_mediation", "event_reporting"):
                assert profile[key] == []
            self.registered[name] = body
            self.api_key = f"agent-key-{len(self.registered)}-{uuid.uuid4().hex[:6]}"
            return {"id": self.agent_id, "api_key": self.api_key}

        @app.get("/agents/{agent_id}/ops")
        def ops(agent_id: str, authorization: str | None = Header(None)):
            self._auth(authorization)
            return {
                "operations": {k: {"description": k, "input_schema": v} for k, v in SCHEMAS.items()}
            }

        @app.post("/agents/{agent_id}/message")
        async def message(
            agent_id: str, request: Request, authorization: str | None = Header(None)
        ):
            self._auth(authorization)
            body = await request.json()
            if self.fail_posts > 0:
                self.fail_posts -= 1
                raise HTTPException(503, "bridge restarting")
            if body["room_id"] not in self.rooms:
                raise HTTPException(404, "unknown room")
            self.posted.append((body["room_id"], body["content"]))
            self._event(
                body["room_id"], self.bot_sender, "airgrid-worldbot", body["content"], False
            )
            return {"ok": True, "event_id": f"$ev-{len(self.events)}"}

        @app.post("/agents/{agent_id}/ops/{op}")
        async def run_op(
            op: str,
            agent_id: str,
            request: Request,
            authorization: str | None = Header(None),
            x_switch_connection_id: str | None = Header(None),
        ):
            self._auth(authorization)
            args = await request.json()
            self.calls.append((op, args))
            schema = SCHEMAS.get(op)
            if schema is None:
                raise HTTPException(404, f"no operation {op}")
            extra = set(args) - set(schema["properties"])
            missing = set(schema["required"]) - set(args)
            if extra or missing:
                return JSONResponse({"detail": f"bad arguments {extra or missing}"}, 400)
            try:
                return {"result": self._op(op, args, x_switch_connection_id)}
            except PermissionError as e:
                return JSONResponse({"detail": str(e)}, 403)
            except ValueError as e:
                return JSONResponse({"detail": str(e)}, 400)

        @app.websocket("/agents/{agent_id}/connection/ws")
        async def ws(websocket: WebSocket, agent_id: str):
            await self._socket(websocket)

        return app

    def _bound_room(self, connection_id: str | None) -> str:
        """Document ops work on the connection's one claimed room (context.py)."""
        live = self._live
        if connection_id is None or live is None or live[0].get("connection_id") != connection_id:
            raise ValueError("Not connected to a room. Call connect_to_room first.")
        if len(live[1]) != 1:
            raise ValueError("This connection covers several rooms; the operation needs one")
        return next(iter(live[1]))

    def _op(self, op: str, args: dict, connection_id: str | None):
        if op == "list_rooms":
            return [
                {"room_id": k, "name": v, "description": "", "connected": False, "archived": False}
                for k, v in self.rooms.items()
            ]
        if op == "update_agent_detail":
            self.addressing = args.get("addressing")
            return {"ok": True}
        if op == "connect_to_room":
            if self._live and self._live[0].get("connection_id") == connection_id:
                self._live[1].clear()
                self._live[1].add(args["room_id"])
            docs = [
                {"id": k, "name": d["name"], "created_by_agent_id": d["owner"]}
                for k, d in self.docs.items()
                if d["room_id"] == args["room_id"]
            ]
            return {"room_id": args["room_id"], "documents": docs, "warning": None}
        room_id = self._bound_room(connection_id)
        if self.fail_docs:
            raise PermissionError("document store refused")
        if op == "create_room_document":
            doc_id = f"doc-{len(self.docs) + 1}"
            self.docs[doc_id] = {**args, "room_id": room_id, "owner": self.agent_id}
            return {"document_id": doc_id}
        if op == "update_room_document":
            doc = self.docs.get(args["document_id"])
            if doc is None or doc["owner"] != self.agent_id:
                raise PermissionError("only the creating agent can update it")
            doc.update({k: v for k, v in args.items() if k != "document_id"})
            return {"document_id": args["document_id"]}
        if op == "load_internal_documents":
            return [
                {"id": i, "description": "", "content": self.docs[i]["content"]}
                for i in args["ids"]
            ]
        raise ValueError(op)

    async def _socket(self, websocket: WebSocket) -> None:
        query = dict(websocket.query_params)
        if websocket.headers.get("authorization") != f"Bearer {self.api_key}":
            await websocket.accept()
            await websocket.close(4401)
            return
        await websocket.accept()
        self.opens.append(query)
        self.generation += 1
        start = query.get("start_from", "head")
        with self._lock:
            sent = len(self.events) if start == "head" else int(start)
        claims = {r for r in query.get("rooms", "").split(",") if r}
        self._live = (query, claims)
        await websocket.send_json(
            {
                "event": "connection_state",
                "data": {
                    "connection_id": query.get("connection_id"),
                    "agent_id": self.agent_id,
                    "generation": self.generation,
                    "scope": query.get("scope"),
                    "filter": query.get("filter"),
                    "rooms": sorted(self.rooms),
                    "cursor": sent,
                    "heartbeat_interval_seconds": PING_S,
                },
            }
        )
        pump = asyncio.create_task(self._pongs(websocket))
        loop = asyncio.get_running_loop()
        next_ping = loop.time()
        try:
            while True:
                with self._lock:
                    fresh = self.events[sent:]
                    commands, self._commands = self._commands, []
                for frame in fresh:
                    await websocket.send_json(frame)
                sent += len(fresh)
                for c in commands:
                    if c["frame"]:
                        await websocket.send_json(c["frame"])
                    if c["close"] is not None:
                        await websocket.close(c["close"])
                        return
                if loop.time() >= next_ping:
                    await websocket.send_json({"event": "ping", "data": {}})
                    next_ping = loop.time() + PING_S
                await asyncio.sleep(0.01)
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            pump.cancel()

    async def _pongs(self, websocket: WebSocket) -> None:
        try:
            while True:
                frame = await websocket.receive_json()
                if self.answer_pings:
                    self.pongs.append(frame)
        except (WebSocketDisconnect, RuntimeError):
            return
