"""`--profile city` on the fake Switch with a real sim (docs/city-mode.md). Agents are a scripted
stand-in that replies the way the instruction files say, so messages per incident are counted with
the desks answering and with them offline."""

import asyncio
import re
import subprocess
import time

import httpx
import pytest
import yaml

from tests.integration.conftest import HUMANS_PATH, TOKENS_PATH
from tests.integration.fake_switch import FakeSwitch
from tests.integration.harness import SimProcess, base_env, free_port
from tests.integration.unit.test_install_agents import bash
from tests.sim.helpers import ROOT
from worldbot import city_profile, rooms
from worldbot.bot import WorldBot
from worldbot.cards import parse_city_command
from worldbot.city_profile import CityProfile, load_lead, report_text
from worldbot.decisions_sink import FakeSink
from worldbot.switch_room import SwitchConfig, SwitchRoom

KEY = "city-key-for-tests"
H = {"X-Game-Key": KEY}
KAZUM = "@switch-user-kazum01:airgrid.local"
OTHER = "@switch-user-other99:airgrid.local"
ROLES = ("watch_commander", "remote_pilot", "field_officer", "incident_lead", "duty_doctor")
OPS = "AirGrid: ops"
INC1, INC2 = "AirGrid: incident-1", "AirGrid: incident-2"
HUMAN_ID = re.compile(r"INC-|\(\d")  # drone ids are fine since L65; incident ids not
RUN_GAME = ROOT / "scripts" / "run_game.sh"


@pytest.fixture(scope="module")
def city_sim(tmp_path_factory):
    proc = SimProcess(tmp_path_factory.mktemp("city"), TOKENS_PATH, HUMANS_PATH)
    proc.env["AIRGRID_GAME_KEY"] = KEY
    proc.start()
    yield proc
    proc.stop()


@pytest.fixture
def switch():
    fake = FakeSwitch((OPS, INC1, INC2, "AirGrid: test-room"))
    fake.start()
    yield fake
    fake.stop()


class Run:
    """Sim paused and stepped by hand; the bot runs on the real event stream."""

    def __init__(self, fake, sim, tmp_path, key=KEY, agents=True):
        self.fake, self.sim, self.agents = fake, sim, agents
        self.tmp_path, self.key = tmp_path, key
        self.c = httpx.Client(base_url=sim.url, timeout=30)
        self.t = 9 * 60
        self.answered = 0

    async def __aenter__(self):
        cfg = SwitchConfig(self.fake.url, self.fake.agent_id, self.fake.api_key, "conn-c")
        self.room = SwitchRoom(cfg, {KAZUM: "kazum"}, backoff_s=0.01, home_room="#ops")
        await self.room.start(city_profile.ROOMS)
        self.bot = WorldBot(
            self.room,
            self.sim.url,
            humans={"kazum": ROLES, "t2": ("field_officer",)},
            tokens={},
            sink=FakeSink(self.tmp_path / "d.jsonl"),
            profile=CityProfile("<@123>"),
            operator_key=self.key,
        )
        self.task = asyncio.ensure_future(self.bot.run())
        assert self.c.post("/city/start", json={"paused": True}, headers=H).status_code == 200
        await wait_for(lambda: self.room._connected.is_set() and self.bot._synced)
        return self

    async def __aexit__(self, *exc):
        self.bot.stop()
        await self.task
        await self.bot.aclose()
        await self.room.aclose()
        self.c.close()

    def _reply_as_agents(self):
        """A desk answers in the room it was mentioned, once, as the instruction files say."""
        while self.answered < len(self.fake.events):
            data = self.fake.events[self.answered]["data"]
            self.answered += 1
            p = data["payload"]
            if p["sender"] != self.fake.bot_sender:
                continue
            for name in re.findall(r"@(fire-station|hospital|police)\b", p["body"]):
                room = self.fake.rooms[data["room_id"]]
                self.fake.say(room, f"@switch-agent-{name}:x", name, f"{name}: on the way, ETA 4")

    async def step(self, minutes=1.0):
        self.t += minutes
        to = f"{int(self.t // 60):02d}:{int(self.t % 60):02d}"
        r = self.c.post("/control/skip", json={"to": to})
        assert r.status_code == 200, r.text
        await asyncio.sleep(0.03)
        if self.agents:
            self._reply_as_agents()

    def view(self):
        return self.c.get("/city").json()

    async def until(self, check, limit=200):
        for _ in range(limit):
            if check():
                return
            await self.step()
        pytest.fail("not reached")

    def asking(self, iid):
        return any("decision" in i for i in self.view()["incidents"] if i["id"] == iid)

    def trigger(self, kind):
        r = self.c.post("/city/trigger", json={"kind": kind}, headers=H)
        assert r.status_code == 200, r.text
        return r.json()["incident"]["id"]


async def wait_for(check, timeout=15.0):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.02)


def texts(fake):
    return [t for _r, t in fake.posted]


def counts(fake, room):
    """Bot and agent lines in a room; a person's own typing isn't counted."""
    rid = fake.room_id(room)
    rows = [e["data"] for e in fake.events if e["data"]["room_id"] == rid]
    return len([d for d in rows if d["payload"]["sender"] != KAZUM])


def in_room(fake, room):
    return [t for r, t in fake.posted if r == fake.room_id(room)]


async def answer(run, iid, word, room):
    prof = run.bot.profile
    await wait_for(lambda: iid in prof.asking())
    kind = prof.open[iid]["call"]
    if len(prof.asking(kind)) > 1:  # a flare-up is waiting too
        word = f"{word} {prof.key(iid)}"
    run.fake.say(room, KAZUM, "kazum", word)
    await wait_for(lambda: iid not in prof.asking() or iid not in prof.open)


async def full_incident(run, kind, reply="leave", room=OPS, care="transport"):
    """Trigger, then typed replies with no ids, whatever calls come up. Returns the id."""
    iid = run.trigger(kind)
    for _ in range(400):
        rows = run.view()["incidents"]
        if iid not in [i["id"] for i in rows]:
            break
        for inc in rows:  # flare-ups too: an unanswered call keeps its crew busy
            d = inc.get("decision")
            if d:
                word = care if d["kind"] == "treat_transport" else reply
                await answer(run, inc["id"], word, room if inc["id"] == iid else OPS)
        await run.step()
    else:
        pytest.fail("never closed")
    await run.step()
    return iid


@pytest.mark.parametrize("kind", ["fire", "accident"])
def test_one_incident_stays_within_the_room_limits(switch, city_sim, tmp_path, kind):
    async def go():
        async with Run(switch, city_sim, tmp_path) as run:
            await full_incident(run, kind)

    asyncio.run(asyncio.wait_for(go(), 90))
    print(f"\n{kind}: #ops {counts(switch, OPS)}, #incident-1 {counts(switch, INC1)}")
    care_calls = sum("`transport`" in t for t in in_room(switch, INC1))
    assert counts(switch, OPS) == 2, in_room(switch, OPS)  # start + report: info only (L63)
    assert not [t for t in in_room(switch, OPS) if t.startswith("🙋")]
    escort = 7 if care_calls else 0
    assert 2 <= counts(switch, INC1) <= 6 + escort, in_room(switch, INC1)
    assert counts(switch, INC2) == 0


def test_every_incident_stays_within_limits_including_injuries(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path) as run:
            for i in range(8):
                await full_incident(run, "fire" if i % 2 else "accident")

    asyncio.run(asyncio.wait_for(go(), 120))
    started = sum("Crews are working" in t for t in in_room(switch, OPS))  # flare-ups count
    rooms = in_room(switch, INC1) + in_room(switch, INC2)
    care_calls = sum("`transport" in t for t in rooms)
    assert counts(switch, OPS) <= 2 * started + 2  # a "Nothing is waiting" reply or two
    assert counts(switch, INC1) + counts(switch, INC2) <= 6 * started + 7 * care_calls
    per = " ".join(in_room(switch, INC1) + in_room(switch, INC2))
    hurt = "@hospital" in per and "no ambulance needed" in per
    assert hurt, "both outcomes must appear or this proved nothing"


def test_humans_never_read_ids_or_coordinates_outside_the_report(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire")

    asyncio.run(asyncio.wait_for(go(), 90))
    lines = [t for t in texts(switch) if ": closed." not in t]
    assert lines and not [t for t in lines if HUMAN_ID.search(t)], lines
    assert not [t for t in lines if "on scene" in t or "heads back" in t]


def test_every_incident_room_line_starts_with_the_place(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire")

    asyncio.run(asyncio.wait_for(go(), 90))
    lines = in_room(switch, INC1)
    start = in_room(switch, OPS)[0]
    place = re.sub(r"^\S+ ", "", start).split(". Crews")[0]
    assert "#incident-1" in start and place.endswith("fire")
    assert all(place in t.split(":")[0] for t in lines), lines


def test_call_names_the_place_and_says_what_to_type(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire")

    asyncio.run(asyncio.wait_for(go(), 90))
    call = next(t for t in texts(switch) if t.startswith("🙋"))
    assert re.fullmatch(
        r"🙋 <@123> .+ fire looks (mostly )?clear\. Reply: `leave` or `stay`\.", call
    )


def test_reply_in_the_incident_room_works(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire", room=INC1)

    asyncio.run(asyncio.wait_for(go(), 90))
    assert any("heading back to patrol" in t for t in in_room(switch, INC1))


def test_overlap_uses_the_free_room_and_short_words(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            a, b = run.trigger("fire"), run.trigger("fire")
            prof = run.bot.profile
            await wait_for(lambda: a in prof.open and b in prof.open)
            assert {prof.open[a]["room"], prof.open[b]["room"]} == {"#incident-1", "#incident-2"}
            await run.until(lambda: run.asking(a) and run.asking(b))
            await wait_for(lambda: len(prof.asking()) == 2)
            word = prof.key(a)
            second = next(t for t in texts(switch) if "`leave " in t and t.startswith("🙋"))
            assert "`stay " in second and "@" not in second.split("Reply")[1]
            switch.say(OPS, KAZUM, "kazum", "leave")
            await wait_for(lambda: any("Two calls are open" in t for t in texts(switch)))
            assert run.asking(a) and run.asking(b)
            switch.say(OPS, KAZUM, "kazum", f"leave {word}")
            await wait_for(lambda: prof.asking() == [b])
            switch.say(INC1 if prof.open[b]["room"] == "#incident-1" else INC2, KAZUM, "k", "leave")
            await wait_for(lambda: prof.asking() == [])

    asyncio.run(asyncio.wait_for(go(), 90))
    assert in_room(switch, INC1) and in_room(switch, INC2)


def test_a_freed_room_is_reused(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire")
            await full_incident(run, "fire")

    asyncio.run(asyncio.wait_for(go(), 90))
    assert in_room(switch, INC2) == []


def test_log_is_written_once_per_incident_at_the_report(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            iid = run.trigger("fire")
            await run.until(lambda: run.asking(iid))
            await wait_for(lambda: iid in run.bot.profile.asking())
            switch.say(OPS, OTHER, "kazum", "leave")
            await wait_for(lambda: any("FORBIDDEN_ROLE" in t for t in texts(switch)))
            assert run.bot.sink.entries == []
            switch.say(OPS, KAZUM, "kazum", "leave")
            await wait_for(lambda: run.bot.sink.entries)
            assert len(run.bot.sink.entries) == 1

    asyncio.run(asyncio.wait_for(go(), 90))


def test_switch_down_the_city_runs_on(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            switch.fail_posts = 10**6
            run.room._backoff = 0
            iid = run.trigger("fire")
            await run.until(lambda: run.asking(iid))
            assert not run.task.done()

    asyncio.run(asyncio.wait_for(go(), 90))
    assert switch.posted == []


def test_report_text_without_recording():
    r = {"incident": "INC-1", "kind": "fire", "scene": "X", "response_s": 5, "who": []}
    assert "Recording: none" in report_text(r)


def test_a_full_profile_bot_ignores_leave(tmp_path):
    room = _Recorder()

    async def go():
        bot = _bot(room, tmp_path, profile=None)
        await bot.handle_message(_msg("kazum", "leave INC-1"))
        await bot.aclose()

    asyncio.run(go())
    assert room.out == []


def test_bot_joining_late_says_nothing_about_the_past(switch, city_sim, tmp_path):
    with httpx.Client(base_url=city_sim.url, timeout=30) as c:
        c.post("/city/start", json={"paused": True}, headers=H)
        c.post("/city/trigger", json={"kind": "fire"}, headers=H)
        c.post("/control/skip", json={"to": "09:05"})

    async def go():
        cfg = SwitchConfig(switch.url, switch.agent_id, switch.api_key, "conn-c")
        room = SwitchRoom(cfg, {}, backoff_s=0.01, home_room="#ops")
        await room.start(city_profile.ROOMS)
        sink = FakeSink(tmp_path / "d.jsonl")
        bot = WorldBot(
            room, city_sim.url, humans={}, tokens={}, sink=sink, profile=CityProfile("@k")
        )
        task = asyncio.ensure_future(bot.run())
        await wait_for(lambda: bot._synced)
        await asyncio.sleep(0.3)
        bot.stop()
        await task
        await bot.aclose()
        await room.aclose()

    asyncio.run(asyncio.wait_for(go(), 30))
    assert switch.posted == []


@pytest.mark.parametrize(
    ("text", "verb", "target", "minutes"),
    [
        ("leave", "leave", "", ""),
        ("Stay.", "stay", "", ""),
        ("stay 2 more minutes", "stay", "", "2"),
        ("leave market", "leave", "market", ""),
        ("stay north road", "stay", "north road", ""),
        ("@dispatch leave", "leave", "", ""),
    ],
)
def test_replies_parse(text, verb, target, minutes):
    cmd = parse_city_command(text)
    assert (cmd.verb, cmd.target, cmd.reason) == (verb, target, minutes)


@pytest.mark.parametrize("text", ["we should leave soon", "don't leave", "leaves", "@dispatch ask"])
def test_chat_does_not_parse(text):
    assert parse_city_command(text) is None


def _open_call(bot, iid="INC-T1", scene="Market Street", room="#incident-1"):
    bot.profile.open[iid] = {
        "kind": "fire",
        "scene": scene,
        "name": f"{scene} fire",
        "room": room,
        "asking": True,
        "pinged": {"leave_stay"},
        "call": "leave_stay",
        "recommend": None,
        "human_stayed": False,
    }


def test_word_that_means_no_open_call_is_chat(tmp_path):
    room, seen = _Recorder(), []

    async def go():
        bot = _bot(room, tmp_path, calls=seen)
        await bot.handle_message(_msg("kazum", "leave"))
        await bot.handle_message(_msg("stranger", "stay"))
        _open_call(bot)
        await bot.handle_message(_msg("kazum", "stay safe"))
        await bot.handle_message(_msg("kazum", "leave", room="#test-room"))
        await bot.handle_message(_msg("kazum", "leave", room="#incident-2"))
        await bot.aclose()

    asyncio.run(go())
    # the lead hears "nothing waiting"; strangers and chat stay silent (L60)
    assert seen == []
    assert all("Nothing is waiting" in t for t in room.out)


def test_who_may_decide(tmp_path):
    room, seen = _Recorder(), []

    async def go():
        bot = _bot(room, tmp_path, calls=seen, humans={"t2": ("field_officer",)})
        _open_call(bot)
        await bot.handle_message(_msg("t2", "leave"))
        await bot.handle_message(_msg("unmapped:@x", "leave"))
        await bot.handle_message(_msg("police", "leave"))
        await bot.aclose()

    asyncio.run(go())
    assert not [c for c in seen if c[0] == "/city/decide"]
    assert "needs incident_lead" in room.out[0]
    assert "unmapped user" in room.out[1] and "an agent" in room.out[2]


def test_no_key_refuses_and_the_key_never_shows(tmp_path):
    room, seen = _Recorder(), []

    async def go():
        bot = _bot(room, tmp_path, key="", calls=seen)
        _open_call(bot)
        await bot.handle_message(_msg("kazum", "leave"))
        keyed = _bot(room, tmp_path, key="s3cret", calls=seen)
        _open_call(keyed)
        await keyed.handle_message(_msg("kazum", "stay 2 more minutes"))
        await bot.aclose()
        await keyed.aclose()

    asyncio.run(go())
    assert "no operator key" in room.out[0]
    assert seen == [("/city/decide", "s3cret", b'{"incident":"INC-T1","choice":"stay"}')]
    assert not [t for t in room.out if "s3cret" in t]
    assert "staying a little longer. The sim sets how long, not 2 min." in room.out[-1]


def test_stay_is_asked_again_once_a_silent_room_is_not(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            iid = run.trigger("fire")
            await run.until(lambda: run.asking(iid))
            await wait_for(lambda: iid in run.bot.profile.asking())
            for _ in range(60):
                await run.step()
            assert sum(t.startswith("🙋") for t in texts(switch)) == 1
            await run.until(lambda: run.asking(iid))
            await wait_for(lambda: iid in run.bot.profile.asking())
            switch.say(OPS, KAZUM, "kazum", "stay")
            await wait_for(lambda: any("staying a little" in t for t in texts(switch)))
            await run.until(lambda: sum(t.startswith("🙋") for t in texts(switch)) == 2)

    asyncio.run(asyncio.wait_for(go(), 120))


def test_spoofed_display_name_cannot_decide(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            iid = run.trigger("fire")
            await run.until(lambda: run.asking(iid))
            await wait_for(lambda: run.bot.profile.asking() == [iid])
            switch.say(OPS, OTHER, "kazum", "leave")
            await wait_for(lambda: any("FORBIDDEN_ROLE" in t for t in texts(switch)))
            assert run.asking(iid)

    asyncio.run(asyncio.wait_for(go(), 90))


def test_final_report_is_tidy_and_alone_holds_the_ids(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path, agents=False) as run:
            await full_incident(run, "fire")

    asyncio.run(asyncio.wait_for(go(), 90))
    report = in_room(switch, OPS)[-1]
    for part in (
        "Response: first eyes on in ",
        "Who went: fire engine",
        "Recording: ",
        "Timeline:",
    ):
        assert part in report
    assert report.startswith("✅") and "(simulated)" in report
    assert re.search(r"Recording: \d\d:\d\d(:\d\d)? to ", report), report


def test_no_hold_is_ever_reported(tmp_path):
    seen, room = [], _Recorder()

    async def go():
        bot = _bot(room, tmp_path, calls=seen)
        alert = {"incident": "I", "kind": "fire"}
        text = "Fire reported at X."
        await bot.handle_event({"seq": 1, "type": "city_alert", "text": text, "payload": alert})
        p = {"incident": "I"}
        await bot.handle_event({"seq": 2, "type": "city_eyes_on", "text": "y", "payload": p})
        await bot.aclose()

    asyncio.run(go())
    assert len(room.out) == 2 and not [c for c in seen if "agent-activity" in c[0]]


ESCORT = [
    (
        "care_assessed",
        "2 injured, condition needs the Hospital. Recommend transport.",
        {"recommend": "transport", "injuries": 2},
    ),
    ("city_decision", "x", {"kind": "treat_transport", "role": "duty_doctor"}),
    ("transport_started", "Ambulance leaves.", {}),
    ("traffic_blocked", "Traffic at (2.25, 3).", {"at": [2.25, 3.0]}),
    ("police_requested", "Police asked.", {"at": [2.25, 3.0]}),
    ("traffic_cleared", "Junction clear.", {"at": [2.25, 3.0]}),
    ("patient_delivered", "Handed over.", {}),
]


def _escort_events(bot, iid="I"):
    alert = {"incident": iid, "kind": "accident"}
    text = "Accident reported at Ring Road."
    yield {"seq": 1, "type": "city_alert", "text": text, "payload": alert}
    for n, (kind, line, p) in enumerate(ESCORT, 2):
        yield {"seq": n, "type": kind, "text": line, "payload": {"incident": iid, **p}}


class _Rooms:
    def __init__(self):
        self.out = []

    async def post(self, room, text):
        self.out.append((room, text))
        return "x"

    async def on_sim_time(self, t):
        return None


def test_escort_lines_rooms_and_wording(tmp_path):
    room = _Rooms()

    async def go():
        bot = _bot(room, tmp_path)
        bot.profile.doctor = "<@7>"
        for e in _escort_events(bot):
            await bot.handle_event(e)
        await bot.aclose()

    asyncio.run(go())
    inc = [t for r, t in room.out if r == "#incident-1"]
    ops = [t for r, t in room.out if r == "#ops"]
    assert len(ops) == 1 and "Crews are working" in ops[0]  # #ops is info only (L63)
    assert not [t for t in inc if t.startswith("🙋")]  # the assessment line is the call
    assert len(inc) == 5 and len(inc) + 2 <= 7  # plus Police's "on it" and the doctor's confirm
    assert "<@7>" in inc[0] and "Recommend transport" in inc[0]
    assert "escorting the ambulance, route clear" in inc[1]
    assert "@police ambulance stuck near " in inc[2] and "please clear it" in inc[2]
    assert not [t for t in inc if HUMAN_ID.search(t)], inc
    assert all(t.split(": ")[0].endswith("Ring Road accident") for t in inc)
    assert "Delivered" in inc[-1]


def test_doctor_decides_and_words_do_not_cross_calls(tmp_path):
    room, seen = _Rooms(), []

    async def go():
        bot = _bot(
            room, tmp_path, calls=seen, humans={"lead": ("incident_lead",), "doc": ("duty_doctor",)}
        )
        for e in list(_escort_events(bot))[:3]:
            await bot.handle_event(e)
        await bot.handle_message(_msg("doc", "leave"))
        await bot.handle_message(_msg("lead", "transport"))
        assert [c for c in seen if c[0] == "/city/decide"] == []
        await bot.handle_message(_msg("doc", "transport", room="#incident-1"))
        await bot.handle_message(_msg("doc", "treat"))  # already answered: chat now
        await bot.aclose()

    asyncio.run(go())
    calls = [c for c in seen if c[0] == "/city/decide"]
    assert len(calls) == 1 and b'"choice":"transport"' in calls[0][2]
    assert any("needs duty_doctor" in t for _r, t in room.out)
    assert any("Duty Doctor confirms transport" in t for _r, t in room.out)


def test_refused_transport_keeps_the_call_open(tmp_path):
    room = _Rooms()

    def handler(req):
        if req.url.path == "/city/decide":
            return httpx.Response(404, json={"ok": False, "code": "NOT_FOUND", "reason": "no"})
        return httpx.Response(200, json={"pilots": []})

    async def go():
        bot = _bot(room, tmp_path)
        await bot.client.aclose()
        bot.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://s")
        for e in list(_escort_events(bot))[:3]:
            await bot.handle_event(e)
        await bot.handle_message(_msg("kazum", "transport"))
        assert bot.profile.asking() == ["I"]
        await bot.aclose()

    asyncio.run(go())
    assert not [t for _r, t in room.out if "confirms" in t]


def test_two_transport_calls_use_place_words(tmp_path):
    room, seen = _Rooms(), []

    async def go():
        bot = _bot(room, tmp_path, calls=seen)
        for iid, place, base in (("A", "Ring Road", 0), ("B", "North Road", 20)):
            alert = {"incident": iid, "kind": "accident"}
            text = f"Accident reported at {place}."
            await bot.handle_event(
                {"seq": base + 1, "type": "city_alert", "text": text, "payload": alert}
            )
            for n, (k, line, p) in enumerate(ESCORT[:2], base + 2):
                e = {"seq": n, "type": k, "text": line, "payload": {"incident": iid, **p}}
                await bot.handle_event(e)
        await bot.handle_message(_msg("kazum", "transport"))
        assert any("`transport ring` or `treat ring`" in t for _r, t in room.out)
        await bot.handle_message(_msg("kazum", "treat north"))
        await bot.aclose()

    asyncio.run(go())
    calls = [c for c in seen if c[0] == "/city/decide"]
    assert len(calls) == 1 and b'"incident":"B"' in calls[0][2]
    assert any("`transport north` or `treat north`" in t for _r, t in room.out)


def test_real_escort_counts_and_report(switch, city_sim, tmp_path):
    """Accidents through the real sim until one is transported, with the room counted."""

    async def go():
        async with Run(switch, city_sim, tmp_path) as run:
            for _ in range(10):
                before = len(in_room(switch, INC1))
                await full_incident(run, "accident")
                mine = in_room(switch, INC1)[before:]
                if any("Delivered" in t for t in mine):
                    assert len(mine) <= 7 + 2 + 1, mine  # + the lead's call (L63)
                    return mine
            pytest.fail("no transport in 10 accidents")

    mine = asyncio.run(asyncio.wait_for(go(), 150))
    report = next(t for t in reversed(in_room(switch, OPS)) if ": closed." in t)
    assert "Care: transport" in report and re.search(r"Transport: [\d.]+ min, delay", report)
    assert not [t for t in mine if HUMAN_ID.search(t)], mine
    assert any(re.search(r"Drone Ops \(D-\d+\) escorting the ambulance", t) for t in mine), mine


def test_random_fire_posts_nothing_until_detected_then_one_heat_line(switch, city_sim, tmp_path):
    async def go():
        async with Run(switch, city_sim, tmp_path) as run:
            r = run.c.post("/city/trigger", json={"kind": "random_fire"}, headers=H)
            assert r.status_code == 200, r.text
            await asyncio.sleep(0.3)
            assert switch.posted == []  # hidden: nobody knows yet
            types = set()
            for _ in range(200):
                types |= {e["type"] for e in run.view()["events"]}
                if "heat_possible" in types:
                    break
                assert switch.posted == []
                await run.step()
            assert "heat_possible" in types
            await wait_for(lambda: switch.posted)
            first = texts(switch)[0]  # the heat line comes before anything in #ops
            assert first.startswith("🌡") and switch.posted[0][0] == switch.room_id(INC1)
            heat = [t for t in in_room(switch, INC1) if t.startswith("🌡")]
            assert len(heat) == 1 and heat[0].startswith("🌡 D-")
            assert heat[0].endswith("on patrol: possible heat source, checking (simulated sensor)")
            for _ in range(200):
                if any("found by a drone on patrol" in t for t in in_room(switch, OPS)):
                    break
                await run.step()
            start = next(t for t in in_room(switch, OPS) if "found by a drone" in t)
            assert start.startswith("🔥 ") and "#incident-1" in start
            iid = next(i["id"] for i in run.view()["incidents"] if i.get("found_by"))
            while iid in [i["id"] for i in run.view()["incidents"]]:
                d = next(i.get("decision") for i in run.view()["incidents"] if i["id"] == iid)
                if d:
                    await answer(run, iid, "leave", OPS)
                await run.step()
            await run.step()

    asyncio.run(asyncio.wait_for(go(), 120))
    assert sum("🌡" in t for t in texts(switch)) == 1
    assert counts(switch, OPS) == 2, in_room(switch, OPS)  # start + report (L63)
    assert counts(switch, INC1) <= 1 + 6, in_room(switch, INC1)
    assert in_room(switch, INC2) == []


def test_heat_room_is_kept_for_the_fire_and_repeats_are_dropped(tmp_path):
    room = _Rooms()

    async def go():
        bot = _bot(room, tmp_path)
        busy = {"incident": "A", "kind": "accident"}
        text = "Accident reported at Ring Road."
        await bot.handle_event({"seq": 1, "type": "city_alert", "text": text, "payload": busy})
        heat = {"drone": "D-07", "confidence_pct": 40, "sensor": "simulated"}
        for n in (2, 3):
            await bot.handle_event(
                {"seq": n, "type": "heat_possible", "text": "x", "payload": heat}
            )
        confirm = {"drone": "D-07", "confidence_pct": 90}
        await bot.handle_event(
            {"seq": 4, "type": "fire_confirmed", "text": "x", "payload": confirm}
        )
        fire = {"incident": "B", "kind": "fire"}
        text = "Fire reported at Mill Lane."
        await bot.handle_event({"seq": 5, "type": "city_alert", "text": text, "payload": fire})
        await bot.aclose()

    asyncio.run(go())
    assert [r for r, t in room.out if t.startswith("🌡")] == ["#incident-2"]
    assert room.out[-1] == (
        "#ops",
        "🔥 Mill Lane fire, found by a drone on patrol. Crews are working in #incident-2.",
    )
    assert not [t for _r, t in room.out if "fire_confirmed" in t or "confirms" in t]


def test_load_ping_config(tmp_path):
    p = tmp_path / "h.yaml"
    doc = {"humans": [{"discord": "kazum", "roles": ["incident_lead"]}]}
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert load_lead(p) == "@kazum"
    doc["humans"][0]["discord_mention"] = "<@42>"
    p.write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert load_lead(p) == "<@42>"


def test_agents_are_tiny_and_installed():
    for name in rooms.CITY_AGENTS:
        text = (ROOT / "agents" / name / "CLAUDE.md").read_text(encoding="utf-8")
        assert len(text) < 1250 and "airgrid city --place" in text and "simulated" in text
        assert "reply in that same room" in text and "on scene or done" in text
        env = (ROOT / "agents" / name / ".env.example").read_text(encoding="utf-8")
        assert f"AIRGRID_ROLE={name}" in env
    src = (ROOT / "scripts" / "install_agents.sh").read_text(encoding="utf-8")
    assert all(f'"{n}"' in src for n in rooms.CITY_AGENTS)
    assert "Drone Ops" in (ROOT / "agents/dispatch/CLAUDE.md").read_text(encoding="utf-8")


def test_room_table_and_todo():
    doc = yaml.safe_load((ROOT / "agents/switch/agent-rooms.yaml").read_text(encoding="utf-8"))
    assert set(doc["city"]) == {*city_profile.AGENTS, "worldbot"}
    todo = (ROOT / "HUMAN_TODO.md").read_text(encoding="utf-8")
    assert "## City mode" in todo and "airgrid-agents\\fire-station" in todo
    ops_text = (ROOT / "agents/switch/rooms/ops.txt").read_text(encoding="utf-8")
    assert "information only" in ops_text and "dashboard" not in ops_text
    inc_text = (ROOT / "agents/switch/rooms/incident-city.txt").read_text(encoding="utf-8")
    assert "`leave market`" in inc_text
    assert "incident-city.txt" in todo and (ROOT / "agents/switch/rooms/incident-city.txt").exists()


def test_cli_city_gives_the_eta(city_sim):
    from tests.integration.harness import base_env as env

    with httpx.Client(base_url=city_sim.url, timeout=30) as c:
        c.post("/city/start", json={"paused": True}, headers=H)
        inc = c.post("/city/trigger", json={"kind": "fire"}, headers=H).json()["incident"]
        iid, name = inc["id"], inc["name"]
        c.post("/control/skip", json={"to": "09:06"})
    r = subprocess.run(
        ["uv", "run", "airgrid", "city", "--place", name.split()[0]],
        cwd=ROOT,
        env={**env(), "AIRGRID_API_URL": city_sim.url},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    import json

    body = json.loads(r.stdout)
    assert body["incidents"][0]["id"] == iid
    assert list(body["incidents"][0]["eta_min"].values())[0] > 0
    assert "drones" not in body


def test_make_tokens_missing_adds_and_keeps(tmp_path):
    from sim.auth import add_missing_tokens

    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump({"agents": {"dispatch": "keep"}, "humans": {"k": "h"}}), "utf-8")
    assert "police" in add_missing_tokens(p)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert doc["agents"]["dispatch"] == "keep" and doc["humans"] == {"k": "h"}
    assert doc["agents"]["fire-station"]


def test_run_game_defaults_to_city_and_key_stays_in_the_url():
    port = free_port()
    proc = subprocess.Popen(
        [bash(), str(RUN_GAME), "--port", str(port)],
        cwd=ROOT,
        env={**base_env(), "AIRGRID_DEMO_SECONDS": "5"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    out, key = [], None
    for line in proc.stdout:
        out.append(line)
        m = re.search(r"/ui/\?key=([\w-]+)", line)
        if m:
            key = m.group(1)
            break
    assert key, out
    assert httpx.get(f"http://127.0.0.1:{port}/city").json()["started"] is True
    text = "".join(out) + proc.stdout.read()
    proc.wait(timeout=30)
    assert text.count(key) == 1


class _Recorder:
    def __init__(self):
        self.out = []

    async def post(self, room, text):
        self.out.append(text)
        return "x"

    async def on_sim_time(self, t):
        return None


def _msg(author, text, room="#ops"):
    from worldbot.transport import RoomMessage

    return RoomMessage(room, author, text)


def _bot(room, tmp_path, humans=None, key="k", calls=None, profile="city"):
    calls = [] if calls is None else calls

    def handler(req):
        calls.append((req.url.path, req.headers.get("x-game-key"), req.content))
        return httpx.Response(200, json={"ok": True, "pilots": []})

    return WorldBot(
        room,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://s"),
        humans={"kazum": ROLES} if humans is None else humans,
        tokens={},
        sink=FakeSink(tmp_path / "d.jsonl"),
        approval_role={"track": "watch_commander"},
        profile=CityProfile("@k") if profile else None,
        operator_key=key or " ".strip(),
    )


def test_fleet_lines_go_to_fleet_without_mentions(tmp_path):
    """L64: the whole fleet in #fleet, plain lines, no @mention, so no agent wakes."""
    room = _Rooms()
    events = [
        {
            "seq": 1,
            "type": "fleet_board",
            "text": "9 in the air (9 on patrol) · 3 charging · 2 ready.",
            "payload": {},
        },
        {
            "seq": 2,
            "type": "fleet_alert",
            "text": "D-04 heads to a dock at 35% from patrol route 2.",
            "payload": {"drone": "D-04"},
        },
    ]

    async def go():
        bot = _bot(room, tmp_path)
        for e in events:
            await bot.handle_event(e)
        await bot.aclose()

    asyncio.run(go())
    assert [r for r, _t in room.out] == ["#fleet", "#fleet"]
    assert room.out[0][1].startswith("🛰 Fleet: 9 in the air")
    assert room.out[1][1].startswith("⚠️ Fleet: D-04")
    assert not [t for _r, t in room.out if "@" in t]


def test_no_fleet_room_skips_fleet_lines_quietly(tmp_path):
    room = _Rooms()
    room.rooms = {"#ops": "1", "#incident-1": "2", "#incident-2": "3"}  # a server without #fleet

    async def go():
        bot = _bot(room, tmp_path)
        await bot.handle_event({"seq": 1, "type": "fleet_board", "text": "x", "payload": {}})
        await bot.aclose()

    asyncio.run(go())
    assert room.out == []


def test_fleet_log_posts_rows_to_fleet(tmp_path):
    room = _Rooms()
    rows = "09:17 D-13 takes off for patrol (100%)\n09:19 D-07 heads to dock P7 (44%)"

    async def go():
        bot = _bot(room, tmp_path)
        await bot.handle_event({"seq": 1, "type": "fleet_log", "text": rows, "payload": {}})
        await bot.aclose()

    asyncio.run(go())
    assert room.out == [("#fleet", rows)]


def test_orders_reach_the_sim_only_from_the_watch_commander(tmp_path):
    """L67: `watch market`, `dock D-4` from the mapped watch_commander; chat stays chat."""
    room, seen = _Rooms(), []

    async def go():
        bot = _bot(
            room,
            tmp_path,
            calls=seen,
            humans={"boss": ("watch_commander",), "doc": ("duty_doctor",)},
        )
        await bot.handle_message(_msg("doc", "watch market"))
        await bot.handle_message(_msg("boss", "watch out!"))
        await bot.handle_message(_msg("boss", "watch market 30 min"))
        await bot.handle_message(_msg("boss", "dock D-4"))
        await bot.handle_message(_msg("boss", "release d04"))
        await bot.aclose()

    asyncio.run(go())
    orders = [c for c in seen if c[0] == "/city/order"]
    assert len(orders) == 3 and all(c[1] == "k" for c in orders)
    assert b'"place":"market"' in orders[0][2] and b'"minutes":30.0' in orders[0][2]
    assert b'"drone":"D-04"' in orders[1][2] and b'"order":"release"' in orders[2][2]
    assert any("needs watch_commander" in t for _r, t in room.out)


def test_order_events_go_to_fleet(tmp_path):
    room = _Rooms()

    async def go():
        bot = _bot(room, tmp_path)
        e = {
            "seq": 1,
            "type": "city_order",
            "text": "D-03 watches Market Street until 10:05.",
            "payload": {},
        }
        await bot.handle_event(e)
        await bot.aclose()

    asyncio.run(go())
    assert room.out == [("#fleet", "📌 Fleet: D-03 watches Market Street until 10:05.")]
