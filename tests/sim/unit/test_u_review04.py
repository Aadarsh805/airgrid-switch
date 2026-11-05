"""Phase 04 review fixes: one test per finding (hints, `at`, safety checks, evidence, /kpi leaks,
intrusion targets, VIP exit, DB tables, Fleet cadence, ready count, trigger after shift)."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sim import baseline, golden, incidents, triggers
from sim.actions import decide, execute, propose
from sim.app import app
from sim.auth import Identity
from sim.engine import Engine
from sim.geo import in_rect, tmin
from sim.world import load_world

DISPATCH = Identity.agent("dispatch")
COMMS = Identity.agent("comms")
FLEET = Identity.agent("fleet")
KAZUM = Identity.human("kazum", ["watch_commander", "remote_pilot", "field_officer"])
W = load_world()
VIP = next(z for z in W.raw["zones"] if z["id"] == "VIP-TEMP")["rect"]


def demo():
    eng = Engine.load("FRIDAY-RUSH", mode="demo")
    a = propose(eng, DISPATCH, "standing_policy", {}, "brief")
    decide(eng, KAZUM, a.result["id"], True)
    return eng


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "r.db"))
    with TestClient(app) as client:
        client.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "headless"})
        yield client


def task_pc2(until="17:21"):
    eng = demo()
    eng.run_until("17:13")
    assert execute(eng, COMMS, "task_unit", {"unit": "patrol_car_2", "incident": "INC-1"}).ok
    eng.run_until(until)
    return eng


def release(eng, evidence):
    args = {"unit": "patrol_car_2", "incident": "INC-1", "evidence": evidence}
    return execute(eng, COMMS, "release_unit", args)


# ---- 1. hints carry no scenario text
def test_release_hint_is_generic():
    r = release(task_pc2(), "nothing useful")
    hint = r.rejection.hint
    assert r.rejection.code == "AUTH_REQUIRED"
    assert "airgrid incidents --id INC-1" in hint
    assert "minor" not in hint and "D-09" not in hint


@pytest.mark.parametrize("word", ["minor", "walking", "scooter", "D-09", "hoodie"])
def test_no_scenario_text_in_rules_source(word):
    assert word not in Path("sim/rules.py").read_text(encoding="utf-8")


# ---- 2. `at` validation
@pytest.mark.parametrize(
    "bad",
    [[1], ["a", "b"], [1, 2, 3], [99, 99], [-1, 2], [True, 2], "nowhere", 5, {}, "25:00", ""],
)
@pytest.mark.parametrize("kind", ["fire", "unauthorised_drone"])
def test_bad_at_is_422_and_no_state_change(c, kind, bad):
    c.post("/control/skip", json={"to": "17:05"})
    before = c.get("/state").json()
    n = len(c.get("/events").json()["events"])
    r = c.post("/trigger", json={"kind": kind, "at": bad})
    assert r.status_code == 422, r.text
    assert r.json()["ok"] is False and "code" not in r.json()
    after = c.get("/state").json()
    assert after["incidents"] == before["incidents"] and after["intrusions"] == []
    assert len(c.get("/events").json()["events"]) == n
    assert c.post("/control/skip", json={"to": "17:30"}).status_code == 200


@pytest.mark.parametrize("good", [[1, 2], [0.5, 5.5], [0, 6], "market_street", "17:20", None])
def test_good_at_accepted(c, good):
    c.post("/control/skip", json={"to": "17:05"})
    assert c.post("/trigger", json={"kind": "fire", "at": good}).status_code == 200
    assert c.post("/control/skip", json={"to": "17:30"}).status_code == 200


def test_location_at_is_used_for_alert_and_intrusion():
    eng = demo()
    eng.run_until("17:05")
    r = triggers.fire(eng, "fire", at="market_street")
    assert eng.incidents[r["incident"]]["at"] == "market_street"
    assert triggers.fire(eng, "unauthorised_drone", at="market_street")["sector"] == "SE"


# ---- 3. safety checks are not wins (L37k)
@pytest.fixture(scope="module")
def friday():
    return golden.run("FRIDAY-RUSH")


def test_safety_rows_not_counted(friday):
    rows = {r["key"]: r for r in friday.compare}
    for key in ("reserve_breaches", "red_zone_entries"):
        r = rows[key]
        assert r["counted"] is False and r["status"] == "ok" and r["baseline"] == "n/a"
    assert friday.baseline["reserve_breaches"] is None
    assert sum(1 for r in friday.compare if r["counted"]) == 5


def _fake_run(monkeypatch, friday, **change):
    rows = copy.deepcopy(friday.compare)
    key = change.pop("key")
    next(r for r in rows if r["key"] == key).update(change)
    fake = copy.copy(friday)
    fake.compare = rows
    monkeypatch.setattr(golden, "run", lambda *_a, **_k: fake)


def test_baseline_cli_friday_5_of_5(friday, monkeypatch, capsys):
    monkeypatch.setattr(golden, "run", lambda *_a, **_k: friday)
    assert baseline.main(["FRIDAY-RUSH"]) == 0
    out = capsys.readouterr().out
    assert "managed wins 5/5 counted lines" in out
    assert "[ok  ] reserve breaches" in out and "[ok  ] red-zone entries" in out


def test_baseline_cli_exits_1_on_safety_fail(friday, monkeypatch, capsys):
    _fake_run(monkeypatch, friday, key="red_zone_entries", status="fail", managed=1)
    assert baseline.main(["FRIDAY-RUSH"]) == 1
    assert "[FAIL] red-zone entries" in capsys.readouterr().out


def test_baseline_cli_exits_1_on_lost_line(friday, monkeypatch, capsys):
    _fake_run(monkeypatch, friday, key="coverage", win=False)
    assert baseline.main(["FRIDAY-RUSH"]) == 1
    assert "[LOSS]" in capsys.readouterr().out


def test_quiet_shift_baseline_is_sensible(capsys):
    assert baseline.main(["QUIET-SHIFT"]) == 0
    out = capsys.readouterr().out
    assert "managed wins 1/1 counted lines" in out and "[ok  ] reserve breaches" in out


# ---- 4. evidence quotes a full fact value
FACTS = [{"fact": {"injuries": "2 minor, walking", "vehicles": 3, "fire": "none"}}]


@pytest.mark.parametrize(
    "ev",
    [
        "critical injuries, nobody walking",
        "feed shows minor injuries",
        "D-13 on station",  # '3' inside a longer token
        "none of this matters",  # short value without its field
        "13 vehicles",
    ],
)
def test_partial_quotes_rejected(ev):
    assert not incidents.evidence_matches(ev, FACTS)


@pytest.mark.parametrize("ev", ["D-09 feed: 2 MINOR, Walking", "vehicles: 3", "fire: none"])
def test_full_value_accepted(ev):
    assert incidents.evidence_matches(ev, FACTS)


def test_shared_word_cannot_release_a_unit():
    eng = task_pc2()
    assert release(eng, "critical injuries, nobody walking").rejection.code == "AUTH_REQUIRED"
    assert eng.ground.units["patrol_car_2"].incident == "INC-1"


def test_golden_release_quotes_the_fact_keeps_the_line(friday):
    e = next(x for x in friday.events if x["type"] == "unit_released")
    assert e["payload"]["evidence"] == "2 minor, walking"
    assert e["text"] == "✅ patrol_car_2 released early (feed shows minor injuries)"


# ---- 5. /kpi never reveals the future
def test_kpi_mid_shift_withholds_future_at_1741(c):
    c.post("/control/skip", json={"to": "17:41"})
    assert c.post("/baseline/run").status_code == 200
    k = c.get("/kpi").json()
    b = k["baseline"]
    assert b["pending"] is True and b["coverage_pct"] is None and b["suspect_lost"] == {}
    assert b["eyes_on_min"] == {} and b["first_unit_on_scene_min"] == {}
    for key in ("eyes_on", "robbery", "coverage"):
        assert k["wins"][key] is None
    blob = json.dumps(k, ensure_ascii=False)
    for leak in ("18:08", "LOST", "17.0", "28.7", "8.6"):
        assert leak not in blob, leak


def test_kpi_before_any_alert_names_no_incident(c):
    c.post("/control/skip", json={"to": "17:05"})
    c.post("/baseline/run")
    k = c.get("/kpi").json()
    assert "robbery" not in k["wins"] and "eyes_on" not in k["wins"]


def test_kpi_incident_line_appears_when_closed(c):
    c.post("/control/skip", json={"to": "18:10"})
    c.post("/baseline/run")
    b = c.get("/kpi").json()
    assert b["baseline"]["pending"] is True
    assert set(b["baseline"]["eyes_on_min"]) == {"INC-2"}  # closed 18:07; INC-1 never closes
    assert b["baseline"]["suspect_lost"] == {} and b["wins"]["robbery"] is None


def test_kpi_after_shift_is_final(c):
    c.post("/control/skip", json={"to": "20:00"})
    c.post("/baseline/run")
    k = c.get("/kpi").json()
    assert k["baseline"]["pending"] is False and k["baseline"]["suspect_lost"] == {"INC-2": True}
    assert len(k["wins"]) == 5 and all(v is True for v in k["wins"].values())
    assert k["safety"] == {"reserve_breaches": "ok", "red_zone_entries": "ok"}


# ---- 6. intrusion hold blocks policy launches by TARGET sector
def test_patrol_launch_into_held_sector_waits_then_retries():
    eng = demo()
    eng.run_until("17:05")
    r = triggers.fire(eng, "unauthorised_drone", at=[4.5, 4.5])  # NE: NORTH centre (3, 5)
    assert r["sector"] == "NE"
    eng.drones["D-02"].bat = 47.0
    eng.run_until("17:09")
    held = [e for e in eng.events if e["type"] == "launch_held"]
    assert len(held) == 1 and held[0]["payload"]["code"] == "SEPARATION"
    assert not [e for e in eng.events if e["type"] == "rotation" and e["t"] < tmin("17:10")]
    eng.run_until("17:11")
    assert [e for e in eng.events if e["type"] == "rotation" and e["payload"]["loop"] == "NORTH"]


def test_auto_launch_into_held_sector_waits_then_retries():
    eng = demo()
    eng.run_until("17:05")
    triggers.fire(eng, "unauthorised_drone", at=[4.5, 4.5])
    iid = triggers.fire(eng, "fire", at=[4.0, 4.5])["incident"]
    eng.run_until("17:09")
    assert not [d for d in eng.drones.values() if d.incident == iid]
    assert [e for e in eng.events if e["type"] == "launch_held"]
    eng.run_until("17:11")
    assert [d for d in eng.drones.values() if d.incident == iid]


# ---- 7. VIP activation: nobody stays inside
def _launch_d01(eng, to):
    iid = eng.inject_alert({"kind": "robbery", "at": to})
    args = {"drone": "D-01", "mission": "perimeter", "incident": iid, "to": to}
    assert execute(eng, KAZUM, "launch", args).ok
    return iid


def _approve_vip(eng):
    r = triggers.fire(eng, "vip_zone")
    assert decide(eng, KAZUM, r["approval_id"], True).ok


def test_vip_activation_evicts_drone_inside():
    eng = demo()
    eng.run_until("17:05")
    _launch_d01(eng, [0.75, 5.5])
    eng.run_until("17:08")
    d = eng.drones["D-01"]
    assert in_rect(d.pos, VIP)
    _approve_vip(eng)
    assert d.exit_point is not None and d.mission == "returning"
    ev = [e for e in eng.events if e["type"] == "zone_exit"]
    assert ev and ev[0]["payload"]["code"] == "NO_FLY_ZONE" and ev[0]["timeline"]
    eng.run_until(eng.now + 3)
    assert d.exit_point is None and not in_rect(d.pos, VIP)
    for _ in range(40):
        eng.step()
        assert not (d.state == "airborne" and in_rect(d.pos, VIP))
    assert d.state == "docked" and d.slot != "P1"
    assert eng.violations == []


def test_vip_activation_inside_drone_with_outside_target_keeps_mission():
    eng = demo()
    eng.run_until("17:05")
    _launch_d01(eng, [2.0, 4.8])
    eng.step()
    d = eng.drones["D-01"]
    assert in_rect(d.pos, VIP)
    _approve_vip(eng)
    assert d.mission == "perimeter" and d.exit_point is not None
    while not d.on_station:
        eng.step()
        assert d.exit_point is not None or not in_rect(d.pos, VIP)
    assert d.pos == [2.0, 4.8] and eng.violations == []


def test_route_round_active_vip_still_lands():
    """A drone north-west of P2's approach routes round the zone corner and lands."""
    eng = demo()
    eng.run_until("17:05")
    _launch_d01(eng, [0.9, 4.2])
    eng.run_until("17:09")
    d = eng.drones["D-01"]
    _approve_vip(eng)
    d.pos = [0.1, 4.3]
    d.target = [2.25, 5.0]  # straight line crosses the zone
    d.on_station = False
    for _ in range(60):
        eng.step()
        assert not in_rect(d.pos, VIP), d.pos
        if d.on_station:
            break
    assert d.on_station and d.pos == [2.25, 5.0] and eng.violations == []


def test_invariant_flags_drone_inside_active_vip():
    eng = demo()
    eng.run_until("17:05")
    _approve_vip(eng)
    d = eng.drones["D-02"]
    d.pos, d.exit_point = [0.5, 5.5], None
    d.hold_until = eng.now + 5  # sits still: nothing moves it out
    eng.step()
    assert any("inside an active VIP zone" in v for v in eng.violations)


def test_auto_launch_never_routes_into_no_entry_zone():
    eng = demo()
    eng.run_until("17:05")
    iid = triggers.fire(eng, "fire", at=[5.8, 3.0])["incident"]  # inside RED-AIRFIELD
    eng.run_until("17:07")
    assert not [d for d in eng.drones.values() if d.incident == iid]
    e = [x for x in eng.events if x["type"] == "auto_launch_unavailable"]
    assert e and e[-1]["payload"]["code"] == "NO_FLY_ZONE"
    assert eng.violations == []


# ---- 8. feed_facts + units tables
def test_db_feed_facts_and_units(tmp_path, monkeypatch):
    path = tmp_path / "d.db"
    monkeypatch.setenv("AIRGRID_DB", str(path))
    with TestClient(app) as cl:
        cl.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "headless"})
        cl.post("/control/skip", json={"to": "17:20"})
        db = sqlite3.connect(path)
        facts = db.execute("SELECT incident, sim_min, fact_json FROM feed_facts").fetchall()
        units = dict(db.execute("SELECT id, status FROM units").fetchall())
        tasked = db.execute("SELECT incident FROM units WHERE id='ambulance_1'").fetchone()
        db.close()
    assert sorted(x[1] for x in facts) == [tmin("17:14"), tmin("17:18")]
    assert "cleared" not in json.dumps(facts)  # the 17:33 fact is not revealed yet
    assert len(units) == 15 and units["patrol_car_3"] == "available"
    assert tasked == ("INC-1",)


# ---- 9. Fleet cadence from the world
def test_fleet_cadence_read_from_world():
    w = copy.deepcopy(W)
    eng = Engine.load("QUIET-SHIFT", world=w)
    w.policies["fleet_status_every_min"] = 60
    assert golden.fleet_messages(eng) == 3
    w.policies["fleet_status_every_min"] = 30
    assert golden.fleet_messages(eng) == 5


# ---- 10. faulted drones aren't READY
def test_kpi_ready_excludes_faulted_docked_drone():
    eng = demo()
    eng.run_until("17:01")
    n = eng.kpi.strip(eng)["ready"]
    assert eng.ready_count() == n
    eng.drones["D-01"].fault = "motor_temp"
    assert eng.kpi.strip(eng)["ready"] == n - 1
    eng.kpi.ready_min = (99, None)
    eng.kpi.tick(eng, eng.now)
    assert eng.kpi.ready_min[0] == n - 1


# ---- 11. trigger after the shift
def test_trigger_after_shift_end_is_mission_expired(c):
    c.post("/control/skip", json={"to": "20:00"})
    n = len(c.get("/state").json()["incidents"])
    r = c.post("/trigger", json={"kind": "fire"})
    assert r.status_code == 409 and r.json()["code"] == "MISSION_EXPIRED"
    assert len(c.get("/state").json()["incidents"]) == n
    row = c.get("/decisions").json()["decisions"][-1]
    assert (row["actor"], row["action"], row["code"]) == (
        "operator",
        "trigger:fire",
        "MISSION_EXPIRED",
    )
