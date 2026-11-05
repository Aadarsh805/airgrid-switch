"""Phase 04 (sim-engineer unit tests): ground units, incidents + feed reveal, triggers,
baseline arithmetic, KPI / scorecard. Interface: docs/phase04-interface.md."""

import json
import random

import pytest
from fastapi.testclient import TestClient

from sim import baseline, ground, incidents, triggers
from sim.actions import decide, execute, propose
from sim.auth import Identity
from sim.engine import Engine
from sim.geo import fmt, tmin
from sim.world import load_world

DISPATCH = Identity.agent("dispatch")
COMMS = Identity.agent("comms")
KAZUM = Identity.human("kazum", ["watch_commander", "remote_pilot", "field_officer"])
W = load_world()
GU = W.raw["ground_units"]


def demo(name="FRIDAY-RUSH", approve_policy=True):
    eng = Engine.load(name, mode="demo")
    if approve_policy:
        a = propose(eng, DISPATCH, "standing_policy", {}, "brief")
        decide(eng, KAZUM, a.result["id"], True)
    return eng


# ---------------------------------------------------------------- ground units
def test_fifteen_units_from_world():
    g = ground.GroundUnits(W.raw)
    kinds = [u.kind for u in g.units.values()]
    assert kinds.count("patrol_car") == 10
    assert kinds.count("ambulance") == 3
    assert kinds.count("fire_engine") == 2
    assert all(u.status == "available" for u in g.units.values())


def test_manhattan_eta_and_states():
    g = ground.GroundUnits(W.raw)
    t0 = tmin("17:13")
    u = g.task(t0, "patrol_car_2", "INC-1", [1.5, 1.0], "accident")
    # 1 min dispatch + 2.75 km Manhattan / 0.5 km/min
    assert u.arrive_t == pytest.approx(t0 + 1 + 2.75 / 0.5)
    assert u.status == "en_route"
    g.tick(u.arrive_t - 0.1)
    assert u.status == "en_route"
    g.tick(u.arrive_t)
    assert u.status == "on_scene" and u.pos == [1.5, 1.0]
    g.tick(u.arrive_t + GU["on_scene_min"]["accident"])
    assert u.status == "returning"
    g.tick(u.back_t)
    assert u.status == "available" and u.pos == GU["start"]["patrol_car_2"]


def test_manhattan_path_moves_x_then_y():
    g = ground.GroundUnits(W.raw)
    u = g.task(0.0, "patrol_car_2", "X", [1.5, 1.0], "accident")  # from (2.25, 3.0)
    g.tick(1.0 + 0.5)  # 0.25 km driven, all on x
    assert u.pos == pytest.approx([2.0, 3.0])
    g.tick(1.0 + 3.5)  # 1.75 km: 0.75 x then 1.0 y
    assert u.pos == pytest.approx([1.5, 2.0])


def test_release_saves_planned_minutes():
    g = ground.GroundUnits(W.raw)
    u = g.task(0.0, "patrol_car_2", "X", [1.5, 1.0], "accident")
    g.tick(10.0)
    saved = g.release(10.0, "patrol_car_2")
    assert saved == pytest.approx(u.arrive_t + 23 - 10.0)
    assert u.status == "returning" and u.incident is None


def test_nearest_available():
    g = ground.GroundUnits(W.raw)
    assert g.nearest_available("patrol_car", [1.5, 1.0]).id == "patrol_car_2"
    assert g.nearest_available("ambulance", [1.5, 1.0]).id == "ambulance_1"
    g.task(0.0, "patrol_car_2", "X", [1.5, 1.0], "accident")
    assert g.nearest_available("patrol_car", [1.5, 1.0]).id != "patrol_car_2"


def test_task_unit_action_and_units_view():
    eng = demo()
    eng.run_until("17:13")
    r = execute(eng, COMMS, "task_unit", {"unit": "patrol_car_2", "incident": "INC-1"})
    assert r.ok, r.body()
    v = {u["id"]: u for u in eng.ground.view(eng.now)}
    assert v["patrol_car_2"]["status"] == "en_route"
    assert v["patrol_car_2"]["incident"] == "INC-1"
    assert v["patrol_car_2"]["eta_min"] == pytest.approx(6.5, abs=0.11)
    assert set(v["patrol_car_2"]) >= {"id", "kind", "status", "pos", "incident", "eta_min"}
    assert any(e["type"] == "unit_tasked" and not e["timeline"] for e in eng.events)


def test_task_unit_rejections():
    eng = demo()
    eng.run_until("17:13")
    r = execute(eng, COMMS, "task_unit", {"unit": "patrol_car_99", "incident": "INC-1"})
    assert r.rejection.code == "NOT_FOUND"
    r = execute(eng, COMMS, "task_unit", {"unit": "patrol_car_2", "incident": "INC-9"})
    assert r.rejection.code == "NOT_FOUND"
    r = execute(eng, DISPATCH, "task_unit", {"unit": "patrol_car_2", "incident": "INC-1"})
    assert r.rejection.code == "FORBIDDEN_ROLE"


def test_task_unit_closed_incident_mission_expired():
    eng = demo()
    eng.run_until("17:13")
    eng.incidents["INC-1"]["status"] = "closed"
    r = execute(eng, COMMS, "task_unit", {"unit": "patrol_car_2", "incident": "INC-1"})
    assert r.rejection.code == "MISSION_EXPIRED"


def _tasked_pc2(until="17:21"):
    eng = demo()
    eng.run_until("17:13")
    assert execute(eng, COMMS, "task_unit", {"unit": "patrol_car_2", "incident": "INC-1"}).ok
    eng.run_until(until)
    return eng


def test_release_needs_revealed_fact_evidence():
    eng = _tasked_pc2()
    args = {"unit": "patrol_car_2", "incident": "INC-1", "evidence": "looked fine to me"}
    r = execute(eng, COMMS, "release_unit", args)
    assert r.rejection.code == "AUTH_REQUIRED"
    assert "feed" in r.rejection.hint
    args["evidence"] = "D-09 feed: 2 minor, walking"
    r = execute(eng, COMMS, "release_unit", args)
    assert r.ok, r.body()
    assert r.result["saves_min"] > 0
    assert eng.kpi.released_early == 1


def test_release_cannot_quote_unrevealed_fact():
    eng = _tasked_pc2("17:16")  # the 17:33 'cleared' fact is not revealed yet
    r = execute(
        eng,
        COMMS,
        "release_unit",
        {"unit": "patrol_car_2", "incident": "INC-1", "evidence": "tow cleared"},
    )
    assert r.rejection.code == "AUTH_REQUIRED"


def test_release_unit_not_on_incident():
    eng = _tasked_pc2()
    r = execute(
        eng, COMMS, "release_unit", {"unit": "patrol_car_3", "evidence": "2 minor, walking"}
    )
    assert r.rejection.code == "NOT_FOUND"


def test_release_after_released_rejected():
    eng = _tasked_pc2()
    ev = {"unit": "patrol_car_2", "evidence": "2 minor, walking"}
    assert execute(eng, COMMS, "release_unit", ev).ok
    assert execute(eng, COMMS, "release_unit", ev).rejection.code == "NOT_FOUND"


def test_close_incident_stands_units_down():
    eng = demo()
    eng.run_until("17:41")
    execute(eng, COMMS, "task_unit", {"unit": "patrol_car_5", "incident": "INC-2"})
    eng.run_until("17:50")
    assert execute(eng, DISPATCH, "close_incident", {"incident": "INC-2", "note": "x"}).ok
    u = eng.ground.units["patrol_car_5"]
    assert u.status == "returning" and u.incident is None
    assert eng.kpi.released_early == 0


# ---------------------------------------------------------------- incidents and feed
def test_feed_hidden_until_drone_on_station():
    eng = demo()
    eng.run_until("17:13.8")  # D-09 on station 17:13:54
    assert incidents.revealed(eng, "INC-1") == []
    eng.run_until("17:14")
    facts = incidents.revealed(eng, "INC-1")
    assert len(facts) == 1 and facts[0]["fact"]["vehicles"] == 3
    assert facts[0]["t"] == "17:14"


def test_feed_never_before_its_time():
    eng = demo()
    eng.run_until("17:30")
    assert [f["t"] for f in incidents.revealed(eng, "INC-1")] == ["17:14", "17:18"]


def test_feed_waits_for_station_even_if_due():
    """A fact timed before any drone is on station is revealed only once one is."""
    eng = demo(approve_policy=False)  # no auto-launch → nobody on station
    eng.run_until("17:20")
    assert incidents.revealed(eng, "INC-1") == []


def test_track_drone_reveals_robbery_feed():
    eng = Engine.load("FRIDAY-RUSH", mode="headless", golden=True)
    eng.run_until("17:43")
    facts = incidents.revealed(eng, "INC-2")
    assert facts and "red scooter" in facts[0]["fact"]["suspect_vehicle"]


def test_incident_view_shape():
    eng = Engine.load("FRIDAY-RUSH", mode="headless", golden=True)
    eng.run_until("17:22")
    v = eng.incident_view("INC-1")
    for k in (
        "id",
        "kind",
        "at",
        "priority",
        "status",
        "geofence_km",
        "opened",
        "closed",
        "drones",
        "units",
        "authorisations",
        "recording_drones",
        "facts",
    ):
        assert k in v, k
    assert v["priority"] in (1, 2, 3)
    assert "ambulance_1" in v["units"]
    assert "patrol_car_2" not in v["units"]  # released 17:21
    assert all(tmin(f["t"]) <= eng.now for f in v["facts"])


def test_evidence_match():
    facts = [{"fact": {"injuries": "2 minor, walking", "vehicles": 3}}]
    assert not incidents.evidence_matches("feed shows minor injuries", facts)
    assert incidents.evidence_matches("feed says 2 minor, walking", facts)
    assert incidents.evidence_matches("2 minor, walking", facts)
    assert not incidents.evidence_matches("all good", facts)
    assert not incidents.evidence_matches("", facts)


# ---------------------------------------------------------------- golden units
def test_golden_units_run_as_comms_actions():
    eng = Engine.load("FRIDAY-RUSH", mode="headless", golden=True)
    eng.run()
    tasked = [d for d in eng.decisions if d["action"] == "task_unit"]
    assert [d["actor"] for d in tasked] == ["comms"] * 5
    assert all(d["code"] is None for d in tasked)
    rel = [d for d in eng.decisions if d["action"] == "release_unit"]
    assert len(rel) == 1 and rel[0]["code"] is None
    assert not [
        e
        for e in eng.events
        if e["type"].startswith("unit_") and e["timeline"] and e["type"] != "unit_released"
    ]


# ---------------------------------------------------------------- triggers
def _trig(eng, kind, **kw):
    return triggers.fire(eng, kind, **kw)


def test_unknown_trigger():
    eng = demo()
    with pytest.raises(triggers.TriggerNotFound):
        _trig(eng, "meteor")


def test_fire_prefers_thermal():
    eng = demo()
    eng.run_until("17:05")
    r = _trig(eng, "fire")
    eng.run_until("17:06")
    ds = [d for d in eng.drones.values() if d.incident == r["incident"]]
    assert ds and ds[0].thermal and ds[0].mission == "incident"


def test_missing_child_search_pair():
    eng = demo()
    eng.run_until("17:05")
    r = _trig(eng, "missing_child")
    assert eng.incidents[r["incident"]]["kind"] == "missing_person"
    eng.run_until("17:06")
    ds = [d for d in eng.drones.values() if d.incident == r["incident"]]
    assert len(ds) == 2 and {d.mission for d in ds} == {"search"}
    assert ds[0].target != ds[1].target


def test_underpass_flood_auto_launch():
    eng = demo()
    eng.run_until("17:05")
    r = _trig(eng, "underpass_flood")
    assert eng.incidents[r["incident"]]["kind"] == "flood"
    eng.run_until("17:06")
    assert [d for d in eng.drones.values() if d.incident == r["incident"]]


def test_drone_fault_safety_rtb_and_card():
    eng = demo()
    eng.run_until("17:05")
    tight = min(
        (d for d in eng.drones.values() if d.state == "airborne" and d.mission != "returning"),
        key=lambda d: d.bat,
    )
    r = _trig(eng, "drone_fault")
    assert r["drone"] == tight.id
    assert tight.mission == "returning"
    a = eng.approvals.items[r["approval_id"]]
    assert a.type == "out_of_service" and a.status == "pending"
    assert tight.id not in [d.id for d in eng.ready_candidates([3, 3], 0)]
    decide(eng, KAZUM, a.id, True)
    assert tight.out_of_service


def test_unauthorised_drone_separation_hold():
    eng = demo()
    eng.run_until("17:05")
    r = _trig(eng, "unauthorised_drone")
    assert r["sector"] == "NW"
    hold = W.policies["intrusion_hold_min"]
    assert r["until"] == fmt(eng.now + hold)
    out = execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-01", "mission": "perimeter", "incident": "INC-X", "to": [1.0, 4.0]},
    )
    assert out.rejection is not None  # INC-X unknown → open a real one
    iid = eng.inject_alert({"kind": "robbery", "at": [1.0, 4.0]})
    out = execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-01", "mission": "perimeter", "incident": iid, "to": [1.0, 4.0]},
    )
    assert out.rejection.code == "SEPARATION"
    eng.run_until(eng.now + hold + 0.2)
    out = execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-01", "mission": "perimeter", "incident": iid, "to": [1.0, 4.0]},
    )
    assert out.ok, out.body()


def test_unauthorised_drone_holds_airborne_nw():
    eng = demo()
    eng.run_until("17:05")
    iid = eng.inject_alert({"kind": "robbery", "at": [1.0, 4.0]})
    assert execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-05", "mission": "perimeter", "incident": iid, "to": [1.0, 4.5]},
    ).ok
    eng.run_until(eng.now + 1)
    d = eng.drones["D-05"]
    _trig(eng, "unauthorised_drone")
    p = list(d.pos)
    eng.run_until(eng.now + 2)
    assert d.pos == p
    eng.run_until(eng.now + 4)
    assert d.pos != p


def test_vip_zone_card_then_p1_unusable():
    eng = demo()
    eng.run_until("17:05")
    r = _trig(eng, "vip_zone")
    a = eng.approvals.items[r["approval_id"]]
    assert a.type == "zone" and a.status == "pending"
    assert a.args["zone"] == "VIP-TEMP" and tmin(a.args["until"]) == pytest.approx(eng.now + 60)
    iid = eng.inject_alert({"kind": "robbery", "at": [1.0, 4.0]})
    decide(eng, KAZUM, a.id, True)
    z = next(z for z in eng.airspace.zones if z["id"] == "VIP-TEMP")
    assert z["active"]
    out = execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-01", "mission": "perimeter", "incident": iid, "to": [1.0, 3.5]},
    )
    assert out.rejection.code == "DRONE_NOT_READY"
    out = execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-05", "mission": "perimeter", "incident": iid, "to": [0.6, 5.5]},
    )
    assert out.rejection.code == "NO_FLY_ZONE"
    assert eng.docks["P1"].free_slots() == 0
    assert "D-01" not in [d.id for d in eng.ready_candidates([0.75, 5.0], 0)]
    eng.run_until(eng.now + 61)
    assert not z["active"] and eng.docks["P1"].usable


def test_vip_zone_reroutes_landing():
    eng = demo()
    eng.run_until("17:05")
    iid = eng.inject_alert({"kind": "robbery", "at": [1.0, 4.0]})
    d1 = eng.drones["D-01"]
    assert execute(
        eng,
        KAZUM,
        "launch",
        {"drone": "D-01", "mission": "perimeter", "incident": iid, "to": [1.0, 4.0]},
    ).ok
    eng.run_until(eng.now + 2)
    execute(eng, Identity.agent("fleet"), "rtb", {"drone": "D-01"})
    assert d1.target == "P1"
    r = _trig(eng, "vip_zone")
    decide(eng, KAZUM, r["approval_id"], True)
    assert d1.target != "P1" and d1.id not in eng.docks["P1"].reserved


def test_officer_request_room_message():
    eng = demo()
    eng.run_until("17:05")
    n = len(eng.log)
    r = _trig(eng, "officer_request")
    ev = eng.events[n:]
    assert [e["type"] for e in ev] == ["room_message"]
    assert ev[0]["payload"]["room"] == "field-units" and "@dispatch" in ev[0]["payload"]["text"]
    assert r["room"] == "field-units"


def test_trigger_api_all_kinds(tmp_path, monkeypatch):
    from sim.app import app

    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "t.db"))
    with TestClient(app) as c:
        c.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "demo"})
        c.post("/control/skip", json={"to": "17:05"})
        for kind in triggers.menu():
            r = c.post("/trigger", json={"kind": kind})
            assert r.status_code == 200, (kind, r.text)
            assert r.json()["ok"] is True
        r = c.post("/trigger", json={"kind": "meteor"})
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize("seed", range(20))
def test_random_triggers_keep_invariants(seed):
    rng = random.Random(seed)
    eng = Engine.load("FRIDAY-RUSH", mode="headless", golden=True, seed=f"S{seed}")
    kinds = list(triggers.menu())
    plan = sorted(rng.uniform(eng.T0 + 1, eng.T1 - 30) for _ in range(3))
    for t in plan:
        eng.run_until(t)
        kind = rng.choice(kinds)
        r = triggers.fire(eng, kind)
        if r.get("approval_id") and rng.random() < 0.7:
            decide(eng, KAZUM, r["approval_id"], True)
    eng.run()
    hard = ("reserve", "RED zone", "over cap", "over capacity", "named pilot", "recording")
    bad = [v for v in eng.violations if any(h in v for h in hard) and "SAFETY RTB" not in v]
    assert bad == [], bad


# ---------------------------------------------------------------- baseline
def test_baseline_friday_matches_checker():
    from sim.scenario import load_scenario

    b = baseline.run(W, load_scenario("FRIDAY-RUSH"))
    assert b.eyes_min["INC-1"] == pytest.approx(17.0)
    assert fmt(b.eyes_t["INC-2"]) == "18:08:42"
    assert b.suspect_lost["INC-2"] is True
    assert round(b.coverage_frac * 100) == 9
    assert b.released_early == 0 and b.unit_minutes_saved == 0
    assert b.first_unit_t["INC-1"] is not None


def test_baseline_quiet_shift():
    from sim.scenario import load_scenario

    b = baseline.run(W, load_scenario("QUIET-SHIFT"))
    assert b.eyes_min == {} and b.coverage_frac > 0


def test_baseline_summary_hides_future_incidents():
    from sim.scenario import load_scenario

    b = baseline.run(W, load_scenario("FRIDAY-RUSH"))
    s = b.summary(visible={"INC-1"})
    blob = json.dumps(s)
    assert "INC-2" not in blob and "18:08" not in blob


# ---------------------------------------------------------------- scorecard + KPI
@pytest.fixture(scope="module")
def golden_run():
    from sim import golden

    return golden.run("FRIDAY-RUSH")


def test_scorecard_equals_checker_block(golden_run):
    from sim import golden
    from sim.golden import ROOT

    exp = (ROOT / "docs" / "golden-timeline.txt").read_text(encoding="utf-8")
    assert golden.diff_scorecard(golden_run.text(), exp) == []


def test_compare_rows_managed_wins(golden_run):
    rows = golden_run.compare
    counted = [r for r in rows if r["counted"]]
    assert {r["key"] for r in counted} >= {
        "eyes_on",
        "robbery",
        "released_early",
        "unit_minutes_saved",
        "coverage",
    }
    assert all(r["win"] for r in counted)
    info = [r for r in rows if not r["counted"]]
    assert {r["key"]: r.get("status") for r in info if "status" in r} == {
        "reserve_breaches": "ok",
        "red_zone_entries": "ok",
    }
    assert any(r["key"] == "first_ground_unit" for r in info)


def test_quiet_golden():
    from sim import golden

    q = golden.run("QUIET-SHIFT")
    assert q.violations == [] and q.kpi["coverage_pct"] >= 95
    from sim.scenario import load_scenario

    cap = load_scenario("QUIET-SHIFT")["expected"]["agent_messages_max"]["fleet"]
    assert q.fleet_messages <= cap


def test_kpi_endpoint_and_history(tmp_path, monkeypatch):
    from sim.app import app

    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "k.db"))
    with TestClient(app) as c:
        c.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "headless"})
        c.post("/control/skip", json={"to": "17:20"})
        k = c.get("/kpi").json()
        assert k["ok"] and k["baseline"] is None and k["managed"]["eyes_on_s"]["INC-1"] == 114
        assert k["wins"] == {}
        h = c.get("/kpi/history").json()["history"]
        assert h and all(tmin(x["clock"]) <= tmin("17:20") for x in h)
        r = c.post("/baseline/run")
        assert r.status_code == 200
        k = c.get("/kpi").json()
        assert k["baseline"]["pending"] is True and k["baseline"]["eyes_on_min"] == {}
        assert "INC-2" not in json.dumps(k)  # not alerted yet at 17:20
        assert k["wins"]["eyes_on"] is None
        c.post("/control/skip", json={"to": "20:00"})
        k = c.get("/kpi").json()
        assert k["baseline"]["eyes_on_min"]["INC-1"] == pytest.approx(17.0)
        assert k["wins"]["eyes_on"] is True
        u = c.get("/units").json()
        assert len(u["units"]) == 15
