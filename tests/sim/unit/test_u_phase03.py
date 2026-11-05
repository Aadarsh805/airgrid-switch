"""Phase 03 engine-level units: auth init, decisions log, privacy append-only, simulate,
approval clock holds, track expiry (sim-engineer)."""

import sqlite3

import pytest
import yaml

from sim import actions
from sim.actions import decide, execute, propose
from sim.auth import Identity, identify, init_tokens, load_identities
from sim.clock import Clock
from sim.db import DB
from sim.engine import Engine

DISPATCH = Identity.agent("dispatch")
KAZUM = Identity.human("kazum", ["watch_commander", "remote_pilot", "field_officer"])


def demo(**kw):
    eng = Engine.load("FRIDAY-RUSH", mode="demo", **kw)
    a = propose(eng, DISPATCH, "standing_policy", {}, "brief")
    assert decide(eng, KAZUM, a.result["id"], True).ok
    return eng


def test_auth_init_writes_unique_tokens(tmp_path):
    p = init_tokens(tmp_path / "tok.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert set(data["agents"]) == {
        "dispatch",
        "fleet",
        "airspace",
        "intel",
        "relay",
        "privacy",
        "comms",
        "fire-station",  # city mode, read-only (L56)
        "hospital",
        "police",
    }
    toks = list(data["agents"].values()) + list(data["humans"].values())
    assert len(set(toks)) == len(toks)
    ids = load_identities(p)
    assert identify(data["agents"]["fleet"], ids) == Identity.agent("fleet")
    assert identify("nope", ids) is None
    with pytest.raises(FileExistsError):
        init_tokens(p)


def test_every_write_and_rejection_logged():
    eng = demo()
    eng.run_until("17:14")
    n = len(eng.decisions)
    assert execute(
        eng,
        DISPATCH,
        "launch",
        {"drone": "D-01", "mission": "incident", "incident": "INC-1", "to": [1.0, 4.0]},
    ).ok
    r = execute(eng, Identity.agent("fleet"), "launch", {"drone": "D-03"})
    assert r.status == 403 and r.rejection.code == "FORBIDDEN_ROLE"
    r = execute(
        eng,
        DISPATCH,
        "launch",
        {"drone": "D-10", "mission": "incident", "incident": "INC-1", "to": "ring_road_j9"},
    )
    assert r.status == 409 and r.rejection.code == "DRONE_NOT_READY"
    new = eng.decisions[n:]
    assert [(x["actor"], x["code"]) for x in new] == [
        ("dispatch", None),
        ("fleet", "FORBIDDEN_ROLE"),
        ("dispatch", "DRONE_NOT_READY"),
    ]
    assert all(x["kpi_before"] and x["kpi_after"] for x in new)


def test_malformed_args_are_invalid_request_not_a_code():
    eng = demo()
    with pytest.raises(actions.InvalidRequest):
        execute(eng, DISPATCH, "launch", {"drone": "D-01"})


def test_face_id_always_rejected_and_logged_even_for_kazum():
    eng = demo()
    for who in (KAZUM, DISPATCH, Identity.agent("relay")):
        r = execute(eng, who, "request_face_id", {"request": "identify the rider"})
        assert r.rejection.code == "PRIVACY_POLICY" and r.status == 409
    rows = [x for x in eng.privacy.rows() if x["kind"] == "face_id_refused"]
    assert len(rows) == 3


def test_privacy_log_append_only_in_db():
    db = DB(":memory:")
    eng = demo(db=db)
    execute(eng, KAZUM, "request_face_id", {"request": "who is that"})
    db.sync(eng)
    assert db.count("privacy_log") == 1
    for sql in ("UPDATE privacy_log SET detail='x'", "DELETE FROM privacy_log"):
        with pytest.raises(sqlite3.DatabaseError):
            db.conn.execute(sql)
    assert not hasattr(eng.privacy, "update") and not hasattr(eng.privacy, "delete")


def test_recording_derived_inside_geofence_only():
    eng = demo()
    eng.run_until("17:13")
    assert eng.drones["D-09"].recording is False  # in transit
    eng.run_until("17:14")
    assert eng.drones["D-09"].recording is True  # on station at INC-1
    assert all(not d.recording for d in eng.drones.values() if d.mission == "patrol")
    kinds = [r["kind"] for r in eng.privacy.rows() if r["drone"] == "D-09"]
    assert kinds == ["recording_on"]


def test_simulate_changes_nothing():
    eng = demo()
    eng.run_until("17:14")
    before = (eng.state_view(), len(eng.log), len(eng.decisions))
    r = execute(
        eng,
        DISPATCH,
        "simulate",
        {
            "action": "launch",
            "args": {"drone": "D-01", "mission": "incident", "incident": "INC-1", "to": [1.0, 4.0]},
        },
    )
    assert r.ok and r.result["would"]["ok"] is True
    assert r.result["kpi_delta"]["airborne"] == 1
    assert (eng.state_view(), len(eng.log), len(eng.decisions)) == before


def test_hold_clock_approval_holds_demo_clock():
    eng = demo()
    eng.run_until("17:41")
    a = propose(
        eng,
        DISPATCH,
        "track",
        {"incident": "INC-2", "scope": "vehicle", "minutes": 30},
        "track",
        hold_clock=True,
    )
    clock = Clock("demo", time_source=lambda: 0.0)
    pending = [x.id for x in eng.approvals.pending() if x.hold_clock]
    for aid in pending:
        clock.hold(f"approval:{aid}", None, approval=True)
    assert clock.held
    decide(eng, KAZUM, a.result["id"], True)
    assert not [x for x in eng.approvals.pending() if x.hold_clock]


def test_track_authorisation_expiry_rtbs_tracker():
    eng = demo()
    eng.run_until("17:41")
    a = propose(
        eng, DISPATCH, "track", {"incident": "INC-2", "scope": "vehicle", "minutes": 2}, "track"
    )
    decide(eng, KAZUM, a.result["id"], True)
    assert execute(
        eng,
        DISPATCH,
        "divert",
        {"drone": "D-13", "incident": "INC-2", "mission": "track", "pilot": "kazum"},
    ).ok
    eng.run_until("17:43.1")
    assert eng.drones["D-13"].mission == "returning"
    assert any(r["code"] == "MISSION_EXPIRED" and r["actor"] == "sim" for r in eng.decisions)
    r = execute(eng, DISPATCH, "assign_track", {"drone": "D-13", "incident": "INC-2"})
    assert r.rejection.code == "MISSION_EXPIRED"


def test_standing_policy_gates_auto_launch():
    eng = Engine.load("FRIDAY-RUSH", mode="demo")
    eng.run_until("17:13")
    assert eng.drones["D-09"].state == "docked"
    assert any(e["payload"].get("what") == "standing_policy" for e in eng.events)
