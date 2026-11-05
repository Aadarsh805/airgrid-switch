"""Phase 08: SwitchRoom, SwitchSink and the registration script against fake_switch.py.
No live Switch and no sim: the sim's HTTP side is an httpx MockTransport."""

import asyncio
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable

import httpx
import pytest
import yaml

from tests.integration.fake_switch import REG_KEY, FakeSwitch
from tests.integration.harness import base_env, dead_url
from tests.sim.helpers import ROOT, TOKENS_DOC
from tests.sim.p3 import approval_id, load, propose
from worldbot import decisions_sink, rooms
from worldbot.bot import WorldBot
from worldbot.decisions_sink import DOC_NAME, SwitchSink, summary_line
from worldbot.switch_room import (
    SwitchConfig,
    SwitchError,
    SwitchRoom,
    load_senders,
    room_key,
    who,
)

KAZUM = "@switch-user-kazum01:airgrid.local"
STRANGER = "@switch-user-other99:airgrid.local"
AGENT = "@switch-agent-disp01:airgrid.local"
ROOM_NAMES = (*(f"AirGrid: {r.lstrip('#')}" for r in rooms.ROOMS), "AirGrid: test-room")
ROLES = ("watch_commander", "remote_pilot", "field_officer")


@pytest.fixture
def switch():
    fake = FakeSwitch(ROOM_NAMES)
    fake.start()
    yield fake
    fake.stop()


def config(fake: FakeSwitch, **over) -> SwitchConfig:
    return SwitchConfig(fake.url, fake.agent_id, fake.api_key, "conn-1", **over)


async def until(check: Callable[[], bool], timeout=5.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


async def next_message(room: SwitchRoom, it, timeout=5.0):
    return await asyncio.wait_for(anext(it), timeout)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 30))


class Session:
    """A started SwitchRoom with its message iterator."""

    def __init__(self, fake, senders=None, **kwargs):
        self.fake = fake
        self.room = SwitchRoom(config(fake), senders or {KAZUM: "kazum"}, backoff_s=0.01, **kwargs)

    async def __aenter__(self):
        await self.room.start()
        self.it = self.room.messages()
        self.first = asyncio.ensure_future(anext(self.it))
        await until(lambda: self.room._connected.is_set())
        return self

    async def get(self):
        if self.first is not None:
            task, self.first = self.first, None
            return await asyncio.wait_for(task, 5)
        return await next_message(self.room, self.it)

    async def __aexit__(self, *exc):
        if self.first is not None:
            self.first.cancel()
        await self.room.aclose()


# ---------------------------------------------------------------- startup
def test_room_names_map_to_ours():
    assert room_key("AirGrid: dispatch") == "#dispatch"
    assert room_key("airgrid:field-units") == "#field-units"
    assert room_key("#incident-1") == "#incident-1"


def test_start_maps_every_room(switch):
    async def go():
        room = SwitchRoom(config(switch))
        await room.start()
        assert set(rooms.ROOMS) <= set(room.rooms)
        assert room.rooms["#dispatch"] == switch.room_id("AirGrid: dispatch")
        await room.aclose()

    run(go())


def test_a_room_the_bot_is_not_in_is_named():
    fake = FakeSwitch(ROOM_NAMES[:-2])
    fake.start()

    async def go():
        room = SwitchRoom(config(fake))
        with pytest.raises(SwitchError, match=r"not in #shift-report.*!invite-agent"):
            await room.start()
        await room.aclose()

    try:
        run(go())
    finally:
        fake.stop()


def test_honest_failures_nothing_listening_and_a_bad_key(switch):
    async def go():
        dead = SwitchRoom(SwitchConfig(dead_url(), "a", "k", "c"))
        with pytest.raises(SwitchError, match="no Switch bridge at"):
            await dead.start()
        await dead.aclose()
        bad = SwitchRoom(SwitchConfig(switch.url, "a", "wrong-key", "c"))
        with pytest.raises(SwitchError, match="rejected the API key") as e:
            await bad.start()
        assert "wrong-key" not in str(e.value)
        await bad.aclose()

    run(go())


def test_config_missing_says_how_to_fix(tmp_path):
    with pytest.raises(SwitchError, match="register_worldbot"):
        SwitchConfig.load(tmp_path / "nope.yaml")


# ---------------------------------------------------------------- post and receive
def test_post_lands_in_the_mapped_room_and_needs_no_socket(switch):
    async def go():
        room = SwitchRoom(config(switch))
        await room.start()
        event_id = await room.post("#airspace", "🧭 hello")
        await room.aclose()
        return event_id

    assert run(go()).startswith("$ev-")
    assert switch.posted == [(switch.room_id("AirGrid: airspace"), "🧭 hello")]
    assert switch.opens == []


def test_post_retries_a_503_then_succeeds(switch):
    switch.fail_posts = 2

    async def go():
        async with Session(switch) as s:
            s.room._backoff = 0
            assert await s.room.post("#dispatch", "x")

    run(go())
    assert len(switch.posted) == 1


def test_failed_post_is_counted_with_the_reason_never_raised(switch, caplog):
    async def go():
        room = SwitchRoom(config(switch), backoff_s=0)
        await room.start()
        switch.fail_posts = 99
        with caplog.at_level(logging.ERROR, "worldbot.switch"):
            assert await room.post("#dispatch", "x") == ""
        assert await room.post("#nowhere", "x") == ""
        assert room.post_failures == 2
        await room.aclose()

    run(go())
    assert "HTTP 503: bridge restarting" in caplog.text


def test_connection_params_follow_the_protocol(switch):
    async def go():
        async with Session(switch):
            pass

    run(go())
    q = switch.opens[0]
    assert (q["scope"], q["filter"], q["connection_id"]) == ("all", "all", "conn-1")
    assert q["rooms"] == switch.room_id("AirGrid: dispatch")
    assert q["start_from"] == "head"


def test_unaddressed_human_message_arrives(switch):
    async def go():
        async with Session(switch) as s:
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "approve A-0001", addressed=False)
            m = await s.get()
            assert (m.room, m.author, m.text, m.sender) == (
                "#dispatch",
                "kazum",
                "approve A-0001",
                KAZUM,
            )

    run(go())


def test_own_messages_are_skipped_and_a_copycat_gains_nothing(switch):
    async def go():
        async with Session(switch) as s:
            await s.room.post("#dispatch", "🟡 APPROVAL A-0001")
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "after")
            assert (await s.get()).text == "after"
            assert switch.bot_sender in s.room._own_senders
            await s.room.post("#dispatch", "second")
            switch.say("AirGrid: airspace", switch.bot_sender, "renamed", "any text")
            switch.say("AirGrid: dispatch", STRANGER, "airgrid-worldbot", "not a copy")
            m = await s.get()
            assert (m.text, m.author) == ("not a copy", f"unmapped:{STRANGER}")

    run(go())


def test_leading_mention_of_the_bot_is_dropped(switch):
    async def go():
        async with Session(switch) as s:
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "@airgrid-worldbot approve A-0002")
            assert (await s.get()).text == "approve A-0002"

    run(go())


# ---------------------------------------------------------------- liveness and resume
def test_every_ping_gets_a_pong_with_the_cursor(switch):
    async def go():
        async with Session(switch) as s:
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "one")
            await s.get()
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "two")
            await s.get()  # "one" counts as processed once the next is asked for
            await until(lambda: any(p.get("cursor") >= 1 for p in switch.pongs))

    run(go())
    assert all(set(p) == {"type", "cursor"} and p["type"] == "pong" for p in switch.pongs)


def test_reconnect_resumes_after_the_cursor_with_the_same_id_and_generation(switch):
    async def go():
        async with Session(switch) as s:
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "before")
            assert (await s.get()).text == "before"
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "second")
            assert (await s.get()).text == "second"
            switch.drop_socket(1012)
            await until(lambda: len(switch.opens) == 2)
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "after")
            assert (await s.get()).text == "after"

    run(go())
    q = switch.opens[1]
    assert q["connection_id"] == "conn-1"
    assert q["expected_generation"] == "1"
    assert int(q["start_from"]) >= 1


def test_message_said_during_a_drop_on_an_empty_buffer_still_arrives(switch):
    """Cursor 0 is a position: reopening from `head` would skip what was said meanwhile."""

    async def go():
        async with Session(switch) as s:
            assert s.room.cursor == 0
            switch.drop_socket(1012)
            await until(lambda: not s.room._connected.is_set())
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "during")
            assert (await s.get()).text == "during"

    run(go())
    assert switch.opens[1]["start_from"] == "0"


def test_a_replayed_event_is_not_handed_over_twice(switch):
    async def go():
        async with Session(switch) as s:
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "once")
            assert (await s.get()).text == "once"
            s.room.cursor = 0  # the socket drops before the cursor moved: the server replays
            switch.drop_socket(1011)
            await until(lambda: len(switch.opens) == 2)
            switch.say("AirGrid: dispatch", KAZUM, "kazum", "next")
            assert (await s.get()).text == "next"

    run(go())


def test_heartbeat_lapse_reopens(switch):
    async def go():
        async with Session(switch):
            switch.control("evicted", close=1000, code="heartbeat_lapsed", reason="silent")
            await until(lambda: len(switch.opens) == 2)

    run(go())


@pytest.mark.parametrize("code", ["taken_over", "closed"])
def test_final_evictions_stop_with_the_reason_and_no_reconnect(switch, code):
    async def go():
        async with Session(switch) as s:
            switch.control("evicted", close=1000, code=code, reason="second open won")
            with pytest.raises(SwitchError, match=f"{code}: second open won"):
                await s.get()

    run(go())
    assert len(switch.opens) == 1


def test_refusal_close_code_is_fatal_with_the_detail(switch):
    async def go():
        async with Session(switch) as s:
            switch.control("refused", close=4409, status=409, detail="room already claimed")
            with pytest.raises(SwitchError, match="4409.*409 room already claimed"):
                await s.get()

    run(go())


def test_gap_moves_the_cursor_and_is_logged(switch, caplog):
    async def go():
        async with Session(switch) as s:
            with caplog.at_level(logging.ERROR, "worldbot.switch"):
                switch.control("gap", resumed_at=40, reason="retention", rooms=[], all_rooms=True)
                await until(lambda: s.room.gaps)
            assert s.room.cursor == 40

    run(go())
    assert "event gap (retention)" in caplog.text


# ---------------------------------------------------------------- spoof-safe approvals
def sim_mock(calls: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/approvals":
            return httpx.Response(
                200, json={"approvals": [{"id": "A-0001", "type": "track", "status": "pending"}]}
            )
        if path.endswith("/approve"):
            calls.append((path, request.headers.get("authorization")))
            return httpx.Response(200, json={"result": {"type": "track", "status": "active"}})
        return httpx.Response(200, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")


def bot_on(room, calls, sink=None, tmp_path=None):
    return WorldBot(
        room,
        client=sim_mock(calls),
        humans={"kazum": ROLES},
        tokens={"kazum": "human-token"},
        approval_role={"track": "watch_commander"},
        sink=sink or decisions_sink.FakeSink(tmp_path / "d.jsonl"),
    )


def said(fake: FakeSwitch, needle: str) -> list[str]:
    return [t for _r, t in fake.posted if needle in t]


def test_mapped_sender_approves_spoofed_display_name_is_refused(switch, tmp_path):
    calls: list = []

    async def go():
        async with Session(switch) as s:
            bot = bot_on(s.room, calls, tmp_path=tmp_path)
            switch.say("AirGrid: dispatch", STRANGER, "kazum", "approve A-0001")
            await bot.handle_message(await s.get())
            assert calls == []
            assert said(switch, "FORBIDDEN_ROLE")
            assert said(switch, "unmapped user")
            assert f"unmapped:{STRANGER}" == bot.sink.entries[-1]["actor"]

            switch.say("AirGrid: dispatch", KAZUM, "Anything At All", "approve A-0001")
            await bot.handle_message(await s.get())
            await bot.aclose()

    run(go())
    assert calls == [("/approvals/A-0001/approve", "Bearer human-token")]
    assert said(switch, "✅ APPROVED A-0001 by @kazum")


def test_display_name_of_an_agent_cannot_approve(switch, tmp_path):
    calls: list = []

    async def go():
        async with Session(switch) as s:
            bot = bot_on(s.room, calls, tmp_path=tmp_path)
            switch.say("AirGrid: dispatch", AGENT, "dispatch", "approve A-0001")
            m = await s.get()
            assert m.author == "dispatch"
            await bot.handle_message(m)
            await bot.aclose()

    run(go())
    assert calls == [] and said(switch, "FORBIDDEN_ROLE")


def test_load_senders_list_and_single_and_duplicate(tmp_path):
    p = tmp_path / "humans.yaml"
    doc = {
        "humans": [
            {"discord": "kazum", "switch_sender": ["@a:x", "@b:x"], "roles": list(ROLES)},
            {"discord": "t1", "switch_sender": "@c:x", "roles": ["field_officer"]},
            {"discord": "t2", "roles": ["remote_pilot"]},
        ]
    }
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert load_senders(p) == {"@a:x": "kazum", "@b:x": "kazum", "@c:x": "t1"}
    doc["humans"][1]["switch_sender"] = "@a:x"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(SwitchError, match="two humans"):
        load_senders(p)


def test_who_prints_the_sender_id_and_takes_nothing(switch):
    import io

    out = io.StringIO()

    async def go():
        task = asyncio.ensure_future(who(config(switch), {KAZUM: "kazum"}, 1.5, out))
        await until(lambda: switch.opens)
        switch.say("AirGrid: test-room", STRANGER, "kazum", "hello")
        switch.say("AirGrid: dispatch", KAZUM, "kazum", "hi")
        assert await task == 0

    run(go())
    text = out.getvalue()
    assert f"#test-room | kazum | switch_sender: {STRANGER} | UNMAPPED" in text
    assert f"switch_sender: {KAZUM} | mapped to kazum" in text
    assert switch.posted == []
    assert "rooms" not in switch.opens[0] and switch.opens[0]["connection_id"] != "conn-1"


# ---------------------------------------------------------------- decision log
ENTRY = {
    "kind": "approved",
    "sim_min": 1061.0,
    "room": "#dispatch",
    "actor": "kazum",
    "action": "approve",
    "target": "A-0003",
    "code": None,
    "detail": "track",
}


def test_summary_is_one_line():
    assert summary_line(ENTRY) == "17:41 · approved · approve A-0003 · by kazum · track"
    many = {**ENTRY, "kind": "refusal", "code": "NOT_FOUND", "detail": "a\n b"}
    assert "\n" not in summary_line(many) and "NOT_FOUND · a b" in summary_line(many)


def test_document_is_created_in_dispatch_then_appended(switch, tmp_path):
    async def go():
        async with Session(switch) as s:
            sink = SwitchSink(s.room, tmp_path / "d.jsonl")
            sink.record(ENTRY)
            sink.record({**ENTRY, "target": "A-0004"})
            await sink.drain()
            assert sink.fallbacks == 0

    run(go())
    doc = next(iter(switch.docs.values()))
    assert doc["name"] == DOC_NAME
    assert doc["room_id"] == switch.room_id("AirGrid: dispatch")
    assert doc["content"].splitlines()[1:] == [
        "17:41 · approved · approve A-0003 · by kazum · track",
        "17:41 · approved · approve A-0004 · by kazum · track",
    ]
    assert "simulated" in doc["content"].splitlines()[0]
    assert len(switch.docs) == 1 and not said(switch, "DECISION")


def test_restart_finds_the_document_and_keeps_its_lines(switch, tmp_path):
    async def one(target):
        async with Session(switch) as s:
            sink = SwitchSink(s.room, tmp_path / "d.jsonl")
            sink.record({**ENTRY, "target": target})
            await sink.drain()

    run(one("A-0001"))
    run(one("A-0002"))
    assert len(switch.docs) == 1
    text = next(iter(switch.docs.values()))["content"]
    assert "A-0001" in text and "A-0002" in text and text.count("Flight authorisation log") == 1


def test_document_failure_posts_the_summary_and_logs_then_catches_up(switch, tmp_path, caplog):
    switch.fail_docs = True

    async def go():
        async with Session(switch) as s:
            sink = SwitchSink(s.room, tmp_path / "d.jsonl")
            with caplog.at_level(logging.ERROR, "worldbot.decisions"):
                sink.record(ENTRY)
                await sink.drain()
            assert sink.fallbacks == 1
            switch.fail_docs = False
            sink.record({**ENTRY, "target": "A-0004"})
            await sink.drain()

    run(go())
    line = said(switch, "📣 DECISION")
    assert line == [
        "📣 DECISION · 17:41 · approved · approve A-0003 · by kazum · track · #dispatch"
    ]
    assert "HTTP 403: document store refused" in caplog.text
    text = next(iter(switch.docs.values()))["content"]
    assert "A-0003" in text and "A-0004" in text


def test_document_owned_by_someone_else_falls_back(switch, tmp_path):
    room_id = switch.room_id("AirGrid: dispatch")
    switch.docs["doc-x"] = {
        "name": DOC_NAME,
        "content": "theirs",
        "room_id": room_id,
        "owner": "dispatch-agent",
    }

    async def go():
        async with Session(switch) as s:
            sink = SwitchSink(s.room, tmp_path / "d.jsonl")
            sink.record(ENTRY)
            await sink.drain()

    run(go())
    assert said(switch, "📣 DECISION") and switch.docs["doc-x"]["content"] == "theirs"


def test_no_connection_falls_back_without_hanging(switch, tmp_path):
    async def go():
        room = SwitchRoom(config(switch))
        await room.start()
        sink = SwitchSink(room, tmp_path / "d.jsonl", connect_wait_s=0.05)
        sink.record(ENTRY)
        await sink.drain()
        await room.aclose()

    run(go())
    assert said(switch, "📣 DECISION")


def test_bot_stops_with_the_reason_when_the_room_dies(switch, tmp_path):
    async def go():
        room = SwitchRoom(config(switch), {})
        await room.start()
        bot = bot_on(room, [], tmp_path=tmp_path)
        task = asyncio.ensure_future(bot.run())
        await until(lambda: switch.opens)
        await until(lambda: room._connected.is_set())
        switch.control("evicted", close=1000, code="taken_over", reason="x")
        with pytest.raises(SwitchError, match="taken_over"):
            await task
        await bot.aclose()
        await room.aclose()

    run(go())


# ---------------------------------------------------------------- whole loop
def test_card_spoof_approve_and_log_through_a_real_sim_and_bot_run(switch, sim, c, tmp_path):
    """Real sim, real SSE, bot.run() on the fake Switch: the path a live approval takes."""
    load(c, mode="demo")
    aid = approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor"}))

    def status() -> str:
        rows = c.get("/approvals").json()["approvals"]
        return next(a for a in rows if a["id"] == aid)["status"]

    async def go():
        room = SwitchRoom(config(switch), {KAZUM: "kazum"}, backoff_s=0.01)
        await room.start()
        sink = SwitchSink(room, tmp_path / "d.jsonl")
        tokens = {"kazum": TOKENS_DOC["humans"]["kazum"]}
        bot = WorldBot(room, sim.url, humans={"kazum": ROLES}, tokens=tokens, sink=sink)
        run_task = asyncio.ensure_future(bot.run())
        await until(lambda: said(switch, f"🟡 APPROVAL {aid}"), 15)
        await until(room._connected.is_set)
        hangar = "AirGrid: fleet-hangar"
        switch.say(hangar, STRANGER, "kazum", f"approve {aid}")
        await until(lambda: said(switch, "FORBIDDEN_ROLE"), 15)
        assert status() == "pending"
        switch.say(hangar, KAZUM, "kazum", f"approve {aid}")
        await until(lambda: said(switch, f"✅ APPROVED {aid} by @kazum"), 15)
        await sink.drain()
        bot.stop()
        await run_task
        await bot.aclose()
        await room.aclose()

    run(go())
    assert status() != "pending"
    log_text = next(iter(switch.docs.values()))["content"]
    assert f"approved · approve {aid} · by kazum" in log_text
    assert f"unmapped:{STRANGER}" in log_text


# ---------------------------------------------------------------- registration script
REGISTER = ROOT / "scripts" / "register_worldbot.py"


def register(env, *args):
    return subprocess.run(
        [sys.executable, str(REGISTER), *args],
        env={**base_env(), **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def test_register_stores_the_key_and_prints_neither(switch, tmp_path):
    cfg = tmp_path / "switch.local.yaml"
    env = {"AIRGRID_SWITCH_REG_KEY": REG_KEY, "AIRGRID_SWITCH_CONFIG": str(cfg)}
    r = register(env, "--url", switch.url)
    assert r.returncode == 0, r.stderr
    doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert doc["api_key"] == switch.api_key and doc["agent_id"] == switch.agent_id
    assert doc["connection_id"]
    assert REG_KEY not in r.stdout + r.stderr and doc["api_key"] not in r.stdout + r.stderr
    assert REG_KEY not in cfg.read_text(encoding="utf-8")
    body = switch.registered["airgrid-worldbot"]
    assert body["integration_profile"]["connection_model"] == "always_on"
    assert switch.addressing == "anyone"
    loaded = SwitchConfig.load(cfg)
    assert loaded.base_url == switch.url


def test_register_refuses_to_clobber_and_overwrite_issues_a_new_key(switch, tmp_path):
    cfg = tmp_path / "s.yaml"
    env = {"AIRGRID_SWITCH_REG_KEY": REG_KEY, "AIRGRID_SWITCH_CONFIG": str(cfg)}
    assert register(env, "--url", switch.url).returncode == 0
    first = yaml.safe_load(cfg.read_text(encoding="utf-8"))["api_key"]
    r = register(env, "--url", switch.url)
    assert r.returncode == 1 and "--overwrite" in r.stderr
    cfg.unlink()
    r = register(env, "--url", switch.url)
    assert r.returncode == 1 and "already exists" in r.stderr
    assert register(env, "--url", switch.url, "--overwrite").returncode == 0
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["api_key"] != first


def test_register_key_only_from_env_and_no_argv_flag(switch, tmp_path):
    env = {"AIRGRID_SWITCH_CONFIG": str(tmp_path / "s.yaml")}
    r = register(env, "--url", switch.url)
    assert r.returncode == 1 and "AIRGRID_SWITCH_REG_KEY" in r.stderr
    r = register({**env, "AIRGRID_SWITCH_REG_KEY": REG_KEY}, "--key", "x", "--url", switch.url)
    assert r.returncode == 2


def test_register_wrong_key_and_wrong_url_are_explained(switch, tmp_path):
    env = {"AIRGRID_SWITCH_REG_KEY": "nope-secret", "AIRGRID_SWITCH_CONFIG": str(tmp_path / "s")}
    r = register(env, "--url", switch.url)
    assert r.returncode == 1 and "type registration" in r.stderr
    assert "nope-secret" not in r.stdout + r.stderr
    r = register(env, "--url", dead_url())
    assert r.returncode == 1 and "nothing answered" in r.stderr


def test_local_config_is_gitignored():
    r = subprocess.run(
        ["git", "check-ignore", "config/switch.local.yaml", "config/humans.yaml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and "switch.local.yaml" in r.stdout


# ---------------------------------------------------------------- room text (agents/switch)
SWITCH_DIR = ROOT / "agents" / "switch"


def rules_table() -> dict[str, str]:
    text = (ROOT / "docs" / "05-AGENTS.md").read_text(encoding="utf-8")
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 3 and cells[0].startswith("`#"):
            for room in re.findall(r"`(#[\w-]+)`", cells[0]):
                out[room] = cells[2]
    return out


def test_instructions_are_verbatim_for_all_rooms():
    rules = rules_table()
    assert len(rules) == 9
    for room, text in rules.items():
        f = SWITCH_DIR / "rooms" / f"{room.lstrip('#')}.txt"
        assert f.read_text(encoding="utf-8").strip() == text, room


def test_room_table_covers_every_agent_and_room():
    doc = yaml.safe_load((SWITCH_DIR / "agent-rooms.yaml").read_text(encoding="utf-8"))
    assert set(doc["agents"]) == set(rooms.AGENTS)
    used = {r for rs in doc["agents"].values() for r in rs}
    assert used == set(rooms.ROOMS)
    assert doc["agents"]["privacy"] == ["#privacy-log", "#incident-1", "#incident-2"]
    assert set(rooms.ROOMS) | {"#test-room"} == set(doc["worldbot"])


def test_no_secret_shaped_values_in_agents_switch():
    for p in SWITCH_DIR.rglob("*"):
        if p.is_file():
            assert not re.search(r"(?i)api[_-]?key\s*[:=]\s*\S+", p.read_text(encoding="utf-8"))
