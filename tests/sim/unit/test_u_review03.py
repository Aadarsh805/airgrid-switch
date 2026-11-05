"""Phase 03 review fixes (sim-engineer): operator rows, consume-on-use approvals, INC-T ids,
simulate logging, token identity as actor, queued auto-launch."""

from fastapi.testclient import TestClient

from sim.actions import decide, execute, propose
from sim.app import app
from sim.auth import Identity
from sim.engine import Engine

DISPATCH = Identity.agent("dispatch")
KAZUM = Identity.human("kazum", ["watch_commander", "remote_pilot", "field_officer"])


def demo(approve_policy=True):
    eng = Engine.load("FRIDAY-RUSH", mode="demo")
    if approve_policy:
        a = propose(eng, DISPATCH, "standing_policy", {}, "brief")
        decide(eng, KAZUM, a.result["id"], True)
    return eng


def test_operator_controls_and_triggers_are_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "op.db"))
    with TestClient(app) as c:
        c.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "demo"})
        c.post("/control/skip", json={"to": "17:05"})
        c.post("/control/speed", json={"sim_min_per_s": 2})
        r = c.post("/trigger", json={"kind": "fire"})
        assert r.json()["result"]["incident"] == "INC-T1"
        c.post("/trigger", json={"kind": "drone_fault"})
        rows = [(x["actor"], x["action"]) for x in c.get("/decisions").json()["decisions"]]
    assert ("operator", "control:skip") in rows and ("operator", "control:speed") in rows
    assert ("operator", "trigger:fire") in rows and ("operator", "trigger:drone_fault") in rows


def test_broadcast_approval_consumed_on_use():
    eng = demo()
    eng.run_until("17:14")
    a = propose(eng, DISPATCH, "broadcast", {"drone": "D-09"}, "x")
    decide(eng, KAZUM, a.result["id"], True)
    call = ("broadcast", {"drone": "D-09", "text": "Keep left"})
    assert execute(eng, KAZUM, *call).ok
    r = execute(eng, KAZUM, *call)
    assert r.rejection.code == "AUTH_REQUIRED" and r.rejection.hint.startswith("propose again")


def test_divert_off_incident_approval_consumed_on_use():
    eng = demo()
    eng.run_until("17:14")
    eng.incidents["INC-X"] = dict(eng.incidents["INC-1"], incident="INC-X")
    a = propose(eng, DISPATCH, "divert_off_incident", {"drone": "D-09"}, "x")
    decide(eng, KAZUM, a.result["id"], True)
    there = {"drone": "D-09", "incident": "INC-X", "mission": "perimeter"}
    back = {"drone": "D-09", "incident": "INC-1", "mission": "perimeter"}
    assert execute(eng, KAZUM, "divert", there).ok
    r = execute(eng, KAZUM, "divert", back)
    assert r.rejection.code == "AUTH_REQUIRED" and r.rejection.hint.startswith("propose again")


def test_footage_release_consumed():
    eng = demo()
    eng.run_until("17:14")
    req = execute(
        eng,
        Identity.agent("privacy"),
        "request_footage_release",
        {"incident": "INC-1", "clip": "C-1", "to": "traffic court"},
    )
    aid = req.result["approval_id"]
    decide(eng, KAZUM, aid, True)
    assert execute(eng, KAZUM, "release_footage", {"approval_id": aid}).ok
    r = execute(eng, KAZUM, "release_footage", {"approval_id": aid})
    assert r.rejection.code == "AUTH_REQUIRED"


def test_track_authorisation_is_not_consumed():
    eng = demo()
    eng.run_until("17:41")
    a = propose(eng, DISPATCH, "track", {"incident": "INC-2", "minutes": 30}, "t")
    decide(eng, KAZUM, a.result["id"], True)
    assert execute(eng, KAZUM, "assign_track", {"drone": "D-13", "incident": "INC-2"}).ok
    assert execute(eng, KAZUM, "assign_track", {"drone": "D-13", "incident": "INC-2"}).ok


def test_trigger_ids_are_their_own_series():
    eng = demo()
    assert eng.inject_alert({"kind": "fire", "at": [2.25, 3.4]}) == "INC-T1"
    assert eng.inject_alert({"kind": "flood", "at": [3.0, 1.6]}) == "INC-T2"
    eng.run_until("17:12")
    assert "INC-1" in eng.incidents  # scheduled series untouched


def test_simulate_face_id_logged_for_real_and_refused_simulate_logged():
    eng = demo()
    n_priv, n_dec = len(eng.privacy), len(eng.decisions)
    r = execute(
        eng, DISPATCH, "simulate", {"action": "request_face_id", "args": {"request": "who is he"}}
    )
    assert r.ok and r.result["would"]["code"] == "PRIVACY_POLICY"
    assert len(eng.privacy) == n_priv + 1
    assert eng.decisions[n_dec]["code"] == "PRIVACY_POLICY"
    r = execute(eng, DISPATCH, "simulate", {"action": "approve", "args": {}})
    assert not r.ok and r.rejection.code == "FORBIDDEN_ROLE"
    assert eng.decisions[-1]["action"] == "simulate" and eng.decisions[-1]["code"]


def test_actor_is_token_identity_by_is_detail():
    eng = demo()
    execute(
        eng, Identity.agent("relay"), "request_face_id", {"request": "identify him", "by": "kazum"}
    )
    row = eng.privacy.rows()[-1]
    assert row["actor"] == "relay" and "requested_by: kazum" in row["detail"]
    assert eng.decisions[-1]["actor"] == "relay"
    execute(
        eng,
        DISPATCH,
        "log_refusal",
        {"about": "face recognition", "code": "PRIVACY_POLICY", "by": "kazum"},
    )
    assert eng.privacy.rows()[-1]["actor"] == "dispatch"


def test_golden_face_id_actor_is_kazum():
    eng = Engine.load("FRIDAY-RUSH", golden=True)
    eng.run_until("17:52")
    row = [r for r in eng.decisions if r["action"] == "request_face_id"][0]
    assert row["actor"] == "kazum"


def test_auto_launch_queued_until_standing_policy_approved():
    eng = demo(approve_policy=False)
    eng.run_until("17:14")
    assert eng.drones["D-09"].state == "docked"
    assert sum(e["payload"].get("what") == "standing_policy" for e in eng.events) == 1
    a = propose(eng, DISPATCH, "standing_policy", {}, "late")
    decide(eng, KAZUM, a.result["id"], True)
    eng.run_until("17:14.1")
    d = eng.drones["D-09"]
    assert (d.state, d.incident) == ("airborne", "INC-1")


def test_queued_auto_launch_skipped_if_incident_already_covered():
    eng = demo(approve_policy=False)
    eng.run_until("17:13")
    assert execute(
        eng,
        DISPATCH,
        "launch",
        {"drone": "D-05", "mission": "incident", "incident": "INC-1", "to": "ring_road_j9"},
    ).ok
    a = propose(eng, DISPATCH, "standing_policy", {}, "late")
    decide(eng, KAZUM, a.result["id"], True)
    eng.run_until("17:14")
    assert eng.drones["D-09"].state == "docked" and not eng.pending_auto
