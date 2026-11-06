"""World Bot main loop (plain code, no LLM).

Sim → rooms: event alerts, simulated lines, approval cards, with an @mention of the owner.
Rooms → sim: a mapped human's `approve/reject A-xxxx` and `take-control/release-control D-xx`,
sent with that human's token. It is the only approval path and agents have none. Tokens are
held in memory only and never logged or posted.

    python -m worldbot.bot --fake-room [--api http://localhost:8740]
    python -m worldbot.bot --switch    [--api http://localhost:8740]   # config/switch.local.yaml
    add --profile game or --profile city for the quiet Discords (game_profile.py, city_profile.py)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
from dataclasses import replace

import httpx
import yaml

from sim.auth import load_humans, roles_config, tokens_path
from worldbot import cards, city_profile, lines, rooms
from worldbot.cards import Command
from worldbot.city_profile import CityProfile, load_lead
from worldbot.decisions_sink import DecisionSink, FakeSink, SwitchSink
from worldbot.fake_room import FakeRoom
from worldbot.game_profile import GameProfile
from worldbot.switch_room import SwitchConfig, SwitchRoom, load_senders
from worldbot.transport import RoomMessage, RoomTransport, SwitchError

log = logging.getLogger("worldbot")
DEFAULT_API = "http://localhost:8740"
RECONNECT_S = 1.0
TIMEOUT_S = 10
STOP_TRIES = 5
STOP_WAIT_S = 1.0


def load_human_tokens(path=None) -> dict[str, str]:
    p = path or tokens_path()
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        return {str(k): str(v) for k, v in (yaml.safe_load(fh).get("humans") or {}).items() if v}


def approval_roles(cfg: dict | None = None) -> dict[str, str]:
    """Approval type → the human role that decides it (config/roles.yaml)."""
    cfg = cfg or roles_config()
    return {t: role for role, types in cfg["approvals"].items() for t in types}


class WorldBot:
    def __init__(
        self,
        transport: RoomTransport,
        api_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        humans: dict[str, tuple[str, ...]] | None = None,
        tokens: dict[str, str] | None = None,
        sink: DecisionSink | None = None,
        approval_role: dict[str, str] | None = None,
        profile: GameProfile | CityProfile | None = None,
        operator_key: str | None = None,
    ):
        self.transport = transport
        self.api = (api_url or os.environ.get("AIRGRID_API_URL") or DEFAULT_API).rstrip("/")
        self.client = client or httpx.AsyncClient(base_url=self.api, timeout=TIMEOUT_S)
        self.humans = load_humans() if humans is None else humans
        self.tokens = load_human_tokens() if tokens is None else tokens
        self.sink = sink or FakeSink()
        self.approval_role = approval_role or approval_roles()
        self.profile = profile
        # City decisions need the operator key. In memory only, never logged or posted.
        self.operator_key = operator_key or os.environ.get("AIRGRID_GAME_KEY") or ""
        self._synced = False  # game profile: history seen, never reposted
        self._held: list[dict] = []  # city decisions, until the incident's report
        self.last_seq = 0
        self.handled = 0  # room messages fully processed
        self.cards: dict[str, dict] = {}  # approval id → approval view, once carded
        self.pilots: dict[str, str] | None = None
        self.drone_incident: dict[str, str] = {}
        self.sim_min = 0.0
        self._stop = asyncio.Event()

    # ------------------------------------------------------------ sim → rooms
    async def _simulated_pilots(self) -> dict[str, str]:
        if self.pilots is None:
            r = await self.client.get("/pilots")
            self.pilots = {
                p["id"]: lines.duty_pilot_name(p["name"])
                for p in r.json().get("pilots", [])
                if p["kind"] == "simulated"
            }
        return self.pilots

    async def handle_event(self, event: dict) -> None:
        """One log event: alert + simulated lines. Replays (reconnect) are dropped by seq."""
        seq = event.get("seq", self.last_seq + 1)
        if seq <= self.last_seq:
            return
        self.last_seq = seq
        t = event.get("t")
        await self._time(max(t, self.sim_min) if isinstance(t, int | float) else None)
        if self.profile is not None:
            await self._game_event(event)
            return
        route = rooms.route(self._with_incident(event))
        if route.beat:  # before the mention, so the owner's reply can't arrive first
            await self.report_activity(route.agent, "busy")
        mention = event.get("type") not in rooms.QUIET_EVENTS
        alert = cards.event_alert(event, route.room, route.agent, mention)
        await self.transport.post(route.room, alert)
        for line in lines.simulated_lines(event, await self._simulated_pilots()):
            await self.transport.post(route.room, line)

    async def _game_event(self, event: dict) -> None:
        moment = self.profile.moment(event)
        if moment is None:
            return
        known = getattr(self.transport, "rooms", None)
        if moment.room in getattr(self.profile, "optional_rooms", ()) and isinstance(known, dict):
            if moment.room not in known:  # e.g. no #fleet in this server: skip, don't fail
                return
        if moment.beat:
            await self.report_activity(moment.agent, "busy")
        await self.transport.post(moment.room, self.profile.line(event, moment))
        if moment.ends and self.profile.decides:
            self._log_incident(event)

    def _approval_room(self, type_: str) -> str:
        return self.profile.home_room if self.profile else rooms.approval_room(type_)

    def _with_incident(self, event: dict) -> dict:
        """Some payloads name only the drone (`reacquire`): use the incident it was last seen on.
        Earlier events are the source, so a replay routes the same as the live run."""
        p = event.get("payload") or {}
        if p.get("drone") and p.get("incident"):
            self.drone_incident[p["drone"]] = p["incident"]
        if not rooms.needs_incident(event) or p["drone"] not in self.drone_incident:
            return event
        return {**event, "payload": {**p, "incident": self.drone_incident[p["drone"]]}}

    async def handle_approval(self, approval: dict) -> None:
        """A new approval: its card, once."""
        aid = approval["id"]
        if aid in self.cards:
            return
        self.cards[aid] = approval
        if approval.get("status", "pending") != "pending":
            return
        needs = self.approval_role.get(approval["type"], "watch_commander")
        room = self._approval_room(approval["type"])
        await self.transport.post(room, cards.approval_card(approval, needs))

    async def report_activity(self, agent: str, state: str) -> None:
        """Best effort: a lost report only means the hold runs to its 45 s limit."""
        try:
            await self.client.post("/control/agent-activity", json={"agent": agent, "state": state})
        except httpx.HTTPError as e:
            log.warning("agent-activity not reported: %s", type(e).__name__)

    async def _time(self, sim_min) -> None:
        if isinstance(sim_min, int | float):
            self.sim_min = sim_min
            await self.transport.on_sim_time(sim_min)

    # ------------------------------------------------------------ rooms → sim
    async def handle_message(self, msg: RoomMessage) -> None:
        try:
            if msg.author in rooms.ALL_AGENTS:
                await self.report_activity(msg.author, "idle")
            if self.profile is not None and self.profile.decides:
                order = cards.parse_order(msg.text)
                if order is not None and msg.room in self.profile.rooms:
                    await self._city_order(msg, order)
                    return
                cmd = cards.parse_city_command(msg.text)
                if cmd is not None and msg.room in self.profile.rooms:
                    await self._city_decide(msg, cmd)
                return
            cmd = cards.parse_command(msg.text)
            if cmd is not None:
                await self._command(msg, cmd)
        finally:
            self.handled += 1

    async def _command(self, msg: RoomMessage, cmd: Command) -> None:
        roles = self.humans.get(msg.author)
        if msg.author in rooms.ALL_AGENTS or roles is None:
            who = "an agent" if msg.author in rooms.ALL_AGENTS else "an unmapped user"
            await self._refuse(msg, cmd, f"{who} cannot do this; a mapped human must")
            return
        token = self.tokens.get(msg.author)
        if not token:
            await self._refuse(msg, cmd, f"no token for {msg.author}; run scripts/make_tokens.sh")
            return
        if cmd.verb in ("approve", "reject"):
            await self._decide(msg, cmd, roles, token)
        else:
            await self._control(msg, cmd, roles, token)

    async def _city_decide(self, msg: RoomMessage, cmd: Command) -> None:
        """A decision typed in #ops or the incident's room, the only place one is made. Only the
        mapped incident lead counts, and no @mention is needed. A word that means no open call is
        ordinary chat. Answered with one short line, then the sim's own report."""
        iid, unclear = self.profile.resolve(msg.room, cmd.target, cmd.verb)
        if iid is None and not unclear:
            if msg.author not in rooms.ALL_AGENTS and self.humans.get(msg.author):
                await self.transport.post(msg.room, "Nothing is waiting on you right now.")
            return
        minutes = cmd.reason
        name = self.profile.open[iid]["name"] if iid else cmd.target
        cmd = replace(cmd, target=name, reason="")
        roles = self.humans.get(msg.author)
        if msg.author in rooms.ALL_AGENTS or roles is None:
            who = "an agent" if msg.author in rooms.ALL_AGENTS else "an unmapped user"
            await self._refuse(msg, cmd, f"{who} cannot do this; a mapped human must")
            return
        needs = city_profile.CALLS[cmd.verb][1]
        if needs not in roles:
            await self._refuse(msg, cmd, f"needs {needs}; ask the person who holds it")
            return
        if not self.operator_key:
            await self._refuse(msg, cmd, "no operator key here; restart with run_game.sh")
            return
        if unclear:
            await self.transport.post(msg.room, self.profile.which(cmd.verb))
            return
        body = {"incident": iid, "choice": cmd.verb}
        headers = {"X-Game-Key": self.operator_key}
        was_open = iid in self.profile.open
        room = self.profile.open[iid]["room"] if was_open else msg.room
        name_line = self.profile.confirm(iid, cmd.verb, minutes)[1]
        if await self._sim(msg, cmd, "/city/decide", body, headers=headers) is None:
            if iid in self.profile.open:  # refused: the call is still open
                self.profile.open[iid]["asking"] = True
            return
        line = name_line
        self._record("decided", msg, cmd, detail=cmd.verb)
        await self.transport.post(room, line)

    async def _city_order(self, msg: RoomMessage, cmd: Command) -> None:
        """`watch market`, `dock D-04`... from the mapped Watch Commander only (L67). The sim
        validates and says what happened; the bot only carries it."""
        if cmd.verb in ("watch", "unwatch"):  # "watch out!" is chat, not an order
            first = cmd.target.split()[0].lower()
            names = " ".join(n.lower() for n, _at in self.profile.places)
            if first not in names.split():
                return
        roles = self.humans.get(msg.author)
        if msg.author in rooms.ALL_AGENTS or roles is None:
            who = "an agent" if msg.author in rooms.ALL_AGENTS else "an unmapped user"
            await self._refuse(msg, cmd, f"{who} cannot do this; a mapped human must")
            return
        if cards.ORDER_ROLE not in roles:
            await self._refuse(msg, cmd, f"needs {cards.ORDER_ROLE}; ask the person who holds it")
            return
        if not self.operator_key:
            await self._refuse(msg, cmd, "no operator key here; restart with run_game.sh")
            return
        body: dict = {"order": cmd.verb}
        if cmd.verb in ("dock", "release"):
            body["drone"] = cmd.target
        else:
            body["place"] = cmd.target
        if cmd.reason:
            body["minutes"] = float(cmd.reason)
        headers = {"X-Game-Key": self.operator_key}
        out = await self._sim(msg, cmd, "/city/order", body, headers=headers)
        if out is None:
            return
        self._record("ordered", msg, cmd, detail=out.get("text", ""))
        await self.transport.post(msg.room, f"✅ {out.get('text', 'Done.')}")

    def _log_incident(self, event: dict) -> None:
        """The flight authorisation log gets one entry per incident, at its report."""
        r = (event.get("payload") or {}).get("report") or {}
        name = r.get("name", "")
        mine = [e for e in self._held if e["target"] == name or not e["target"]]
        self._held = [e for e in self._held if e not in mine]
        said = " · ".join(f"{e['action']} by {e['actor']}" for e in mine)
        self.sink.record(
            {
                "kind": "incident",
                "sim_min": self.sim_min,
                "room": self.profile.home_room,
                "actor": "World Bot",
                "action": name,
                "target": r.get("incident", ""),
                "code": None,
                "detail": f"response {r.get('response_s')} s · {said or 'no decision typed'}",
            }
        )

    async def _refuse(self, msg: RoomMessage, cmd: Command, instead: str) -> None:
        """No sim call happens here: the bot stops it before the sim."""
        await self.transport.post(
            msg.room, cards.refusal_line(cmd.label, "FORBIDDEN_ROLE", instead)
        )
        self._record("refusal", msg, cmd, code="FORBIDDEN_ROLE", detail=instead)

    def _record(self, kind: str, msg: RoomMessage, cmd: Command, code=None, detail="") -> None:
        record = self.sink.record
        if self.profile is not None and self.profile.decides:
            record = self._held.append  # one log write per incident, at its report
        record(
            {
                "kind": kind,
                "sim_min": self.sim_min,
                "room": msg.room,
                "actor": msg.author,
                "action": cmd.verb,
                "target": cmd.target,
                "code": code,
                "detail": detail,
            }
        )

    async def _approval(self, aid: str) -> dict | None:
        if aid not in self.cards:
            with contextlib.suppress(httpx.HTTPError, ValueError):
                r = await self.client.get("/approvals")
                for a in r.json().get("approvals", []):
                    self.cards.setdefault(a["id"], a)
        return self.cards.get(aid)

    async def _decide(self, msg: RoomMessage, cmd: Command, roles, token: str) -> None:
        if cmd.verb == "reject" and not cmd.reason:
            await self.transport.post(msg.room, f"⚠ usage: reject {cmd.target} <reason>")
            return
        approval = await self._approval(cmd.target)
        if approval is not None:  # unknown ids go on to the sim, which answers NOT_FOUND
            needs = self.approval_role.get(approval["type"])
            if needs not in roles:
                await self._refuse(msg, cmd, f"needs {needs}; ask a human who holds it")
                return
        body = {"reason": cmd.reason} if cmd.verb == "reject" else {}
        path = f"/approvals/{cmd.target}/{cmd.verb}"
        out = await self._sim(msg, cmd, path, body, token)
        if out is None:
            return
        room = self._approval_room(approval["type"]) if approval else msg.room
        if cmd.verb == "approve":
            result = out.get("result", {})
            text = cards.approved_line(
                cmd.target, msg.author, f"{result.get('type')} {result.get('status')}"
            )
            self._record("approved", msg, cmd, detail=str(result.get("type")))
        else:
            text = cards.rejected_line(cmd.target, msg.author, cmd.reason)
            self._record("rejected", msg, cmd, detail=cmd.reason)
        await self.transport.post(room, text)

    async def _control(self, msg: RoomMessage, cmd: Command, roles, token: str) -> None:
        needs = cards.HUMAN_ACTION_ROLE[cmd.verb]
        if needs not in roles:
            await self._refuse(msg, cmd, f"needs {needs}; ask a human who holds it")
            return
        body = {"role": needs, "action": cmd.verb, "args": {"drone": cmd.target}}
        out = await self._sim(msg, cmd, "/actions", body, token)
        if out is None:
            return
        result = out.get("result", {})
        load = f"load {result['load']}" if result.get("load") else "done"
        await self.transport.post(msg.room, cards.control_line(cmd, msg.author, load))
        self._record("control", msg, cmd, detail=load)

    async def _sim(
        self, msg: RoomMessage, cmd: Command, path, body, token: str = "", headers=None
    ) -> dict | None:
        """POST with the human's token. Returns the body on success; posts the outcome if not."""
        headers = headers or {"Authorization": f"Bearer {token}"}
        try:
            r = await self.client.post(path, json=body, headers=headers)
            out = r.json()
        except (httpx.HTTPError, ValueError) as e:
            await self.transport.post(msg.room, cards.error_line(cmd.label, type(e).__name__))
            self._record("error", msg, cmd, detail=type(e).__name__)
            return None
        if r.status_code < 300:
            return out
        code = out.get("code")
        if code:
            instead = out.get("hint") or "escalated to the Watch Commander"
            await self.transport.post(msg.room, cards.refusal_line(cmd.label, code, instead))
            self._record("refusal", msg, cmd, code=code, detail=out.get("reason", ""))
        else:
            reason = out.get("reason") or f"HTTP {r.status_code}"
            await self.transport.post(msg.room, cards.error_line(cmd.label, reason))
            self._record("error", msg, cmd, detail=reason)
        return None

    # ------------------------------------------------------------ loops
    async def catch_up(self) -> None:
        """After a (re)connect: anything missed, from the sim's own lists. A lower last seq means
        the sim was reset, so the run starts over."""
        events = (await self.client.get("/events")).json().get("events", [])
        if (events[-1]["seq"] if events else 0) < self.last_seq:
            self.last_seq, self.sim_min = 0, 0.0
            self.drone_incident.clear()
            self.cards.clear()
            if self.profile is not None:
                self.profile.reset()
        if self.profile is not None and not self._synced:
            self._synced = True  # a bot joining late stays quiet about what already happened
            for e in events:
                self.profile.moment(e)
            self.last_seq = events[-1]["seq"] if events else 0
        for e in events:
            await self.handle_event(e)
        pending = (await self.client.get("/approvals", params={"status": "pending"})).json()
        for a in pending.get("approvals", []):
            await self.handle_approval(a)

    async def _dispatch(self, kind: str, data: str) -> None:
        if kind == "snapshot":
            await self.catch_up()
            await self._time(json.loads(data).get("t"))
        elif kind == "event":
            await self.handle_event(json.loads(data))
        elif kind == "approval":
            await self.handle_approval(json.loads(data))
        elif kind == "tick":
            await self._time(json.loads(data).get("t"))

    async def _stream_once(self) -> None:
        kind, data = None, []
        async with self.client.stream("GET", "/events/stream", timeout=None) as r:
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and kind:
                    await self._dispatch(kind, "\n".join(data))
                    kind, data = None, []

    async def stream_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._stream_once()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("event stream lost: %s", type(e).__name__)
            await asyncio.sleep(RECONNECT_S)

    async def message_loop(self) -> None:
        with contextlib.suppress(httpx.HTTPError, ValueError, KeyError):
            await self._time((await self.client.get("/status")).json()["t"])
        async for msg in self.transport.messages():
            await self.handle_message(msg)

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.stream_loop()), asyncio.create_task(self.message_loop())]
        fatal: list[BaseException] = []

        def watch(task: asyncio.Task) -> None:  # a dead loop must not leave a half-deaf bot running
            if not task.cancelled() and task.exception():
                fatal.append(task.exception())
                self._stop.set()

        for t in tasks:
            t.add_done_callback(watch)
        await self._stop.wait()
        # A cancel can be lost inside the idle SSE read, so one isn't enough to stop the stream.
        for _ in range(STOP_TRIES):
            pending = [t for t in tasks if not t.done()]
            if not pending:
                break
            for t in pending:
                t.cancel()
            await asyncio.wait(pending, timeout=STOP_WAIT_S)
        if fatal:
            raise fatal[0]

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        await self.client.aclose()


async def run_switch(api: str | None, profile=None) -> None:
    home = profile.home_room if profile else rooms.DISPATCH
    room = SwitchRoom(SwitchConfig.load(), load_senders(), home_room=home)
    try:
        await room.start(profile.rooms if profile else rooms.ROOMS)
        if not room.senders:
            log.warning("no switch_sender in humans.yaml: every approval will be refused")
        # City: the report is already in the room, so a failed log write isn't posted again.
        sink = SwitchSink(
            room, room_name=home, post_fallback=not getattr(profile, "decides", False)
        )
        bot = WorldBot(room, api, sink=sink, profile=profile)
        try:
            await bot.run()
        finally:
            await bot.aclose()
    finally:
        await room.aclose()


async def run_fake(api: str | None, profile=None) -> None:
    bot = WorldBot(FakeRoom(), api, profile=profile)
    try:
        await bot.run()
    finally:
        await bot.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m worldbot.bot")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fake-room", action="store_true", help="terminal room, stdin approvals")
    mode.add_argument("--switch", action="store_true", help="live rooms (register_worldbot first)")
    ap.add_argument("--api", default=None)
    ap.add_argument("--profile", choices=("full", "game", "city"), default="full")
    args = ap.parse_args(argv)
    profile = {
        "game": GameProfile,
        "city": lambda: CityProfile(load_lead(), load_lead(role=city_profile.DOCTOR)),
    }.get(args.profile)
    profile = profile() if profile else None
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        with contextlib.suppress(KeyboardInterrupt):
            run = run_fake if args.fake_room else run_switch
            asyncio.run(run(args.api, profile))
    except SwitchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
