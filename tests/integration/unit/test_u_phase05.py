"""Phase 05 unit tests (integration-engineer): CLI seams, sim additions, bot parts, FakeRoom."""

from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cli import airgrid as cli
from sim import app as sim_app
from sim import rules
from sim.engine import Engine
from worldbot import cards, lines, rooms
from worldbot.bot import WorldBot, load_human_tokens
from worldbot.decisions_sink import FakeSink
from worldbot.fake_room import FakeRoom, parse_line
from worldbot.transport import RoomMessage

SECRET = "sekret-token-value"
TOKENS = {
    "agents": {"dispatch": "tok-dispatch", "fleet": "tok-fleet", "comms": "tok-comms"},
    "humans": {"kazum": SECRET},
}
HUMANS = {"humans": [{"discord": "kazum", "roles": ["watch_commander", "remote_pilot"]}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    t, h = tmp_path / "tokens.yaml", tmp_path / "humans.yaml"
    t.write_text(yaml.safe_dump(TOKENS), encoding="utf-8")
    h.write_text(yaml.safe_dump(HUMANS), encoding="utf-8")
    monkeypatch.setenv("AIRGRID_TOKENS", str(t))
    monkeypatch.setenv("AIRGRID_HUMANS", str(h))
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "t.db"))
    c = TestClient(sim_app.app)
    assert c.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "demo"}).status_code == 200
    return c


def act(c, token, action, args):
    return c.post(
        "/actions",
        json={"action": action, "args": args},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------- engine: the actions slot
def test_run_to_actions_stops_inside_the_tick_and_step_resumes():
    e = Engine.load("FRIDAY-RUSH", mode="demo", golden=False)
    e.run_to_actions("17:12")
    assert e.now == pytest.approx(17 * 60 + 12)
    assert "INC-1" in e.incidents  # the tick's events ran, its movement has not
    before = e.drones["D-02"].pos
    e.run_to_actions("17:12")  # no-op
    assert e.drones["D-02"].pos == before
    e.step()
    assert e._t == pytest.approx(17 * 60 + 12.1)


def test_split_tick_changes_nothing():
    a = Engine.load("FRIDAY-RUSH", mode="headless")
    a.run()
    b = Engine.load("FRIDAY-RUSH", mode="headless")
    while not b.done:
        b.run_to_actions(b._t)
        b.step()
    assert a.log.hash() == b.log.hash()


# ---------------------------------------------------------------- sim additions
def test_agent_activity_holds_and_releases(client):
    r = client.post("/control/agent-activity", json={"agent": "dispatch", "state": "busy"})
    assert r.json()["held"] == ["agent:dispatch"]
    r = client.post("/control/agent-activity", json={"agent": "dispatch", "state": "idle"})
    assert r.json()["held"] == []


def test_agent_activity_rejects_a_bad_state(client):
    r = client.post("/control/agent-activity", json={"agent": "dispatch", "state": "?"})
    assert r.status_code == 422


def test_log_refusal_writes_a_timeline_line(client):
    r = act(client, "tok-dispatch", "log_refusal", {"about": "keep it up", "code": "AUTH_REQUIRED"})
    assert r.status_code == 200
    last = client.get("/events").json()["events"][-1]
    assert (last["type"], last["text"]) == ("refusal", "⛔ AUTH_REQUIRED: 'keep it up'")


def test_simulate_propose_queues_nothing(client):
    args = {
        "action": "propose",
        "args": {"type": "track", "args": {"incident": "INC-9", "scope": "x; 30 min"}},
    }
    r = act(client, "tok-dispatch", "simulate", args)
    assert r.json()["result"]["would"]["code"] == "NOT_FOUND"
    assert client.get("/approvals").json()["approvals"] == []


def test_rtb_of_a_docked_drone_is_rejected(client):
    r = act(client, "tok-fleet", "rtb", {"drone": "D-12"})
    assert (r.status_code, r.json()["code"]) == (409, "DRONE_NOT_READY")


@pytest.mark.parametrize(
    "path,code",
    [
        ("/plan/rotation?loop=NOPE", "NOT_FOUND"),
        ("/route/check?drone=D-99&to=1,1", "NOT_FOUND"),
        ("/route/check?drone=D-12&to=5.8,3.4", "NO_FLY_ZONE"),
        ("/track/status?incident=INC-9", "NOT_FOUND"),
        ("/relay/plan?incident=INC-9", "NOT_FOUND"),
    ],
)
def test_projection_rejections(client, path, code):
    r = client.get(path)
    assert r.status_code in (404, 409)
    assert r.json()["code"] == code and set(r.json()) == {"ok", "code", "reason", "hint"}


def test_projections_change_nothing(client):
    before = client.get("/decisions").json()["decisions"]
    client.get("/route/check?drone=D-12&to=5.8,3.4")
    client.get("/plan/rotation")
    assert client.get("/decisions").json()["decisions"] == before
    assert client.get("/status").json()["kpi"]["refusals"] == 0


def test_route_check_eta_is_rounded_up(client):
    b = client.get("/route/check?drone=D-12&to=2.0,2.0").json()
    m, s = b["eta"].split(":")
    assert (int(m) * 60 + int(s)) / 60 >= b["eta_min"] - 0.01


# ---------------------------------------------------------------- CLI seams
def run_cli(monkeypatch, client, *argv, token=None):
    def fake(method, path, **kw):
        return client.request(method, path, headers=cli._headers(), **kw)

    monkeypatch.setattr(cli, "_request", fake)
    if token:
        monkeypatch.setenv("AIRGRID_TOKEN", token)
    return CliRunner().invoke(cli.app, list(argv))


def test_codes_match_the_contract():
    assert tuple(cli.CODES) == tuple(rules.CODES)


def test_cli_usage_error_exits_1_not_2(monkeypatch, client):
    r = run_cli(monkeypatch, client, "launch", "--drone", "D-05")
    assert r.exit_code == 1


def test_cli_unknown_command_exits_1(monkeypatch, client):
    assert run_cli(monkeypatch, client, "frobnicate").exit_code == 1


def test_cli_rejection_shape_and_exit(monkeypatch, client):
    r = run_cli(monkeypatch, client, "rtb", "--drone", "D-12", token="tok-fleet")
    assert r.exit_code == 2
    body = json.loads(r.stdout)
    assert set(body) == {"ok", "code", "reason", "hint"} and body["code"] == "DRONE_NOT_READY"


def test_cli_simulate_changes_nothing(monkeypatch, client):
    args = ("--drone", "D-05", "--mission", "incident", "--incident", "INC-9", "--to", "1,1")
    r = run_cli(monkeypatch, client, "simulate", "launch", *args, token="tok-dispatch")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["result"]["would"]["code"] == "NOT_FOUND"
    assert client.get("/fleet/D-05").json()["drone"]["state"] == "docked"


def test_cli_simulate_refuses_a_read(monkeypatch, client):
    assert run_cli(monkeypatch, client, "simulate", "fleet").exit_code == 1


def test_cli_message_commands_take_two_text_flags(monkeypatch, client):
    args = ("advisory", "--area", "J9", "--text", "lane closed", "--until", "18:00", "--text")
    r = run_cli(monkeypatch, client, *args, token="tok-dispatch")  # dispatch may not: still parsed
    assert r.exit_code == 2 and r.stdout.startswith("rejected FORBIDDEN_ROLE")


def test_cli_never_prints_the_token_on_transport_errors(monkeypatch):
    bad = "bad\nheader-" + SECRET  # an illegal header value that transports quote in errors
    monkeypatch.setenv("AIRGRID_TOKEN", bad)
    monkeypatch.setenv("AIRGRID_API_URL", "http://127.0.0.1:9")
    r = CliRunner().invoke(cli.app, ["status"])
    assert r.exit_code == 1 and SECRET not in r.stdout + (r.stderr or "")


# ---------------------------------------------------------------- bot parts
@pytest.mark.parametrize(
    "text,verb,target,reason",
    [
        ("approve A-0007", "approve", "A-0007", ""),
        ("  APPROVE a-0007 ", "approve", "A-0007", ""),
        ("FO: approve A-0007", "approve", "A-0007", ""),
        ("reject A-0007 too loud here", "reject", "A-0007", "too loud here"),
        ("RP: take control D-04", "take_control", "D-04", ""),
        ("release-control D-04", "release_control", "D-04", ""),
    ],
)
def test_parse_command(text, verb, target, reason):
    c = cards.parse_command(text)
    assert (c.verb, c.target, c.reason) == (verb, target, reason)


@pytest.mark.parametrize(
    "text", ["please don't approve A-0007", "approve A-0007 now", "approve", "approved A-0007"]
)
def test_chat_that_mentions_a_command_is_not_one(text):
    assert cards.parse_command(text) is None


def test_card_has_both_lines_of_the_format():
    a = {"id": "A-0007", "type": "track", "created_by": "dispatch", "summary": "INC-2 scooter"}
    card = cards.approval_card(a, "watch_commander")
    assert card.startswith("🟡 APPROVAL A-0007 · track · by @dispatch · needs: watch_commander\n")
    assert card.endswith("Reply: approve A-0007 | reject A-0007 <reason>")


def test_routes_cover_incident_rooms_and_overflow():
    assert rooms.incident_room("INC-2") == "#incident-2"
    assert rooms.incident_room("INC-T1") == "#dispatch"
    e = {"type": "feed_fact", "payload": {"incident": "INC-1"}}
    assert rooms.route(e).room == "#incident-1"
    e = {"type": "refusal", "payload": {"code": "PRIVACY_POLICY"}}
    assert rooms.route(e) == rooms.REFUSAL_ROUTES["PRIVACY_POLICY"]
    assert rooms.route({"type": "never_seen"}) == rooms.DEFAULT_ROUTE


def test_simulated_lines_are_labelled():
    pilots = {"DP-1": "Asha Menon"}
    e = {"type": "rotation", "payload": {"pilot": "DP-1", "drone": "D-03", "loop": "NORTH"}}
    assert lines.simulated_lines(e, pilots) == [
        '🛩 DP-1 Asha Menon (duty pilot, simulated): "D-03 is mine for NORTH. Lifting now."'
    ]
    e = {"type": "unit_on_scene", "payload": {"unit": "patrol_car_2", "incident": "INC-1"}}
    assert lines.simulated_lines(e, pilots) == ['📻 patrol_car_2 (simulated): "On scene at INC-1."']
    e = {"type": "rotation", "payload": {"pilot": "kazum"}}
    assert lines.simulated_lines(e, pilots) == []


def test_load_human_tokens_reads_humans_only(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(TOKENS), encoding="utf-8")
    assert load_human_tokens(p) == {"kazum": SECRET}
    assert load_human_tokens(tmp_path / "missing.yaml") == {}


def test_fake_sink_writes_jsonl(tmp_path):
    s = FakeSink(tmp_path / "d.jsonl")
    s.record({"kind": "approved", "target": "A-0001"})
    assert s.entries == [{"kind": "approved", "target": "A-0001"}]
    assert json.loads((tmp_path / "d.jsonl").read_text(encoding="utf-8"))["target"] == "A-0001"


def bot_with(handler, tmp_path, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sim")
    room = FakeRoom(interactive=False, out=io.StringIO())
    humans = kw.pop("humans", {"kazum": ("watch_commander",), "fo": ("field_officer",)})
    tokens = kw.pop("tokens", {"kazum": SECRET, "fo": "tok-fo"})
    sink = FakeSink(tmp_path / "d.jsonl")
    return WorldBot(room, client=client, humans=humans, tokens=tokens, sink=sink), room, sink


def test_agent_message_reports_idle_and_cannot_approve(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"ok": True, "approvals": []})

    bot, room, sink = bot_with(handler, tmp_path)
    asyncio.run(bot.handle_message(RoomMessage("#dispatch", "dispatch", "approve A-0001")))
    assert calls == ["/control/agent-activity"]
    assert "reason: FORBIDDEN_ROLE" in room.texts()[0]
    assert sink.entries[0]["kind"] == "refusal"


def test_wrong_role_never_reaches_the_sim(tmp_path):
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path))
        a = {"id": "A-0001", "type": "track", "status": "pending"}
        return httpx.Response(200, json={"ok": True, "approvals": [a]})

    bot, room, _ = bot_with(handler, tmp_path)
    asyncio.run(bot.handle_message(RoomMessage("#dispatch", "fo", "approve A-0001")))
    assert calls == [("GET", "/approvals")]
    assert "needs watch_commander" in room.texts()[0]


def test_reject_without_a_reason_is_not_sent(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={"ok": True})

    bot, room, _ = bot_with(handler, tmp_path)
    asyncio.run(bot.handle_message(RoomMessage("#dispatch", "kazum", "reject A-0001")))
    assert calls == [] and room.texts() == ["⚠ usage: reject A-0001 <reason>"]


def test_human_token_goes_in_the_header_only(tmp_path):
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        if req.method == "GET":
            a = {"id": "A-0001", "type": "track", "status": "pending"}
            return httpx.Response(200, json={"approvals": [a]})
        return httpx.Response(200, json={"ok": True, "result": {"type": "track", "status": "ok"}})

    bot, room, sink = bot_with(handler, tmp_path)
    asyncio.run(bot.handle_message(RoomMessage("#dispatch", "kazum", "approve A-0001")))
    assert f"Bearer {SECRET}" in seen
    assert SECRET not in " ".join(room.texts()) + json.dumps(sink.entries)


def test_sim_down_is_said_in_the_room_and_changes_nothing(tmp_path):
    def handler(req):
        raise httpx.ConnectError("down")

    bot, room, sink = bot_with(handler, tmp_path)
    asyncio.run(bot.handle_message(RoomMessage("#dispatch", "kazum", "approve A-0001")))
    assert room.texts()[0].startswith("⚠ approve A-0001 not done")
    assert sink.entries[0]["kind"] == "error"


def test_events_are_deduplicated_by_seq(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"ok": True, "pilots": []})

    bot, room, _ = bot_with(handler, tmp_path)
    e = {"seq": 1, "t": 1032.0, "type": "dock_restored", "text": "x", "payload": {}}

    async def go():
        await bot.handle_event(e)
        await bot.handle_event(e)

    asyncio.run(go())
    assert len(room.texts()) == 1


def test_beat_event_reports_busy_before_the_mention(tmp_path):
    order = []

    def handler(req):
        order.append(req.url.path)
        return httpx.Response(200, json={"ok": True, "pilots": []})

    bot, room, _ = bot_with(handler, tmp_path)
    room._on_post = lambda r, t: order.append("post")
    e = {"seq": 1, "t": 1032.0, "type": "cad_alert", "text": "x", "payload": {}}
    asyncio.run(bot.handle_event(e))
    assert order[:2] == ["/control/agent-activity", "post"]


# ---------------------------------------------------------------- FakeRoom
def test_parse_line():
    assert parse_line("kazum: approve A-0001") == RoomMessage(
        "#dispatch", "kazum", "approve A-0001"
    )
    assert parse_line("#airspace kazum: FO: hello").room == "#airspace"
    assert parse_line("no author here") is None
    assert parse_line("   ") is None


async def _collect(room):
    return [m async for m in room.messages()]


def test_fake_room_reads_stdin_lines():
    got = asyncio.run(_collect(FakeRoom(stdin=io.StringIO("kazum: approve A-0001\n\n"))))
    assert got == [RoomMessage("#dispatch", "kazum", "approve A-0001")]


def test_script_waits_for_sim_time():
    room = FakeRoom(
        script=[("17:10", "kazum", "#dispatch", "late"), ("17:05", "kazum", "#dispatch", "early")]
    )

    async def go():
        await room.on_sim_time(17 * 60 + 6)
        task = asyncio.create_task(_collect(room))
        await asyncio.sleep(0.01)
        assert not task.done()
        await room.on_sim_time(17 * 60 + 10)
        return await task

    assert [m.text for m in asyncio.run(go())] == ["early", "late"]


def test_script_without_a_clock_plays_at_once():
    room = FakeRoom(script=[(None, "kazum", "#dispatch", "a"), ("18:00", "k", "#dispatch", "b")])
    assert [m.text for m in asyncio.run(_collect(room))] == ["a", "b"]


# ---------------------------------------------------------------- launch --pilot (L41)
LAUNCH = ("launch", "--drone", "D-05", "--mission", "incident", "--incident", "INC-1")
LAUNCH += ("--to", "0.75,2.2", "--why", "upstream queue")


def saturate(client):
    client.post("/control/skip", json={"to": "17:15"})
    sim_app.S.engine.pilots["kazum"].drones.update({"D-01", "D-03", "D-04", "D-06"})


@pytest.mark.parametrize("named", [(), ("--pilot", "kazum")], ids=["omitted", "named"])
def test_saturated_kazum_is_refused_with_the_retry(monkeypatch, client, named):
    saturate(client)
    r = run_cli(monkeypatch, client, *LAUNCH, *named, token="tok-dispatch")
    assert r.exit_code == 2
    body = json.loads(r.stdout)
    assert body["code"] == "PILOT_SATURATED" and "--pilot DP-2" in body["hint"]
    assert client.get("/fleet/D-05").json()["drone"]["state"] == "docked"  # not reassigned


def test_retry_under_the_named_duty_pilot_flies(monkeypatch, client):
    saturate(client)
    r = run_cli(monkeypatch, client, *LAUNCH, "--pilot", "DP-2", token="tok-dispatch")
    assert r.exit_code == 0
    assert client.get("/fleet/D-05").json()["drone"]["pilot"] == "DP-2"


def test_omitted_pilot_defaults_to_the_remote_pilot(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:15"})
    r = run_cli(monkeypatch, client, *LAUNCH, token="tok-dispatch")
    assert r.exit_code == 0
    assert client.get("/fleet/D-05").json()["drone"]["pilot"] == "kazum"


def test_unknown_pilot_is_not_found(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:15"})
    r = run_cli(monkeypatch, client, *LAUNCH, "--pilot", "DP-9", token="tok-dispatch")
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "NOT_FOUND"


def test_auto_launch_keeps_its_own_fallback():
    e = Engine.load("FRIDAY-RUSH", mode="headless", golden=False)
    e.standing_policy = True
    e.run_to_actions("17:12")
    e.pilots["kazum"].drones.update({"D-01", "D-03", "D-04", "D-06"})
    e.run_until("17:13")
    launched = [x for x in e.log.items if x.type == "launch"]
    assert launched and launched[0].payload["pilot"].startswith("DP-")


# ---------------------------------------------------------------- launch --for-min, evidence
def test_for_min_that_breaks_the_reserve_is_refused(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:15"})
    r = run_cli(monkeypatch, client, *LAUNCH, "--for-min", "300", token="tok-dispatch")
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "BATTERY_RESERVE"
    assert client.get("/fleet/D-05").json()["drone"]["state"] == "docked"


def test_for_min_within_the_reserve_flies(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:15"})
    r = run_cli(monkeypatch, client, *LAUNCH, "--for-min", "5", token="tok-dispatch")
    assert r.exit_code == 0


@pytest.mark.parametrize(
    "evidence,code",
    [
        ("D-09 feed: 2 minor, walking", 0),
        ("D-09 feed: 2 cars, minor injuries", 2),
    ],
)
def test_release_evidence_must_quote_a_full_fact(monkeypatch, client, evidence, code):
    client.post("/control/skip", json={"to": "17:13"})
    tok = {"Authorization": "Bearer tok-comms"}
    args = {"unit": "patrol_car_2", "incident": "INC-1"}
    b = {"action": "task_unit", "args": args}
    assert client.post("/actions", json=b, headers=tok).status_code == 200
    d = {"drone": "D-09", "mission": "incident", "incident": "INC-1", "to": "ring_road_j9"}
    assert act(client, "tok-dispatch", "launch", d).status_code == 200
    client.post("/control/skip", json={"to": "17:16"})
    argv = ("unit", "release", "--unit", "patrol_car_2", "--incident", "INC-1")
    r = run_cli(monkeypatch, client, *argv, "--evidence", evidence, token="tok-comms")
    assert r.exit_code == code, r.stdout


# ---------------------------------------------------------------- review fixes
@pytest.mark.parametrize("argv", [(), ("--nope",), ("propose",)], ids=["bare", "bad-flag", "group"])
def test_group_parse_errors_exit_1(monkeypatch, client, argv):
    assert run_cli(monkeypatch, client, *argv).exit_code == 1


def test_cli_help_still_exits_0(monkeypatch, client):
    assert run_cli(monkeypatch, client, "--help").exit_code == 0


def test_propose_standing_policy_holds_by_type(monkeypatch, client):
    argv = ("propose", "standing-policy", "--scope", "auto-launch")
    r = run_cli(monkeypatch, client, *argv, token="tok-dispatch")
    assert r.exit_code == 0
    aid = json.loads(r.stdout)["result"]["id"]
    assert f"approval:{aid}" in client.get("/status").json()["clock_held"]


def test_a_client_cannot_opt_out_of_the_hold(client):
    body = {"type": "standing_policy", "args": {}, "hold_clock": False}
    tok = {"Authorization": "Bearer tok-dispatch"}
    assert client.post("/approvals", json=body, headers=tok).status_code == 200
    assert client.get("/status").json()["clock_held"]


def test_triggered_cards_hold_too(client):
    client.post("/control/skip", json={"to": "17:15"})
    assert client.post("/trigger", json={"kind": "vip_zone"}).status_code == 200
    assert client.get("/status").json()["clock_held"]


def test_track_assign_without_authorisation_is_refused(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:45"})
    argv = ("track", "assign", "--drone", "D-02", "--incident", "INC-2")
    r = run_cli(monkeypatch, client, *argv, token="tok-dispatch")
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "AUTH_REQUIRED"


def test_divert_defaults_to_track_and_needs_authorisation(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:45"})
    argv = ("divert", "--drone", "D-02", "--incident", "INC-2")
    r = run_cli(monkeypatch, client, *argv, token="tok-dispatch")
    assert r.exit_code == 2 and json.loads(r.stdout)["code"] == "AUTH_REQUIRED"


def test_launch_still_refuses_mission_track(monkeypatch, client):
    client.post("/control/skip", json={"to": "17:45"})
    argv = ("launch", "--drone", "D-05", "--mission", "track", "--incident", "INC-2", "--to", "1,1")
    assert run_cli(monkeypatch, client, *argv, token="tok-dispatch").exit_code == 1


def test_log_refusal_pilot_flags_write_the_golden_line(monkeypatch, client):
    sim_app.S.engine.pilots["kazum"].drones.add("D-04")
    argv = ("log-refusal", "--about", "x", "--code", "PILOT_SATURATED", "--drone", "D-03")
    argv += ("--pilot", "kazum", "--fallback-pilot", "DP-2")
    assert run_cli(monkeypatch, client, *argv, token="tok-dispatch").exit_code == 0
    last = client.get("/events").json()["events"][-1]
    assert last["text"] == "❌ REJECTED D-03 under kazum: PILOT_SATURATED (['D-04']) → DP-2"


def test_route_check_skips_inactive_zones(client):
    zones = client.get("/airspace").json()["zones"]
    vip = next(z for z in zones if z["kind"] == "vip")
    x = (vip["rect"]["x"][0] + vip["rect"]["x"][1]) / 2
    y = (vip["rect"]["y"][0] + vip["rect"]["y"][1]) / 2
    b = client.get(f"/route/check?drone=D-13&to={x},{y}").json()
    assert vip["id"] not in [z["id"] for z in b["zones"]]


def test_review_routes():
    assert rooms.approval_room("zone") == "#dispatch"
    e = {"type": "room_message", "payload": {"room": "field-units", "text": "FO: need eyes"}}
    assert rooms.route(e).room == "#field-units" and rooms.route(e).agent == "comms"
