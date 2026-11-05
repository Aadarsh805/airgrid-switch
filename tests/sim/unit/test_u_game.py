"""Game mode (docs/game-mode.md, L54): flow, choices, timeouts, flight, catch, score, /game API."""

import json

import pytest
from fastapi.testclient import TestClient

from sim import game as gm
from sim.actions import decide
from sim.app import S, app
from sim.auth import Identity
from sim.engine import Engine
from sim.geo import fmt, in_rect
from sim.scenario import load_scenario
from sim.world import load_world

W = load_world()
RED = next(z for z in W.raw["zones"] if z["kind"] == "red")["rect"]
SCORE = W.policies["game_score"]
KAZUM = Identity.human("kazum", ["watch_commander", "remote_pilot", "field_officer"])
KEY = "test-game-key"
H = {"X-Game-Key": KEY}


class FakeTime:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def new_game():
    eng = Engine.load("FRIDAY-RUSH", mode="game")
    clock = FakeTime()
    g = gm.Game(eng, clock)
    g.start()
    return g, clock


def play(g, until="20:00"):
    while not g.done and g.prompt is None and g.eng._t <= _m(until):
        g.step()


def _m(s):
    from sim.geo import tmin

    return tmin(s)


def to_prompt(g, beat):
    play(g)
    assert g.prompt and g.prompt["beat"] == beat, g.prompt
    return g


def through(g, *choices):
    for c in choices:
        play(g)
        g.choose(c)


def full_run(flight=None):
    """Plays a whole shift. `flight`: direction chooser for the finale."""
    g, _ = new_game()
    through(g, "release", "approve", "try", "try")
    play(g)
    if flight is None:
        g.choose("leave")
    else:
        g.choose("take_control")
        while g.flight is not None:
            g.fly(*flight(g))
            g.step()
    play(g)
    return g


def chase(g):
    d = g.eng.drones[g.flight.drone]
    s = g.eng.suspect.pos(g.eng._t)
    dx, dy = s[0] - d.pos[0], s[1] - d.pos[1]
    n = (dx * dx + dy * dy) ** 0.5 or 1
    return dx / n, dy / n


# ---- flow and each choice
def test_start_jumps_before_first_alert_policy_approved():
    g, _ = new_game()
    assert g.step_name == "crash" and fmt(g.eng._t) == "17:11" and g.eng.standing_policy
    assert not g.eng.incidents


def test_crash_auto_launch_then_release_prompt():
    g, _ = new_game()
    to_prompt(g, "crash")
    assert fmt(g.eng.now) == "17:21"
    assert g.eng.kpi.summary(g.eng)["eyes_on_s"]["INC-1"] == 114
    assert "2 minor, walking" in g.prompt["text"]


def test_release_goes_through_validator_with_actor():
    g, _ = new_game()
    to_prompt(g, "crash")
    g.choose("release")
    assert g.eng.kpi.released_early == 1
    row = next(r for r in g.eng.decisions if r["action"] == "release_unit")
    assert row["actor"] == "kazum (dashboard)" and row["code"] is None


def test_keep_releases_nothing():
    g, _ = new_game()
    to_prompt(g, "crash")
    g.choose("keep")
    assert g.eng.kpi.released_early == 0 and g.step_name == "robbery"


def test_robbery_approve_real_approval_and_divert():
    g, _ = new_game()
    through(g, "release")
    to_prompt(g, "robbery")
    aid = g.approval_id
    assert g.eng.approvals.items[aid].status == "pending"
    g.choose("approve")
    a = g.eng.approvals.items[aid]
    assert a.status == "approved" and a.decided_by == "kazum (dashboard)"
    assert g.eng.tracker_id == "D-13" and g.eng.drones["D-13"].mission == "track"


def test_robbery_reject_no_track_and_no_flight():
    g, _ = new_game()
    through(g, "release", "reject", "try", "try")
    assert g.eng.tracker_id is None and g.eng.drones["D-13"].mission != "track"
    to_prompt(g, "finale_no_track")
    assert [o["id"] for o in g.view()["options"]] == ["leave"]
    g.choose("leave")
    play(g)
    assert g.done and g.eng.violations == []
    assert g.final["breakdown"]["approval"]["points"] == 0


def test_typed_approval_in_the_room_is_followed():
    g, _ = new_game()
    through(g, "release")
    to_prompt(g, "robbery")
    assert decide(g.eng, KAZUM, g.approval_id, True).ok
    g.poll()
    assert g.prompt is None and g.track_approved and g.eng.tracker_id == "D-13"


@pytest.mark.parametrize(
    "beat,code", [("trap_face", "PRIVACY_POLICY"), ("trap_route", "NO_FLY_ZONE")]
)
def test_trap_try_is_refused_by_the_real_validator(beat, code):
    g, _ = new_game()
    through(g, "release", "approve")
    if beat == "trap_route":
        through(g, "lawful")
    to_prompt(g, beat)
    g.choose("try")
    assert g.result["ok"] is False and g.result["code"] == code and g.result["hint"]
    assert g.eng.refusals[-1][1] == code
    rows = [r for r in g.eng.decisions if r["code"] == code]
    assert rows and rows[-1]["action"] == "game:" + beat
    if beat == "trap_face":
        assert g.eng.privacy.rows()[-1]["kind"] == "face_id_refused"


def test_trap_lawful_logs_no_refusal_code_breach():
    g, _ = new_game()
    through(g, "release", "approve", "lawful", "lawful")
    assert g.eng.refusals == [] or all(c != "NO_FLY_ZONE" for _t, c, _r in g.eng.refusals)
    assert [r for r in g.eng.decisions if r["action"] == "log_refusal"]


def test_invalid_choice_and_no_prompt():
    g, _ = new_game()
    to_prompt(g, "crash")
    with pytest.raises(ValueError):
        g.choose("launch everything")
    g.choose("keep")
    with pytest.raises(gm.GameError) as e:
        g.choose("keep")
    assert e.value.rejection.code == "MISSION_EXPIRED"


# ---- timeout
@pytest.mark.parametrize(
    "steps,beat,effect",
    [
        ([], "crash", lambda g: g.eng.kpi.released_early == 0),
        (["keep"], "robbery", lambda g: not g.track_approved),
        (["keep", "approve"], "trap_face", lambda g: g.choices["trap_face"] == "timed out"),
        (
            ["keep", "approve", "lawful", "lawful"],
            "finale",
            lambda g: g.flight is None,
        ),
    ],
)
def test_timeout_picks_safe_default(steps, beat, effect):
    g, clock = new_game()
    through(g, *steps)
    to_prompt(g, beat)
    clock.t += W.policies["game_clock"]["prompt_timeout_s"] - 1
    g.poll()
    assert g.prompt is not None
    clock.t += 1
    g.poll()
    assert g.prompt is None and effect(g)
    assert beat in g.timed_out and g.result["timed_out"] is True
    assert any("timed out" in r["rationale"] for r in g.eng.decisions)


def test_nobody_at_the_screen_still_finishes():
    g, clock = new_game()
    for _ in range(20000):
        if g.done:
            break
        if g.prompt:
            clock.t += 30
        g.step()
    assert g.done and g.final["rank"] == "Rookie" and g.eng.violations == []


# ---- flight
def _at_finale():
    g, clock = new_game()
    through(g, "release", "approve", "lawful", "lawful")
    to_prompt(g, "finale")
    g.choose("take_control")
    return g, clock


def test_only_the_controlled_drone_flies():
    g, _ = new_game()
    with pytest.raises(gm.GameError) as e:
        g.fly(1, 0)
    assert e.value.rejection.code == "AUTH_REQUIRED"
    g, _ = _at_finale()
    d = g.eng.drones["D-12"]
    assert g.flight.drone == "D-12" and d.manual and d.pilot == "kazum"
    row = next(r for r in g.eng.decisions if r["action"] == "take_control")
    assert row["actor"] == "kazum (dashboard)"


def test_fly_moves_at_cruise_and_drains_battery():
    g, _ = _at_finale()
    d = g.eng.drones["D-12"]
    x, bat = d.pos[0], d.bat
    g.fly(-1, 0)
    g.step()
    assert d.pos[0] == pytest.approx(x - W.model["cruise_km_per_min"] * 0.1)
    assert d.bat == pytest.approx(bat - W.model["drain_pct_per_min"]["cruise"] * 0.1)


def test_no_input_hovers_at_hover_drain():
    g, _ = _at_finale()
    d = g.eng.drones["D-12"]
    p, bat = list(d.pos), d.bat
    g.fly(0, 0)
    g.step()
    assert d.pos == p
    assert d.bat == pytest.approx(bat - W.model["drain_pct_per_min"]["hover"] * 0.1)


def test_fly_idle_holds_the_drone():
    g, clock = _at_finale()
    d = g.eng.drones["D-12"]
    g.fly(-1, 0)
    g.step()
    clock.t += W.policies["game_clock"]["fly_idle_s"]
    p = list(d.pos)
    g.step()
    assert d.pos == p


def test_fly_blocked_at_red_airfield():
    g, _ = _at_finale()
    d = g.eng.drones["D-12"]
    d.pos = [RED["x"][0] - 0.2, 3.0]  # just west of the airfield
    g.fly(1, 0)
    for _ in range(10):
        g.step()
        assert not in_rect(d.pos, RED), d.pos
    assert d.pos[0] == pytest.approx(RED["x"][0], abs=0.01)
    ev = [e for e in g.eng.events if e["type"] == "game_blocked"]
    assert len(ev) == 1 and ev[0]["payload"]["code"] == "NO_FLY_ZONE"
    assert g.flight.blocked and g.eng.kpi.red_zone_entries == 0 and g.eng.violations == []


def test_fly_blocked_at_active_vip():
    g, _ = _at_finale()
    next(z for z in g.eng.airspace.zones if z["kind"] == "vip")["active"] = True
    d = g.eng.drones["D-12"]
    d.pos = [1.4, 5.0]
    g.fly(-1, 0)
    for _ in range(5):
        g.step()
    assert d.pos[0] >= 1.2 and g.flight.blocked


def test_flyer_is_the_drone_at_the_red_line_watcher_stays_on_the_suspect():
    g, _ = _at_finale()
    assert g.flight.drone == "D-12" and g.eng.drones["D-12"].mission == "hold"
    assert g.eng.tracker_id == "D-04" and g.eng.drones["D-04"].mission == "track"
    assert "D-04" in next(
        e["text"]
        for e in g.eng.events
        if e["type"] == "game_prompt" and e["payload"]["beat"] == "finale"
    )


def test_flight_view_serves_current_suspect_only_while_in_view():
    g, _ = _at_finale()
    f = g.view()["flight"]
    now = g.eng.suspect.pos(g.eng.now)
    assert f["suspect"] == pytest.approx(now, abs=0.001)
    assert f["distance_km"] > g.catch_km * 5
    assert (f["steer"][0] ** 2 + f["steer"][1] ** 2) ** 0.5 == pytest.approx(1, abs=0.01)
    g.eng.drones["D-04"].pos = [0.5, 0.5]  # nobody has it in view any more
    f = g.view()["flight"]
    assert f["suspect"] is None and f["distance_km"] is None and f["steer"] is None


def chase_seconds():
    g, _ = _at_finale()
    ticks = 0
    while g.flight is not None:
        f = g.view()["flight"]
        g.fly(*f["steer"]) if f["steer"] else g.fly(0, 1)
        g.step()
        ticks += 1
    assert g.caught_at is not None
    return ticks * 0.1 / W.policies["game_fly_sim_min_per_s"]


def test_clean_chase_lasts_15_to_40_real_seconds():
    assert 15 <= chase_seconds() <= 40


def test_clock_runs_at_fly_rate_during_the_chase_only(c):
    from sim.clock import DEFAULT_RATE

    start(c)
    S.sync_game()
    assert S.clock.rate == DEFAULT_RATE
    for choice in ("release", "approve", "lawful", "lawful", "take_control"):
        c.post("/control/skip", json={"to": "18:30"})
        c.post("/game/choose", json={"choice": choice}, headers=H)
    S.sync_game()
    assert S.game.flight is not None
    assert S.clock.rate == W.policies["game_fly_sim_min_per_s"]


# ---- catch
def test_catch_by_flying():
    g = full_run(chase)
    assert g.done and g.caught_at is not None and g.eng.violations == []
    assert g.final["caught"] is True and g.final["breakdown"]["catch"]["points"] == 30
    assert g.eng.incidents["INC-2"]["status"] == "closed"
    assert g.eng.incidents["INC-2"]["closed_t"] == g.caught_at
    assert any(e["type"] == "game_catch" for e in g.eng.events)
    assert fmt(g.eng.now) == "20:00"


def test_miss_ground_units_detain_lower_score():
    away = lambda g: (-1, 0)  # noqa: E731
    g = full_run(away)
    caught = full_run(chase)
    assert g.final["caught"] is False and fmt(g.eng.incidents["INC-2"]["closed_t"]) == "18:07"
    assert g.final["score"] < caught.final["score"]


# ---- score
def test_score_weights_total_100_rank_and_old_way():
    assert sum(SCORE["weights"].values()) == 100
    g = full_run(chase)
    f = g.final
    assert f["max"] == 100 and f["score"] == sum(p["points"] for p in f["breakdown"].values())
    assert set(f["breakdown"]) == set(SCORE["weights"])
    assert f["rank"] == "Commander"
    assert len(f["old_way"]) == 5
    blob = json.dumps(f["old_way"], ensure_ascii=False)
    assert "17.0 min" in blob and "LOST" in blob


def test_score_so_far_grows():
    g, _ = new_game()
    assert g.score_so_far() == 0
    through(g, "release")
    a = g.score_so_far()
    assert a == SCORE["weights"]["response"]
    through(g, "approve")
    assert g.score_so_far() == a + SCORE["weights"]["approval"]


# ---- determinism
def test_same_inputs_same_hash_and_score():
    a, b = full_run(chase), full_run(chase)
    assert a.eng.log.hash() == b.eng.log.hash()
    assert a.final == b.final


def test_different_inputs_differ():
    assert full_run(chase).eng.log.hash() != full_run().eng.log.hash()


# ---- API
@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "g.db"))
    monkeypatch.setenv("AIRGRID_GAME_KEY", KEY)
    with TestClient(app) as client:
        S.game = None
        yield client
        S.game = None


def start(c):
    r = c.post("/game/start", json={"paused": True}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


ROUTES = [
    ("get", "/game", None),
    ("get", "/game/score", None),
    ("post", "/game/start", {}),
    ("post", "/game/choose", {"choice": "keep"}),
    ("post", "/game/fly", {"dx": 1, "dy": 0}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_no_key_403(c, method, path, body):
    r = c.request(method, path, json=body)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_server_without_key_refuses_all(c, monkeypatch, method, path, body):
    monkeypatch.delenv("AIRGRID_GAME_KEY")
    r = c.request(method, path, json=body, headers={"X-Game-Key": ""})
    assert r.status_code == 403
    assert c.request(method, path, json=body, headers=H).status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_agent_token_cannot_use_game(c, method, path, body):
    r = c.request(method, path, json=body, headers={"Authorization": "Bearer dispatch-token"})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"


def test_api_shapes_and_whole_game(c):
    s = c.get("/game", headers=H).json()
    assert s["step"] == "idle"
    b = start(c)
    for k in ("step", "prompt", "options", "clock", "score_so_far", "done"):
        assert k in b
    assert b["step"] == "crash" and b["clock"] == fmt(_m("17:11") - 0.1)
    r = c.post("/control/skip", json={"to": "17:30"})
    assert r.status_code == 200
    b = c.get("/game", headers=H).json()
    assert b["clock"] == "17:21" and {o["id"] for o in b["options"]} == {"release", "keep"}
    assert c.post("/game/choose", json={"choice": "nope"}, headers=H).status_code == 422
    assert c.post("/game/fly", json={"dx": 1, "dy": 0}, headers=H).status_code == 409
    b = c.post("/game/choose", json={"choice": "release"}, headers=H).json()
    assert b["result"]["ok"] is True and b["step"] == "robbery"
    assert c.get("/game/score", headers=H).json()["done"] is False
    for want, choice in (
        ("robbery", "approve"),
        ("trap_face", "try"),
        ("trap_route", "try"),
        ("finale", "take_control"),
    ):
        c.post("/control/skip", json={"to": "18:30"})
        b = c.get("/game", headers=H).json()
        assert b["beat"] == want, b
        c.post("/game/choose", json={"choice": choice}, headers=H)
    assert c.post("/game/fly", json={"dx": 2, "dy": 0}, headers=H).status_code == 422
    f = c.post("/game/fly", json={"dx": 0, "dy": 1}, headers=H).json()["flight"]
    assert f["drone"] == "D-12" and "battery" in f and "distance_km" in f
    assert f["suspect"] and f["steer"]
    c.post("/control/skip", json={"to": "18:30"})
    b = c.get("/game", headers=H).json()
    assert b["done"] is True and b["step"] == "score"
    s = c.get("/game/score", headers=H).json()
    assert s["done"] is True and s["rank"] in SCORE["ranks"] and len(s["old_way"]) == 5


def test_get_game_serves_no_future(c):
    scen = load_scenario("FRIDAY-RUSH")
    start(c)
    c.post("/control/skip", json={"to": "17:30"})
    text = c.get("/game", headers=H).text
    text += c.get("/game/score", headers=H).text
    banned = ["suspect_path", "golden_actions", "18:0", "18:2", "17:40", "17:52", "LOST", "17.0"]
    banned += [a["request"] for a in scen["golden_actions"] if a.get("request")]
    banned += [str(e["fact"].get("suspect_vehicle")) for e in scen["feed"] if e["t"] > "17:30"]
    for word in banned:
        assert word not in text, word
    assert "beats" not in text and "skip_target" not in text


def test_engine_stays_headless_elsewhere():
    """Default modes are untouched: game mode exists only behind /game/start."""
    from sim.engine import MODES

    assert "game" not in MODES
