"""Layer 4 · World Bot on FakeRoom against the real sim: cards, approvals, refusals, @mentions,
simulated labels, decision sink. Nothing here is a mock of the sim; `requests` records what the
bot really sent, so "no sim call" is observed, not assumed.
"""

import asyncio
import io
import json
import logging
import re
import threading

import httpx
import pytest

from tests.integration.harness import dead_url, token_findings
from tests.sim.helpers import HUMANS_DOC, TOKENS_DOC
from tests.sim.p3 import approval_id, approvals, decisions, load, propose, skip
from worldbot import rooms
from worldbot.bot import WorldBot
from worldbot.decisions_sink import DecisionSink, FakeSink
from worldbot.fake_room import FakeRoom, parse_line
from worldbot.transport import RoomMessage, RoomTransport

HUMANS = {h["discord"]: tuple(h["roles"]) for h in HUMANS_DOC["humans"]}
AGENTS = ["dispatch", "fleet", "airspace", "intel", "relay", "privacy", "comms"]

# 04 §4.4, World Bot block
CARD = re.compile(
    r"^🟡 APPROVAL (A-\d{4}) · (\S+) · by @(\w+) · needs: (\w+)\n"
    r"\s+(.+) · Reply: approve \1 \| reject \1 <reason>$"
)
APPROVED = re.compile(r"^✅ APPROVED A-\d{4} by @\w+ · \S.*$")
DECLINED = re.compile(r"^❌ REJECTED A-\d{4} by @\w+: \S.*$")
ALERT = re.compile(r"^📣 [A-Z_]+ · .+ · #[a-z0-9-]+\b.*$", re.S)
REFUSAL = re.compile(r"^❌ REJECTED \| action: .+ \| reason: [A-Z_]+ \| instead: \S.*$")
DUTY_PILOT = re.compile(r'^🛩 DP-\d \S.* \(duty pilot, simulated\): ".+"$')
RADIO = re.compile(r'^📻 \S.* \(simulated\): ".+"$')


class Live:
    """A WorldBot running on the real SSE stream, driven from the test."""

    def __init__(self, sim, tmp_path, script=None, url=None):
        self.requests: list[tuple[str, str]] = []
        self.room = FakeRoom(script=script, interactive=False, out=io.StringIO())
        self.sink = FakeSink(tmp_path / "decisions.jsonl")
        url = url or sim.url

        async def note(req):
            self.requests.append((req.method, req.url.path))

        self.bot = WorldBot(
            self.room,
            url,
            client=httpx.AsyncClient(base_url=url, timeout=10, event_hooks={"request": [note]}),
            humans=HUMANS,
            tokens=dict(TOKENS_DOC["humans"]),
            sink=self.sink,
        )

    async def __aenter__(self):
        self.task = asyncio.create_task(self.bot.run())
        await self.wait(lambda: ("GET", "/events/stream") in self.requests)
        return self

    async def __aexit__(self, *exc):
        self.bot.stop()
        await asyncio.wait_for(self.task, 10)
        await self.bot.aclose()

    async def wait(self, pred, what="condition", timeout=15.0):
        end = asyncio.get_running_loop().time() + timeout
        while not pred():
            if asyncio.get_running_loop().time() > end:
                raise AssertionError(f"timed out on {what}; posted: {self.room.posted[-8:]}")
            await asyncio.sleep(0.02)

    async def card(self, aid):
        await self.wait(lambda: self.find(f"APPROVAL {aid}"), f"card {aid}")
        return self.find(f"APPROVAL {aid}")[0]

    def find(self, text, room=None):
        return [(r, t) for r, t in self.room.posted if text in t and (room is None or r == room)]

    async def say(self, author, room, text):
        n = self.bot.handled
        self.room.say(author, room, text)
        await self.wait(lambda: self.bot.handled > n, f"bot handling {text!r}")

    def posts(self):
        return [p for p in self.requests if p[0] == "POST"]

    def decisions_posts(self):
        return [p for p in self.posts() if p[1].startswith("/approvals/") or p[1] == "/actions"]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 90))


def status(c, aid):
    return {a["id"]: a for a in approvals(c)}[aid]["status"]


def two_pending(c):
    """Live mode at 17:45 (nothing golden applied): A-0001 and A-0002 pending."""
    load(c, mode="live")
    skip(c, "17:45")
    a = approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}, "track the scooter"))
    b = approval_id(propose(c, "broadcast", {"drone": "D-02", "text": "keep left"}, "loudspeaker"))
    assert (a, b) == ("A-0001", "A-0002")
    return a, b


# ---------- transport ----------


def test_fake_room_is_a_room_transport():
    assert issubclass(FakeRoom, RoomTransport)
    assert issubclass(FakeSink, DecisionSink)


def test_fake_room_prints_room_then_text():
    out = io.StringIO()
    room = FakeRoom(script=[], out=out)
    msg_id = run(room.post("#dispatch", "hello"))
    assert out.getvalue() == "[#dispatch] hello\n"
    assert msg_id


def test_fake_room_reads_author_lines():
    assert parse_line("kazum: approve A-0007") == RoomMessage(
        "#dispatch", "kazum", "approve A-0007"
    )
    assert parse_line("no author here") is None


def test_stdin_approval_reaches_the_bot(sim, c, tmp_path):
    a, _ = two_pending(c)

    async def go():
        live = Live(sim, tmp_path)
        live.room = FakeRoom(stdin=io.StringIO(f"kazum: approve {a}\n"), out=io.StringIO())
        live.bot.transport = live.room
        async with live:
            await live.wait(lambda: live.find(f"APPROVED {a}"), "approved over stdin")

    run(go())
    assert status(c, a) == "approved"


def test_script_waits_for_its_sim_time(sim, c, tmp_path):
    """A script line fires when the sim reaches its time, not before."""
    load(c, mode="live")
    skip(c, "17:45")
    aid = approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}, "x"))

    async def go():
        script = [("17:50", "kazum", "#dispatch", f"approve {aid}")]
        async with Live(sim, tmp_path, script=script) as live:
            await live.card(aid)
            await asyncio.sleep(0.5)
            assert status(c, aid) == "pending", "script fired ahead of its time"
            await asyncio.to_thread(skip, c, "17:50")
            await live.wait(lambda: live.find(f"APPROVED {aid}"), "scripted approval")

    run(go())


# ---------- cards ----------

CARD_CASES = [
    ("track", {"incident": "INC-2", "minutes": 20}, "dispatch", "#dispatch"),
    ("broadcast", {"drone": "D-02", "text": "keep left"}, "dispatch", "#dispatch"),
    (
        "footage_release",
        {"incident": "INC-1", "clip": "C-3", "to": "court"},
        "privacy",
        "#privacy-log",
    ),
    ("zone", {"zone": "VIP-TEMP", "until": "21:00"}, "airspace", "#dispatch"),  # L42
    ("out_of_service", {"drone": "D-11", "why": "motor"}, "fleet", "#fleet-hangar"),
    ("standing_policy", {}, "dispatch", "#dispatch"),
]


@pytest.mark.parametrize("type_,args,by,room", CARD_CASES, ids=[x[0] for x in CARD_CASES])
def test_card_format_and_room(sim, c, tmp_path, type_, args, by, room):
    load(c, mode="live")
    skip(c, "17:45")

    async def go():
        async with Live(sim, tmp_path) as live:
            aid = await asyncio.to_thread(
                lambda: approval_id(propose(c, type_, args, "one line summary", who=by))
            )
            got_room, text = await live.card(aid)
            assert got_room == room, text
            m = CARD.match(text)
            assert m, f"not §4.4: {text!r}"
            assert m.group(1) == aid
            assert m.group(2) == type_
            assert m.group(3) == by
            assert m.group(4) == "watch_commander"
            assert m.group(5) == "one line summary"
            assert len(live.find(f"APPROVAL {aid}")) == 1, "card posted twice"

    run(go())


def test_pending_cards_are_posted_on_connect(sim, c, tmp_path):
    a, b = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(a)
            await live.card(b)

    run(go())


# ---------- approve ----------


def test_approve_from_kazum_works(sim, c, tmp_path):
    _, b = two_pending(c)
    assert b == "A-0002"

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(b)
            await live.say("kazum", "#dispatch", "approve A-0002")
            room, line = live.find("APPROVED A-0002")[0]
            assert APPROVED.match(line), line
            assert "@kazum" in line
            assert room == "#dispatch"
            assert ("POST", "/approvals/A-0002/approve") in live.requests

    run(go())
    a = {x["id"]: x for x in approvals(c)}[b]
    assert a["status"] == "approved"
    assert a["decided_by"] == "kazum"


def test_prefix_grants_nothing_and_blocks_nothing(sim, c, tmp_path):
    """05: the bot maps the approval to the role, not the FO:/RP: prefix."""
    a, b = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(a)
            await live.say("kazum", "#dispatch", f"FO: approve {a}")
            await live.say("t2", "#dispatch", f"WC: approve {b}")

    run(go())
    assert status(c, a) == "approved"
    assert status(c, b) == "pending"


def test_a_chat_line_mentioning_a_command_is_not_a_command(sim, c, tmp_path):
    a, _ = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(a)
            await live.say("kazum", "#dispatch", f"should I approve {a} or wait?")
            assert not live.decisions_posts()

    run(go())
    assert status(c, a) == "pending"


# ---------- refused before the sim ----------


REFUSED_AUTHORS = [
    ("mallory", "unmapped"),
    ("t2", "wrong_role"),
    *[(a, "agent") for a in AGENTS],
]


@pytest.mark.parametrize("author,why", REFUSED_AUTHORS, ids=lambda v: v)
@pytest.mark.parametrize("verb", ["approve A-0002", "reject A-0002 no"], ids=["approve", "reject"])
def test_refused_with_no_sim_call(sim, c, tmp_path, author, why, verb):
    two_pending(c)
    logged_before = len(decisions(c))

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card("A-0002")
            await live.say(author, "#dispatch", verb)
            assert not live.decisions_posts(), f"sim was called: {live.posts()}"
            lines = live.find("FORBIDDEN_ROLE")
            assert lines, f"no refusal line; posted {live.room.posted[-3:]}"
            room, line = lines[-1]
            assert room == "#dispatch"
            assert REFUSAL.match(line), line
            assert not live.find("APPROVED A-0002")
            e = live.sink.entries[-1]
            assert e["kind"] == "refusal", e
            assert e["actor"] == author, e
            assert e["target"] == "A-0002", e
            assert e["code"] == "FORBIDDEN_ROLE", e

    run(go())
    assert status(c, "A-0002") == "pending"
    assert len(decisions(c)) == logged_before, "the refused attempt reached the sim log"


def test_agent_author_is_refused_even_if_mapped_as_a_human(sim, c, tmp_path):
    """A hostile humans.yaml entry cannot turn a desk into an approver."""
    two_pending(c)

    async def go():
        live = Live(sim, tmp_path)
        live.bot.humans = {**HUMANS, "dispatch": ("watch_commander",)}
        live.bot.tokens = {**TOKENS_DOC["humans"], "dispatch": TOKENS_DOC["humans"]["kazum"]}
        async with live:
            await live.card("A-0002")
            await live.say("dispatch", "#dispatch", "approve A-0002")
            assert not live.decisions_posts()

    run(go())
    assert status(c, "A-0002") == "pending"


def test_mapped_human_without_a_token_is_refused(sim, c, tmp_path):
    two_pending(c)

    async def go():
        live = Live(sim, tmp_path)
        live.bot.tokens = {}
        async with live:
            await live.card("A-0002")
            await live.say("kazum", "#dispatch", "approve A-0002")
            assert not live.decisions_posts()
            assert live.sink.entries[-1]["kind"] == "refusal"

    run(go())
    assert status(c, "A-0002") == "pending"


# ---------- reject ----------


def test_reject_with_reason_works(sim, c, tmp_path):
    _, b = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(b)
            await live.say("kazum", "#dispatch", f"reject {b} not with kids nearby")
            _, line = live.find(f"REJECTED {b}")[0]
            assert DECLINED.match(line), line
            assert "@kazum: not with kids nearby" in line

    run(go())
    a = {x["id"]: x for x in approvals(c)}[b]
    assert a["status"] == "rejected"
    assert a["reason"] == "not with kids nearby"


def test_reject_without_reason_makes_no_sim_call(sim, c, tmp_path):
    _, b = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(b)
            await live.say("kazum", "#dispatch", f"reject {b}")
            assert not live.decisions_posts()

    run(go())
    assert status(c, b) == "pending"


# ---------- refused by the sim ----------


def test_sim_rejection_is_shown_with_its_code(sim, c, tmp_path):
    a, _ = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(a)
            await live.say("kazum", "#dispatch", "approve A-9999")
            _, line = live.find("reason: NOT_FOUND")[0]
            assert REFUSAL.match(line), line
            e = live.sink.entries[-1]
            assert (e["kind"], e["code"], e["target"]) == ("refusal", "NOT_FOUND", "A-9999"), e

    run(go())


def test_double_approval_is_not_applied_twice(sim, c, tmp_path):
    a, _ = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(a)
            await live.say("kazum", "#dispatch", f"approve {a}")
            await live.say("kazum", "#dispatch", f"approve {a}")
            assert len(live.find(f"APPROVED {a}")) == 1
            assert live.sink.entries[-1]["kind"] == "refusal", live.sink.entries

    run(go())


def test_sim_down_posts_an_error_not_a_crash(sim, tmp_path):
    async def go():
        live = Live(sim, tmp_path, url=dead_url())
        live.bot.cards["A-0001"] = {"id": "A-0001", "type": "track"}
        await live.bot.handle_message(RoomMessage("#dispatch", "kazum", "approve A-0001"))
        await live.bot.aclose()
        assert "not done" in live.room.posted[-1][1]
        assert live.sink.entries[-1]["kind"] == "error"
        assert not token_findings("sink", json.dumps(live.sink.entries))

    run(go())


# ---------- control ----------


def test_take_and_release_control_from_chat(sim, c, tmp_path):
    load(c)
    skip(c, "17:45")

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.say("t2", "#fleet-hangar", "RP: take-control D-14")
            assert not live.decisions_posts()
            await live.say("kazum", "#fleet-hangar", "RP: take-control D-14")
            assert c.get("/fleet/D-14").json()["drone"]["manual"] is True
            await live.say("kazum", "#fleet-hangar", "RP: release-control D-14")
            assert c.get("/fleet/D-14").json()["drone"]["manual"] is False

    run(go())


# ---------- every event reaches its owner ----------

OWNERS = {
    "cad_alert": ("#dispatch", "dispatch"),
    "launch": ("#dispatch", "dispatch"),
    "eyes_on": ("#dispatch", "dispatch"),
    "rotation": ("#fleet-hangar", "fleet"),
    "dock_fault": ("#fleet-hangar", "fleet"),
    "weather_grounded": ("#airspace", "airspace"),
    "hold": ("#airspace", "airspace"),
    "handoff": ("#incident-2", "relay"),
    "reacquire": ("#incident-2", "relay"),
    "approved": ("#privacy-log", "privacy"),
    "unit_released": ("#field-units", "comms"),
}


@pytest.fixture(scope="module")
def replayed(sim, c, tmp_path_factory):
    """A golden run to 19:05, then a bot connects and catches up over the real stream."""
    load(c)
    skip(c, "19:05")
    events = c.get("/events").json()["events"]
    tmp = tmp_path_factory.mktemp("bot")

    async def go():
        async with Live(sim, tmp) as live:
            await live.wait(lambda: live.bot.last_seq >= events[-1]["seq"], "catch-up", 60)
            return live.room.posted

    return events, run(go())


def test_every_event_is_alerted_once(replayed):
    events, posted = replayed
    alerts = [t for _, t in posted if t.startswith("📣")]
    assert len(alerts) == len(events)
    for line in alerts:
        assert ALERT.match(line), line


@pytest.mark.parametrize("kind", OWNERS)
def test_owner_is_mentioned_in_the_right_room(replayed, kind):
    events, posted = replayed
    room, agent = OWNERS[kind]
    wanted = [e for e in events if e["type"] == kind]
    assert wanted, (
        f"golden run has no {kind} event; event types: {sorted({e['type'] for e in events})}"
    )
    for e in wanted:
        hits = [(r, t) for r, t in posted if t.startswith("📣") and e["text"] in t]
        assert hits, f"no alert for {e['text']!r}"
        assert any(r == room and f"@{agent}" in t for r, t in hits), (kind, hits)


def test_every_alert_mentions_a_desk(replayed):
    events, posted = replayed
    quiet = {str(e["text"]) for e in events if e["type"] in rooms.QUIET_EVENTS}
    for _, t in posted:
        if t.startswith("📣") and not any(q in t for q in quiet):
            assert any(f"@{a}" in t for a in AGENTS), t


def test_routine_events_post_without_waking_an_agent(replayed):
    """L51: every mention starts a session, so routine lines stay unaddressed."""
    events, posted = replayed
    wanted = [e for e in events if e["type"] in rooms.QUIET_EVENTS]
    assert wanted
    for e in wanted:
        hits = [t for _, t in posted if t.startswith("📣") and e["text"] in t]
        assert hits, e["text"]
        assert not any(f"@{a}" in t for t in hits for a in AGENTS), hits


def test_privacy_refusal_goes_to_privacy_and_no_fly_to_airspace(replayed):
    events, posted = replayed
    for code, room, agent in (
        ("PRIVACY_POLICY", "#privacy-log", "privacy"),
        ("NO_FLY_ZONE", "#airspace", "airspace"),
    ):
        hits = [(r, t) for r, t in posted if t.startswith("📣") and f"⛔ {code}" in t]
        assert hits, code
        assert all(r == room and f"@{agent}" in t for r, t in hits), hits


# ---------- simulated labels ----------


def test_simulated_lines_are_labelled(replayed):
    _, posted = replayed
    pilots = [t for _, t in posted if t.startswith("🛩")]
    radios = [t for _, t in posted if t.startswith("📻")]
    assert pilots, "no duty-pilot line in a whole golden shift"
    assert radios, "no radio line in a whole golden shift"
    for t in pilots:
        assert DUTY_PILOT.match(t), t
    for t in radios:
        assert RADIO.match(t), t


def test_duty_pilot_names_come_from_the_world(replayed):
    _, posted = replayed
    assert any("DP-1 Asha Menon (duty pilot, simulated)" in t for _, t in posted)


def test_no_unlabelled_voice_of_a_simulated_person(replayed):
    """A quoted speech line from anyone but a real desk must carry the label."""
    _, posted = replayed
    for _, t in posted:
        if re.match(r'^\S+ [\w -]+: "', t):
            assert "simulated" in t, t


def test_lines_module_is_templates_only():
    import worldbot.lines as lines

    src = open(lines.__file__, encoding="utf-8").read()
    for word in ("anthropic", "openai", "claude", "llm_call", "completion"):
        assert word not in src.lower()


# ---------- sink ----------


def test_every_decision_reaches_the_sink(sim, c, tmp_path):
    a, b = two_pending(c)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card(b)
            await live.say("mallory", "#dispatch", f"approve {a}")
            await live.say("t2", "#dispatch", f"approve {a}")
            await live.say("dispatch", "#dispatch", f"approve {a}")
            await live.say("kazum", "#dispatch", f"approve {a}")
            await live.say("kazum", "#dispatch", f"reject {b} too loud")
            await live.say("kazum", "#dispatch", "approve A-9999")
            return live.sink

    sink = run(go())
    got = [(e["kind"], e["actor"], e["target"]) for e in sink.entries]
    assert got == [
        ("refusal", "mallory", a),
        ("refusal", "t2", a),
        ("refusal", "dispatch", a),
        ("approved", "kazum", a),
        ("rejected", "kazum", b),
        ("refusal", "kazum", "A-9999"),
    ]
    assert all(e["sim_min"] == pytest.approx(17 * 60 + 45) for e in sink.entries), sink.entries
    on_disk = [json.loads(ln) for ln in sink.path.read_text(encoding="utf-8").splitlines()]
    assert on_disk == sink.entries


# ---------- tokens ----------


def test_no_token_in_rooms_sink_or_log(sim, c, tmp_path, caplog):
    two_pending(c)
    caplog.set_level(logging.DEBUG)

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.card("A-0002")
            await live.say("kazum", "#dispatch", "approve A-0001")
            await live.say("t2", "#dispatch", "approve A-0002")
            await live.say("kazum", "#dispatch", "approve A-9999")
            return live

    live = run(go())
    bad = token_findings("rooms", "\n".join(t for _, t in live.room.posted))
    bad += token_findings("printed", live.room._out.getvalue())
    bad += token_findings("sink", live.sink.path.read_text(encoding="utf-8"))
    bad += token_findings("log", caplog.text)
    assert not bad, "\n".join(bad)


# ---------- agent activity ----------


def test_agent_activity_endpoint_answers(c):
    load(c, mode="demo")
    for state in ("busy", "idle"):
        r = c.post("/control/agent-activity", json={"agent": "dispatch", "state": state})
        assert r.status_code == 200, r.text


def test_idle_releases_a_demo_hold(c):
    load(c, mode="demo")
    c.post("/control/agent-activity", json={"agent": "dispatch", "state": "busy"})
    held = c.get("/status").json()["clock_held"]
    assert any("dispatch" in h for h in held), held
    c.post("/control/agent-activity", json={"agent": "dispatch", "state": "idle"})
    held = c.get("/status").json()["clock_held"]
    assert not any("dispatch" in h for h in held), held


def test_agent_post_reports_idle(sim, c, tmp_path):
    load(c, mode="demo")

    async def go():
        async with Live(sim, tmp_path) as live:
            await live.say("dispatch", "#dispatch", "🚨 INCIDENT | anything")
            assert ("POST", "/control/agent-activity") in live.requests

    run(go())


def test_fake_room_close_ends_the_message_stream():
    """close() ends messages() even when nothing else is queued."""
    room = FakeRoom(interactive=False, out=io.StringIO())
    done = threading.Event()

    async def go():
        async def consume():
            async for _ in room.messages():
                pass
            done.set()

        t = asyncio.create_task(consume())
        await asyncio.sleep(0)
        room.close()
        await asyncio.wait_for(t, 5)

    run(go())
    assert done.is_set()
