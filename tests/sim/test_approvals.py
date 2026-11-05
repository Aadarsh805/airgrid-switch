"""Phase 03 · approvals: proposals, ids, decide, effects, status filter, standing policy gate."""

import json

import pytest

from tests.sim.p3 import (
    act,
    approval_id,
    approvals,
    approve,
    at,
    decisions,
    drone,
    fleet,
    load,
    minutes,
    ok,
    propose,
    reject,
    rejected,
    skip,
)

TRACK = {"incident": "INC-2", "scope": "suspect vehicle only; district; 30 min", "minutes": 30}


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def by_id(rows_, aid):
    hits = [r for r in rows_ if r.get("id") == aid]
    assert len(hits) == 1, f"{aid}: {len(hits)} rows"
    return hits[0]


def test_proposal_pending_and_sequential_ids(c):
    at(c, "17:05", mode="demo")
    ids = [
        approval_id(propose(c, "broadcast", {"drone": "D-02", "text": "Test one"})),
        approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor temp"})),
        approval_id(propose(c, "zone", {"zone": "VIP-TEMP", "until": "21:00"})),
    ]
    assert ids == ["A-0001", "A-0002", "A-0003"]
    pending = approvals(c, "pending")
    for aid in ids:
        assert by_id(pending, aid)["status"] == "pending"


def test_proposal_result_says_pending(c):
    at(c, "17:05", mode="demo")
    res = ok(propose(c, "broadcast", {"drone": "D-02", "text": "Test"}))
    assert res.get("status") == "pending"


def test_golden_ids_match_demo_script(c):
    at(c, "17:45")
    approved = approvals(c, "approved")
    types = {r["id"]: r.get("type") for r in approved}
    assert types.get("A-0001") == "standing_policy"
    assert types.get("A-0002") == "broadcast"
    assert types.get("A-0003") == "track"
    nxt = approval_id(propose(c, "broadcast", {"drone": "D-14", "text": "Stay clear"}))
    assert nxt == "A-0004"


def test_status_filter(c):
    at(c, "17:20")
    a = approval_id(propose(c, "broadcast", {"drone": "D-09", "text": "Keep left"}))
    b = approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor temp"}))
    d = approval_id(propose(c, "broadcast", {"drone": "D-09", "text": "Slow down"}))
    ok(approve(c, a))
    ok(reject(c, b, "drone is fine, sensor glitch"))
    pend = {r["id"] for r in approvals(c, "pending")}
    appr = {r["id"] for r in approvals(c, "approved")}
    rej = {r["id"] for r in approvals(c, "rejected")}
    assert d in pend and a not in pend and b not in pend
    assert a in appr and b not in appr and d not in appr
    assert b in rej and a not in rej and d not in rej
    assert {r.get("status") for r in approvals(c, "approved")} == {"approved"}
    everything = {r["id"] for r in approvals(c)}
    assert {a, b, d} <= everything


def test_reject_records_reason_and_human(c):
    at(c, "17:20")
    aid = approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor temp"}))
    ok(reject(c, aid, "drone is fine, sensor glitch"))
    row = by_id(approvals(c, "rejected"), aid)
    assert "sensor glitch" in json.dumps(row)
    assert "kazum" in json.dumps(row)


def test_track_approval_creates_authorisation_with_expiry(c):
    at(c, "17:41", mode="demo")  # INC-2 opened at 17:40 by the CAD alert; no golden actions
    rejected(act(c, "assign_track", {"drone": "D-13", "incident": "INC-2"}), "AUTH_REQUIRED")
    aid = approval_id(propose(c, "track", TRACK))
    res = approve(c, aid)
    ok(res)
    row = by_id(approvals(c, "approved"), aid)
    blob = res.text + json.dumps(row)
    r = c.get("/incidents/INC-2")
    if r.status_code == 200:
        blob += r.text
    assert "18:11" in blob or "1091" in blob, f"no 30-min expiry (18:11) in {blob[:600]}"
    ok(act(c, "assign_track", {"drone": "D-13", "incident": "INC-2"}))


def test_track_authorisation_expires(c):
    at(c, "17:41", mode="demo")
    aid = approval_id(propose(c, "track", TRACK))
    ok(approve(c, aid))
    ok(act(c, "assign_track", {"drone": "D-13", "incident": "INC-2"}))
    skip(c, "18:12")
    trackers = [k for k, d in fleet(c).items() if d.get("mission") == "track"]
    assert trackers == [], f"still tracking after the auth expired: {trackers}"
    expired = [
        r
        for r in decisions(c)
        if r.get("code") == "MISSION_EXPIRED" and minutes(r["sim_min"]) > 1090
    ]
    assert expired, "auth expiry not logged as MISSION_EXPIRED"
    rejected(act(c, "assign_track", {"drone": "D-06", "incident": "INC-2"}), "MISSION_EXPIRED")


def test_loudspeaker_runs_on_approval(c):
    at(c, "17:20")
    aid = approval_id(
        propose(c, "broadcast", {"drone": "D-09", "text": "Lane 2 closed, keep left"})
    )
    before = len(decisions(c))
    ok(approve(c, aid))
    new = decisions(c)[before:]
    assert any(r.get("approval_id") == aid for r in new), new


def test_zone_approval_activates_until(c):
    at(c, "17:20")
    aid = approval_id(propose(c, "zone", {"zone": "VIP-TEMP", "until": "21:00"}))
    ok(approve(c, aid))
    r = c.get("/airspace")
    text = r.text if r.status_code == 200 else c.get("/world").text
    assert "VIP-TEMP" in text


def test_unknown_approval_type_422(c):
    at(c, "17:05", mode="demo")
    r = propose(c, "launch_everything", {}, who="dispatch")
    assert r.status_code == 422, r.text


# ---------- standing policy gates auto-launch ----------


def _incident_drones(c):
    return [
        k
        for k, d in fleet(c).items()
        if d.get("state") == "airborne" and (d.get("mission") == "incident" or d.get("incident"))
    ]


def test_no_auto_launch_without_standing_policy(c):
    load(c, mode="demo")
    skip(c, "17:14")
    d09 = drone(c, "D-09")
    assert d09["state"] == "docked", d09
    assert _incident_drones(c) == [], "auto-launch ran without the standing policy"
    assert approvals(c, "approved") == []


def test_auto_launch_after_standing_policy_approved(c):
    load(c, mode="demo")
    skip(c, "17:05")
    aid = approval_id(propose(c, "standing_policy", {"kinds": ["accident", "fire", "flood"]}))
    assert aid == "A-0001"
    ok(approve(c, aid))
    skip(c, "17:14")
    d09 = drone(c, "D-09")
    assert d09["state"] == "airborne" and d09.get("mission") == "incident", d09
    assert "INC-1" in json.dumps(d09)
