"""Phase 04 · POST /trigger: each of the 7 menu kinds has its effect; operator-logged (L33)."""

import json
import re

import pytest

from tests.sim.p3 import act, approvals, approve, at, decisions, fleet, ok, rejected, skip
from tests.sim.p4 import (
    THERMAL,
    TRIGGER_KINDS,
    TRIGGERS,
    WORLD,
    euclid,
    events,
    hms,
    minutes,
    now,
    point,
    refused,
    state,
    trigger,
    trigger_ok,
)

INC_T = re.compile(r"^INC-T\d+$")
NW = WORLD["district"]["sectors"]["NW"]
DOCKS = WORLD["docks"]
M = WORLD["drone_model"]
HOLD_MIN = 5  # policies.intrusion_hold_min (phase04-interface)


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def in_nw(p) -> bool:
    return NW["x"][0] <= p[0] < NW["x"][1] and NW["y"][0] < p[1] <= NW["y"][1]


def on_incident(c, iid):
    return {k: d for k, d in fleet(c).items() if d["state"] == "airborne" and d["incident"] == iid}


def triggered_incident(res) -> str:
    iid = res.get("incident")
    assert isinstance(iid, str) and INC_T.match(iid), f"triggered incident id: {res}"
    return iid


def inc1_launch(drone, to, **extra):
    return {"drone": drone, "mission": "incident", "incident": "INC-1", "to": to, **extra}


# ---------- CAD-alert kinds ----------


def test_fire_launches_thermal_drone(c):
    at(c, "17:05")
    iid = triggered_incident(trigger_ok(c, "fire"))
    skip(c, hms(now(c) + 1.0))
    ds = on_incident(c, iid)
    assert ds, f"no drone launched for {iid}"
    assert set(ds) & THERMAL, f"fire got {sorted(ds)}; thermal drones are {sorted(THERMAL)}"
    for d in ds.values():
        assert d["pilot"], d


def test_missing_child_launches_search_pair(c):
    at(c, "17:05")
    iid = triggered_incident(trigger_ok(c, "missing_child"))
    skip(c, hms(now(c) + 1.0))
    ds = on_incident(c, iid)
    assert len(ds) == 2, f"search pair for {iid}: {sorted(ds)}"
    assert all(d["mission"] == "search" for d in ds.values()), ds
    inc = state(c)["incidents"][iid]
    assert inc["kind"] == "missing_person", inc


def test_underpass_flood_auto_launch(c):
    at(c, "17:05")
    iid = triggered_incident(trigger_ok(c, "underpass_flood"))
    skip(c, hms(now(c) + 1.0))
    assert on_incident(c, iid), f"no auto-launch for {iid}"
    assert state(c)["incidents"][iid]["kind"] == "flood"


# ---------- drone_fault ----------


def _projected_landing(d, docks_state) -> float:
    free = [
        k
        for k, v in docks_state.items()
        if v["online"] and len(v["occupants"]) + len(v["reserved"]) < v["slots"]
    ]
    km = min(euclid(d["pos"], DOCKS[k]["at"]) for k in free)
    return d["battery"] - km / M["cruise_km_per_min"] * M["drain_pct_per_min"]["cruise"]


def test_drone_fault_safety_rtb_and_pending_out_of_service(c):
    at(c, "17:05")
    st = state(c)
    flying = {
        k: d
        for k, d in st["drones"].items()
        if d["state"] == "airborne" and d["mission"] != "returning"
    }
    assert len(flying) >= 2, flying
    tightest = min(flying, key=lambda k: _projected_landing(flying[k], st["docks"]))
    trigger_ok(c, "drone_fault")
    pend = [a for a in approvals(c, "pending") if a["type"] == "out_of_service"]
    assert len(pend) == 1, pend
    named = set(re.findall(r"D-\d{2}", json.dumps(pend[0]["args"])))
    assert named == {tightest}, f"out_of_service card names {named}, tightest is {tightest}"
    d = fleet(c)[tightest]
    assert d["mission"] == "returning", f"{tightest} not on safety RTB: {d}"
    assert d["out_of_service"] is False, "out of service before a human approved"
    others = {k for k, v in fleet(c).items() if k in flying and v["mission"] == "returning"}
    assert others == {tightest}, others


# ---------- unauthorised_drone ----------


def test_unauthorised_drone_separation_hold(c):
    at(c, "17:13")
    t0 = now(c)
    before = {k: d for k, d in fleet(c).items() if d["state"] == "airborne" and in_nw(d["pos"])}
    trigger_ok(c, "unauthorised_drone")
    target = [1.4, 3.5]  # NW
    assert in_nw(target)
    rejected(act(c, "launch", inc1_launch("D-01", target)), "SEPARATION")
    skip(c, hms(t0 + 0.5))
    after = fleet(c)
    for k, d in before.items():
        if after[k]["state"] == "airborne":
            assert euclid(d["pos"], after[k]["pos"]) <= 0.01, f"{k} moved in NW hold"
    rejected(act(c, "launch", inc1_launch("D-01", target)), "SEPARATION")
    skip(c, hms(t0 + HOLD_MIN + 0.2))
    ok(act(c, "launch", inc1_launch("D-01", target)))


# ---------- vip_zone ----------


def _vip(c) -> dict:
    return next(z for z in state(c)["zones"] if z["id"] == "VIP-TEMP")


def test_vip_zone_approval_then_no_fly(c):
    at(c, "17:13")
    t0 = now(c)
    trigger_ok(c, "vip_zone")
    pend = [a for a in approvals(c, "pending") if a["type"] == "zone"]
    assert len(pend) == 1, pend
    assert "VIP-TEMP" in json.dumps(pend[0]), pend[0]
    assert _vip(c)["active"] is False, "zone active before approval"
    ok(approve(c, pend[0]["id"]))
    z = _vip(c)
    assert z["active"] is True, z
    until = TRIGGERS["vip_zone"]["approval"]["until_offset"]
    assert abs(minutes(z["until"]) - (t0 + until)) <= 0.1 + 1e-6, z
    # P1 sits inside VIP-TEMP: D-01 cannot launch
    refused(act(c, "launch", inc1_launch("D-01", "ring_road_j9")))
    # P4 (5.25, 5.0) -> (0.1, 4.35): target outside VIP-TEMP, the straight line crosses it
    rejected(act(c, "launch", inc1_launch("D-04", [0.1, 4.35])), "NO_FLY_ZONE")
    ok(act(c, "launch", inc1_launch("D-04", [0.1, 3.9])))


# ---------- officer_request ----------


def test_officer_request_room_message(c):
    at(c, "17:05")
    t0 = now(c)
    before = state(c)
    trigger_ok(c, "officer_request")
    want = TRIGGERS["officer_request"]["room_message"]
    msgs = [e for e in events(c, since=hms(t0)) if e["type"] == "room_message"]
    assert len(msgs) == 1, msgs
    p = msgs[0].get("payload") or {}
    assert p.get("room") == want["room"], msgs[0]
    assert p.get("text") == want["text"], msgs[0]
    after = state(c)
    assert after["drones"] == before["drones"], "officer_request changed the fleet"
    assert after["approvals_pending"] == before["approvals_pending"]


# ---------- unknown kind + operator log ----------


def test_unknown_trigger_not_found(c):
    at(c, "17:05")
    rejected(trigger(c, "alien_invasion"), "NOT_FOUND", status=404)


@pytest.mark.parametrize("kind", [*TRIGGER_KINDS, "alien_invasion"])
def test_trigger_writes_operator_decision_row(c, kind):
    at(c, "17:13")
    n0 = len(decisions(c))
    trigger(c, kind)
    new = decisions(c)[n0:]
    mine = [r for r in new if r["actor"] == "operator" and kind in str(r["action"])]
    assert mine, f"no operator decisions_log row for trigger {kind}: {new}"


def test_menu_has_seven_kinds():
    assert len(TRIGGER_KINDS) == 7, TRIGGER_KINDS
    point(TRIGGERS["fire"]["cad_alert"]["at"])
