"""`--profile game` on the fake Switch with a real sim running real games (docs/game-mode.md)."""

import asyncio
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sim.geo import fmt, tmin
from tests.integration.conftest import HUMANS_PATH, TOKENS_PATH
from tests.integration.fake_switch import FakeSwitch
from tests.integration.harness import SimProcess, base_env, free_port
from tests.integration.unit.test_install_agents import bash
from tests.sim.helpers import ROOT, TOKENS_DOC
from worldbot import game_profile
from worldbot.bot import WorldBot
from worldbot.decisions_sink import SwitchSink
from worldbot.game_profile import GameProfile
from worldbot.switch_room import SwitchConfig, SwitchRoom

KEY = "game-key-for-tests"
H = {"X-Game-Key": KEY}
KAZUM = "@switch-user-kazum01:airgrid.local"
ROLES = ("watch_commander", "remote_pilot", "field_officer")
RUN_GAME = ROOT / "scripts" / "run_game.sh"
SAFE = {"crash": "keep", "robbery": "reject", "trap_face": "lawful", "trap_route": "lawful"}
BOLD = {"crash": "release", "robbery": "approve", "trap_face": "try", "trap_route": "try"}
FORBIDDEN = re.compile(
    r"rotation|ROTATION|RTB|landing|LANDING|feed:|patrol_car_\d → |unit_|"
    r"DOCK|WEATHER|MISSION_EXPIRED"
)


@pytest.fixture(scope="module")
def game_sim(tmp_path_factory):
    proc = SimProcess(tmp_path_factory.mktemp("game"), TOKENS_PATH, HUMANS_PATH)
    proc.env["AIRGRID_GAME_KEY"] = KEY
    proc.start()
    yield proc
    proc.stop()


@pytest.fixture
def switch():
    fake = FakeSwitch(("AirGrid: ops", "AirGrid: privacy-log"))
    fake.start()
    yield fake
    fake.stop()


def drive(url: str, choices: dict, on_prompt=None) -> dict:
    """A player: one paused game, stepped a sim-minute at a time, every prompt answered."""
    with httpx.Client(base_url=url, timeout=30) as c:
        assert c.post("/game/start", json={"paused": True}, headers=H).status_code == 200
        while True:
            v = c.get("/game", headers=H).json()
            if v["done"]:
                return c.get("/game/score", headers=H).json()
            if v["beat"]:
                if on_prompt and on_prompt(c, v):
                    continue
                r = c.post(
                    "/game/choose", json={"choice": choices.get(v["beat"], "leave")}, headers=H
                )
                assert r.status_code == 200, r.text
            else:
                assert c.post("/control/skip", json={"to": fmt(tmin(v["clock"]) + 1)}, headers=H)


async def wait_for(check, timeout=20.0):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.02)


def posted(fake: FakeSwitch) -> list[tuple[str, str]]:
    return [(fake.rooms[r], t) for r, t in fake.posted]


def play_on_switch(fake, sim, choices, on_prompt=None, tmp_path=None) -> list[tuple[str, str]]:
    async def go():
        cfg = SwitchConfig(fake.url, fake.agent_id, fake.api_key, "conn-g")
        room = SwitchRoom(cfg, {KAZUM: "kazum"}, backoff_s=0.01, home_room="#ops")
        await room.start(game_profile.ROOMS)
        tokens = {"kazum": TOKENS_DOC["humans"]["kazum"]}
        bot = WorldBot(
            room,
            sim.url,
            humans={"kazum": ROLES},
            tokens=tokens,
            sink=SwitchSink(room, tmp_path / "d.jsonl", room_name="#ops"),
            profile=GameProfile(),
        )
        task = asyncio.ensure_future(bot.run())
        await wait_for(lambda: room._connected.is_set() and bot._synced)
        score = await asyncio.to_thread(drive, sim.url, choices, on_prompt)
        await wait_for(lambda: bot.profile.over)
        await asyncio.sleep(0.4)  # anything the fast-forward would wrongly post lands here
        bot.stop()
        await task
        await bot.aclose()
        await room.aclose()
        return score

    assert asyncio.run(asyncio.wait_for(go(), 90))["done"] is True
    return posted(fake)


@pytest.mark.parametrize(
    ("choices", "low", "high"),
    [(BOLD, 15, 15), (SAFE, 12, 15)],
    ids=["bold", "safe"],
)
def test_a_shift_is_about_15_messages_in_two_rooms_with_only_the_moments(
    switch, game_sim, tmp_path, choices, low, high
):
    lines = play_on_switch(switch, game_sim, choices, tmp_path=tmp_path)
    print(f"\n{len(lines)} messages")
    assert low <= len(lines) <= high, lines
    assert {r for r, _ in lines} <= {"AirGrid: ops", "AirGrid: privacy-log"}
    assert not [t for _r, t in lines if FORBIDDEN.search(t)]
    mentions = {m for _r, t in lines for m in re.findall(r"@(\w+)", t)}
    assert mentions - {"kazum"} <= set(game_profile.AGENTS)
    assert len({t for _r, t in lines}) == len(lines), "a moment posted twice"
    assert any("INC-2 closed" in t for _r, t in lines)
    assert any("(simulated)" in t for _r, t in lines)


def test_typed_approve_in_ops_decides_the_track_and_the_game_follows(switch, game_sim, tmp_path):
    def on_prompt(c, v):
        if v["beat"] != "robbery":
            return False
        aid = v["approval_id"]
        end = time.monotonic() + 10
        while not any(f"🟡 APPROVAL {aid}" in t for _r, t in posted(switch)):
            assert time.monotonic() < end
            time.sleep(0.02)
        switch.say("AirGrid: ops", KAZUM, "anyone", f"approve {aid}")
        while c.get("/game", headers=H).json()["beat"] == "robbery":
            assert time.monotonic() < end
            time.sleep(0.02)
        return True

    lines = play_on_switch(switch, game_sim, BOLD, on_prompt, tmp_path)
    ops = [t for r, t in lines if r == "AirGrid: ops"]
    assert any(re.match(r"✅ APPROVED A-\d+ by @kazum", t) for t in ops)
    assert any("🔐 track approved by kazum" in t for r, t in lines if r == "AirGrid: privacy-log")


def test_play_again_posts_a_second_shift(switch, game_sim, tmp_path):
    async def go():
        cfg = SwitchConfig(switch.url, switch.agent_id, switch.api_key, "conn-g")
        room = SwitchRoom(cfg, {}, backoff_s=0.01, home_room="#ops")
        await room.start(game_profile.ROOMS)
        bot = WorldBot(
            room,
            game_sim.url,
            humans={},
            tokens={},
            sink=SwitchSink(room, tmp_path / "d.jsonl"),
            profile=GameProfile(),
        )
        task = asyncio.ensure_future(bot.run())
        await wait_for(lambda: room._connected.is_set() and bot._synced)
        await asyncio.to_thread(drive, game_sim.url, SAFE)
        await wait_for(lambda: bot.profile.over)
        first = len(switch.posted)
        await asyncio.to_thread(drive, game_sim.url, SAFE)
        await wait_for(lambda: len(switch.posted) >= 2 * first - 1)
        bot.stop()
        await task
        await bot.aclose()
        await room.aclose()
        return first

    first = asyncio.run(asyncio.wait_for(go(), 90))
    assert first >= 12


def test_switch_down_mid_game_the_game_still_finishes(switch, game_sim, tmp_path):
    switch.fail_posts = 10**6

    async def go():
        cfg = SwitchConfig(switch.url, switch.agent_id, switch.api_key, "conn-g")
        room = SwitchRoom(cfg, {}, backoff_s=0, home_room="#ops")
        await room.start(game_profile.ROOMS)
        bot = WorldBot(
            room,
            game_sim.url,
            humans={},
            tokens={},
            sink=SwitchSink(room, tmp_path / "d.jsonl"),
            profile=GameProfile(),
        )
        task = asyncio.ensure_future(bot.run())
        await wait_for(lambda: bot._synced)
        score = await asyncio.to_thread(drive, game_sim.url, BOLD)
        await wait_for(lambda: room.post_failures > 0)
        assert not task.done()
        bot.stop()
        await task
        await bot.aclose()
        await room.aclose()
        return score

    assert asyncio.run(asyncio.wait_for(go(), 90))["score"] > 0
    assert switch.posted == []


def test_no_bot_at_all_the_game_finishes(game_sim):
    assert drive(game_sim.url, BOLD)["done"] is True


def test_agent_hold_in_a_game_is_capped_at_8_s(monkeypatch, tmp_path):
    from sim.app import S, app

    monkeypatch.setenv("AIRGRID_GAME_KEY", KEY)
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "g.db"))
    with TestClient(app) as c:
        assert c.post("/game/start", json={"paused": True}, headers=H).status_code == 200
        body = {"agent": "dispatch", "state": "busy"}
        assert c.post("/control/agent-activity", json=body).status_code == 200
        expiry, _approval = S.clock._holds["agent:dispatch"]
        assert expiry - S.clock.time() <= 8.0


def test_only_clock_running_alerts_are_beats_and_they_belong_to_game_agents():
    p = GameProfile()
    a = p.moment({"type": "cad_alert", "payload": {"incident": "INC-1"}, "text": "x"})
    assert a.beat and a.agent in game_profile.AGENTS
    refusal = {"type": "refusal", "payload": {"code": "NO_FLY_ZONE"}, "text": "x"}
    assert not p.moment(refusal).beat


def test_flying_moments_block_once_catch_ends_it_and_the_shift_after_is_silent():
    p = GameProfile()
    blocked = {"type": "game_blocked", "payload": {}, "text": "⛔ NO_FLY_ZONE"}
    assert p.moment(blocked).agent == "airspace"
    assert p.moment(blocked) is None
    assert p.moment({"type": "game_catch", "payload": {}, "text": "caught"}).ends
    assert p.moment({"type": "incident_closed", "payload": {"incident": "INC-2"}}) is None
    assert p.moment({"type": "cad_alert", "payload": {"incident": "INC-3"}}) is None


def test_ops_instructions_and_game_room_table():
    text = (ROOT / "agents/switch/rooms/ops-game.txt").read_text(encoding="utf-8")
    assert "simulated" in text and "approve A-000n" in text
    todo = (ROOT / "HUMAN_TODO.md").read_text(encoding="utf-8")
    assert "## Game mode" in todo and "rooms/ops-game.txt" in todo


def test_run_game_key_only_in_the_url_and_only_in_the_sims_env():
    port = free_port()
    proc = subprocess.Popen(
        [bash(), str(RUN_GAME), "--game", "--port", str(port)],
        cwd=ROOT,
        env={**base_env(), "AIRGRID_DEMO_SECONDS": "6"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    out = []
    key = None
    for line in proc.stdout:
        out.append(line)
        m = re.search(rf"http://127\.0\.0\.1:{port}/ui/\?key=([\w-]+)", line)
        if m:
            key = m.group(1)
            break
    assert key and len(key) >= 32, out
    url = f"http://127.0.0.1:{port}"
    assert httpx.get(f"{url}/game").status_code == 403
    assert httpx.get(f"{url}/game", headers={"X-Game-Key": key}).status_code == 200
    rest = proc.stdout.read()
    proc.wait(timeout=30)
    text = "".join(out) + rest
    assert text.count(key) == 1
    logs = sorted((ROOT / "runs").glob("game-sim-*.log"))
    assert key not in logs[-1].read_text(encoding="utf-8", errors="replace")


def test_run_game_keeps_the_key_from_the_game_bot():
    src = RUN_GAME.read_text(encoding="utf-8")
    assert "export AIRGRID_GAME_KEY" not in src
    assert 'AIRGRID_GAME_KEY="$key" uv run uvicorn' in src
    assert '--switch --profile "$profile"' in src
    assert '[ "$profile" = city ] && bot_key=$key' in src
    assert Path(RUN_GAME).stat().st_size


def test_a_bot_joining_after_a_finished_game_posts_nothing(switch, game_sim, tmp_path):
    drive(game_sim.url, SAFE)

    async def go():
        cfg = SwitchConfig(switch.url, switch.agent_id, switch.api_key, "conn-g")
        room = SwitchRoom(cfg, {}, backoff_s=0.01, home_room="#ops")
        await room.start(game_profile.ROOMS)
        sink = SwitchSink(room, tmp_path / "d.jsonl")
        bot = WorldBot(room, game_sim.url, humans={}, tokens={}, sink=sink, profile=GameProfile())
        task = asyncio.ensure_future(bot.run())
        await wait_for(lambda: bot._synced)
        await asyncio.sleep(0.3)
        bot.stop()
        await task
        await bot.aclose()
        await room.aclose()

    asyncio.run(asyncio.wait_for(go(), 30))
    assert switch.posted == []


def test_full_profile_is_unchanged():
    assert WorldBot.__init__.__kwdefaults__["profile"] is None
