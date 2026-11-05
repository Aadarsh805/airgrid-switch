"""Phase 03 · every write and every rejection appends a decisions_log row (actor, action, code);
the golden refusals come from real validators, logged with actor + code."""

import json

import pytest

from tests.sim.helpers import tmin
from tests.sim.p3 import (
    KAZUM_WC,
    act,
    approval_id,
    approve,
    at,
    decisions,
    load,
    minutes,
    propose,
    reject,
    skip,
)


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def inc1(drone):
    return {"drone": drone, "mission": "incident", "incident": "INC-1", "to": "ring_road_j9"}


def row_for(new, action, code, actor):
    hits = [
        r
        for r in new
        if action in str(r.get("action", ""))
        and (r.get("code") or None) == code
        and actor in str(r.get("actor", ""))
    ]
    assert hits, f"no decisions row action~{action} code={code} actor~{actor} in {new}"
    return hits[-1]


CASES = [
    # (time, action, args, who, expected status, code, actor)
    ("17:14", "launch", inc1("D-01"), "dispatch", 200, None, "dispatch"),
    ("17:14", "launch", inc1("D-10"), "dispatch", 409, "DRONE_NOT_READY", "dispatch"),
    ("17:14", "launch", inc1("D-01"), "fleet", 403, "FORBIDDEN_ROLE", "fleet"),
    ("17:14", "launch", inc1("D-99"), "dispatch", 404, "NOT_FOUND", "dispatch"),
    ("17:30", "rtb", {"drone": "D-09", "dock": "P9"}, "fleet", 409, "DOCK_OFFLINE", "fleet"),
    ("17:30", "rtb", {"drone": "D-09"}, "fleet", 200, None, "fleet"),
    (
        "17:58",
        "corridor_watch",
        {"drone": "D-08", "incident": "INC-2", "to": [5.8, 3.2]},
        "airspace",
        409,
        "NO_FLY_ZONE",
        "airspace",
    ),
    (
        "17:20",
        "request_face_id",
        {"request": "face match the driver", "by": "kazum"},
        KAZUM_WC,
        409,
        "PRIVACY_POLICY",
        "kazum",
    ),
    (
        "17:20",
        "log_refusal",
        {"about": "face match", "code": "PRIVACY_POLICY", "by": "kazum"},
        "dispatch",
        200,
        None,
        "dispatch",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[f"{x[1]}-{x[5] or 'ok'}" for x in CASES])
def test_action_appends_decision(c, case):
    t, action, args, who, status, code, actor = case
    at(c, t)
    before = len(decisions(c))
    r = act(c, action, args, who=who)
    assert r.status_code == status, r.text
    new = decisions(c)[before:]
    assert new, f"{action} added no decisions row"
    row = row_for(new, action, code, actor)
    assert abs(minutes(row["sim_min"]) - tmin(t)) <= 0.1 + 1e-6, row


def test_approval_flow_rows(c):
    at(c, "17:20")
    before = len(decisions(c))
    a = approval_id(propose(c, "broadcast", {"drone": "D-09", "text": "Keep left"}))
    b = approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor temp"}))
    assert approve(c, a).status_code == 200
    assert reject(c, b, "sensor glitch").status_code == 200
    assert approve(c, a, who="dispatch").status_code == 403
    new = decisions(c)[before:]
    assert any(r.get("approval_id") == a and "dispatch" in str(r.get("actor")) for r in new), new
    assert any(
        r.get("approval_id") == a and "kazum" in str(r.get("actor")) and not r.get("code")
        for r in new
    ), new
    assert any(r.get("approval_id") == b and "kazum" in str(r.get("actor")) for r in new), new
    assert any(
        r.get("code") == "FORBIDDEN_ROLE" and "dispatch" in str(r.get("actor")) for r in new
    ), new


def test_rows_have_contract_fields(c):
    at(c, "17:20")
    rows_ = decisions(c)
    assert rows_
    for r in rows_:
        for k in ("seq", "sim_min", "actor", "action", "code"):
            assert k in r, f"missing {k}: {r}"
    seqs = [r["seq"] for r in rows_]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


# ---------- golden refusals from real validators ----------

GOLDEN_REFUSALS = [
    ("17:52", "request_face_id", "PRIVACY_POLICY"),
    ("17:55", "corridor_watch", "NO_FLY_ZONE"),
    ("18:04", "launch", "PILOT_SATURATED"),
    ("18:08", "assign_perimeter", "MISSION_EXPIRED"),
]


@pytest.fixture(scope="module")
def golden_decisions(c):
    load(c)
    skip(c, "18:10")
    return decisions(c)


@pytest.mark.parametrize("t,action,code", GOLDEN_REFUSALS, ids=[g[2] for g in GOLDEN_REFUSALS])
def test_golden_refusal_logged_by_validator(golden_decisions, t, action, code):
    hits = [
        r
        for r in golden_decisions
        if r.get("code") == code
        and action in str(r.get("action", ""))
        and abs(minutes(r["sim_min"]) - tmin(t)) <= 0.1 + 1e-6
    ]
    assert len(hits) == 1, f"{t} {action} {code}: {hits}"
    assert str(hits[0].get("actor") or "").strip(), hits[0]


def test_golden_face_id_actor_is_kazum(golden_decisions):
    r = next(r for r in golden_decisions if r.get("code") == "PRIVACY_POLICY")
    assert "kazum" in json.dumps(r.get("actor"))


def test_golden_d03_retried_under_dp2(golden_decisions):
    ok_rows = [
        r
        for r in golden_decisions
        if "launch" in str(r.get("action"))
        and not r.get("code")
        and abs(minutes(r["sim_min"]) - tmin("18:04")) <= 0.1 + 1e-6
    ]
    assert ok_rows, "18:04 retry under DP-2 not logged as a write"
