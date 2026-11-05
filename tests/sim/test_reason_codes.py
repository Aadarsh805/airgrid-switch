"""Phase 03 · every 04 §4.2 reason code: a rejecting case and a passing neighbour, over HTTP.

World states come from the headless golden run (facts checked against its frames):
17:14 D-09 on INC-1, D-10 airborne SOUTH, D-01/D-06/D-08/D-12 docked 100% · 17:18 D-07 docked 45.7%
17:30 P9 offline, P10 holds D-10 · 17:40:30 INC-2 open, no track auth · 17:58 D-08 corridor
18:05 kazum flies 4 · 18:09 INC-2 closed · 18:25 NW/NE grounded, D-01 P1, D-13 P12 (SE).
"""

import pytest

from tests.sim.p3 import (
    ACTOR,
    CODES,
    KAZUM_WC,
    act,
    approval_id,
    approve,
    at,
    auth,
    ok,
    propose,
    reject,
    rejected,
)


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def inc1_launch(drone, to="ring_road_j9", **extra):
    return {"drone": drone, "mission": "incident", "incident": "INC-1", "to": to, **extra}


# ---------- DRONE_NOT_READY ----------


def test_drone_not_ready_airborne(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-10")), "DRONE_NOT_READY")
    ok(act(c, "launch", inc1_launch("D-01")))


def test_drone_not_ready_below_gate(c):
    at(c, "17:18")  # D-07 landed 17:17:18 at 44%, still < incident gate 50
    rejected(act(c, "launch", inc1_launch("D-07")), "DRONE_NOT_READY")
    ok(act(c, "launch", inc1_launch("D-06")))


# ---------- BATTERY_RESERVE ----------


def test_battery_reserve(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-01", for_min=60)), "BATTERY_RESERVE")
    ok(act(c, "launch", inc1_launch("D-01", for_min=10)))


# ---------- NO_FLY_ZONE ----------


def test_no_fly_target_inside_red(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-08", to="old_airfield")), "NO_FLY_ZONE")
    ok(act(c, "launch", inc1_launch("D-08", to=[5.45, 2.05])))


def test_no_fly_straight_route_crosses_red(c):
    at(c, "17:14")
    # P12 (5.25, 1.0) → (5.9, 4.5): target outside RED, the straight line crosses it
    rejected(act(c, "launch", inc1_launch("D-12", to=[5.9, 4.5])), "NO_FLY_ZONE")
    ok(act(c, "launch", inc1_launch("D-12", to=[5.45, 4.45])))


def test_no_fly_corridor_into_red(c):
    at(c, "17:58")
    args = {"drone": "D-08", "incident": "INC-2"}
    rejected(act(c, "corridor_watch", {**args, "to": [5.8, 3.2]}), "NO_FLY_ZONE")
    ok(act(c, "corridor_watch", {**args, "to": [5.45, 2.1]}))


def test_no_fly_no_override_for_any_role(c):
    """No override: the watch commander's own drone-moving action is refused too."""
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-08", to="old_airfield")), "NO_FLY_ZONE")
    args = {"drone": "D-09", "incident": "INC-1"}
    rejected(
        act(c, "assign_perimeter", {**args, "to": "old_airfield"}, who=KAZUM_WC), "NO_FLY_ZONE"
    )
    ok(act(c, "assign_perimeter", {**args, "to": "ring_road_j9"}, who=KAZUM_WC))


# ---------- WEATHER_GROUNDED ----------


def test_weather_grounded_launch_from_north(c):
    at(c, "18:25")
    rejected(act(c, "launch", inc1_launch("D-01")), "WEATHER_GROUNDED")
    ok(act(c, "launch", inc1_launch("D-13")))


def test_weather_grounded_launch_into_north(c):
    at(c, "18:25")
    rejected(act(c, "launch", inc1_launch("D-13", to=[1.0, 5.0])), "WEATHER_GROUNDED")
    ok(act(c, "launch", inc1_launch("D-13", to=[1.0, 1.5])))


def test_weather_reopened(c):
    at(c, "18:51")
    ok(act(c, "launch", inc1_launch("D-01")))


# ---------- DOCK_OFFLINE / DOCK_FULL ----------


def test_dock_offline_forced_rtb(c):
    at(c, "17:30")
    rejected(act(c, "rtb", {"drone": "D-09", "dock": "P9"}), "DOCK_OFFLINE")
    ok(act(c, "rtb", {"drone": "D-09", "dock": "HUB-B"}))


def test_dock_full_forced_rtb(c):
    at(c, "17:30")
    rejected(act(c, "rtb", {"drone": "D-09", "dock": "P10"}), "DOCK_FULL")
    ok(act(c, "rtb", {"drone": "D-09"}))  # auto-booking always finds a slot


def test_dock_at_offline_and_full(c):
    at(c, "17:30")
    rejected(act(c, "dock_at", {"drone": "D-09", "dock": "P9"}), "DOCK_OFFLINE")
    rejected(act(c, "dock_at", {"drone": "D-09", "dock": "P10"}), "DOCK_FULL")
    ok(act(c, "dock_at", {"drone": "D-09", "dock": "HUB-B"}))


# ---------- PILOT_SATURATED ----------


def test_pilot_saturated_fifth_drone(c):
    at(c, "18:05")
    args = {"drone": "D-01", "mission": "perimeter", "incident": "INC-2", "to": "market_street"}
    rejected(act(c, "launch", {**args, "pilot": "kazum"}), "PILOT_SATURATED")
    ok(act(c, "launch", {**args, "pilot": "DP-1"}))


def test_pilot_saturated_take_control(c):
    at(c, "18:05")  # D-03 flies under DP-2; kazum already holds 4
    rejected(act(c, "take_control", {"drone": "D-03"}), "PILOT_SATURATED")


# ---------- SEPARATION ----------


def test_separation_explicit_band(c):
    at(c, "17:14")
    ok(act(c, "launch", inc1_launch("D-01", to=[3.0, 2.5], band=2)))
    rejected(act(c, "launch", inc1_launch("D-06", to=[3.05, 2.5], band=2)), "SEPARATION")
    ok(act(c, "launch", inc1_launch("D-06", to=[3.3, 2.5], band=2)))


def test_separation_other_band_passes(c):
    at(c, "17:14")
    ok(act(c, "launch", inc1_launch("D-01", to=[3.0, 2.5], band=2)))
    ok(act(c, "launch", inc1_launch("D-06", to=[3.05, 2.5], band=3)))


# ---------- OUT_OF_RANGE ----------


def test_out_of_range(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-01", to=[12.0, 5.0])), "OUT_OF_RANGE")  # 11.25 km
    ok(act(c, "launch", inc1_launch("D-01", to=[5.9, 5.9])))  # 5.2 km


# ---------- AUTH_REQUIRED ----------


def test_auth_required_track_without_authorisation(c):
    at(c, "17:40:30")
    rejected(act(c, "assign_track", {"drone": "D-13", "incident": "INC-2"}), "AUTH_REQUIRED")


def test_track_after_authorisation_passes(c):
    at(c, "17:42")  # golden approved the track at 17:41 (A-0003)
    ok(act(c, "assign_track", {"drone": "D-14", "incident": "INC-2"}))


def test_auth_required_broadcast_unapproved(c):
    at(c, "17:14")
    args = {"drone": "D-09", "text": "Lane 2 closed, keep left"}
    rejected(act(c, "broadcast", args), "AUTH_REQUIRED")
    aid = approval_id(propose(c, "broadcast", args))
    ok(approve(c, aid))


def test_auth_required_zone_and_footage(c):
    at(c, "17:20")
    rejected(act(c, "activate_zone", {"zone": "VIP-TEMP", "until": "21:00"}), "AUTH_REQUIRED")
    aid = approval_id(
        act(
            c,
            "request_footage_release",
            {"incident": "INC-1", "clip": "C-3", "to": "traffic court"},
        )
    )
    rejected(act(c, "release_footage", {"approval_id": aid}), "AUTH_REQUIRED")
    ok(approve(c, aid))
    ok(act(c, "release_footage", {"approval_id": aid}))


def test_zone_after_approval_passes(c):
    at(c, "17:20")
    ok(act(c, "launch", inc1_launch("D-06", to=[0.6, 5.2])))  # VIP-TEMP inactive: allowed
    aid = approval_id(propose(c, "zone", {"zone": "VIP-TEMP", "until": "21:00"}))
    ok(approve(c, aid))
    # VIP-TEMP (x 0-1.2, y 4.4-6) now active: the same kind of target is NO_FLY_ZONE
    rejected(act(c, "launch", inc1_launch("D-08", to=[0.6, 5.2])), "NO_FLY_ZONE")


# ---------- PRIVACY_POLICY ----------


@pytest.mark.parametrize("who", [KAZUM_WC, ("kazum", "field_officer")], ids=["wc", "fo"])
def test_face_id_always_refused(c, who):
    at(c, "17:52")
    req = {"request": "run face recognition on the market crowd", "by": "kazum"}
    rejected(act(c, "request_face_id", req, who=who), "PRIVACY_POLICY")
    # lawful neighbour: log the refusal
    ok(act(c, "log_refusal", {"about": req["request"], "code": "PRIVACY_POLICY", "by": "kazum"}))


def test_set_recording_outside_geofence(c):
    at(c, "17:20")
    rejected(act(c, "set_recording", {"drone": "D-02", "on": True}), "PRIVACY_POLICY")  # patrol
    ok(act(c, "set_recording", {"drone": "D-09", "on": True}))  # on station inside INC-1 geofence


def test_set_recording_off_always_allowed(c):
    at(c, "17:20")
    ok(act(c, "set_recording", {"drone": "D-02", "on": False}))


# ---------- MISSION_EXPIRED ----------


def test_mission_expired_closed_incident(c):
    at(c, "18:09")
    args = {"drone": "D-01", "mission": "perimeter", "incident": "INC-2", "to": "market_street"}
    rejected(act(c, "launch", args), "MISSION_EXPIRED")
    rejected(
        act(c, "assign_perimeter", {"drone": "D-01", "incident": "INC-2", "to": "market_street"}),
        "MISSION_EXPIRED",
    )


def test_perimeter_on_open_incident_passes(c):
    at(c, "17:50")
    ok(act(c, "assign_perimeter", {"drone": "D-14", "incident": "INC-2", "to": "market_street"}))


def test_mission_expired_decided_approval(c):
    at(c, "17:45")
    rejected(approve(c, "A-0003"), "MISSION_EXPIRED")  # golden track, approved 17:41
    rejected(reject(c, "A-0003", "too late"), "MISSION_EXPIRED")
    aid = approval_id(propose(c, "broadcast", {"drone": "D-14", "text": "Stay clear"}))
    ok(approve(c, aid))
    rejected(approve(c, aid), "MISSION_EXPIRED")


# ---------- FORBIDDEN_ROLE ----------


def test_agent_cannot_approve(c):
    at(c, "17:14")
    aid = approval_id(propose(c, "broadcast", {"drone": "D-09", "text": "Keep left"}))
    for agent in ("dispatch", "privacy", "relay"):
        rejected(approve(c, aid, who=agent), "FORBIDDEN_ROLE", 403)
        rejected(reject(c, aid, "no", who=agent), "FORBIDDEN_ROLE", 403)
    ok(approve(c, aid))


def test_agent_cannot_take_control(c):
    at(c, "17:14")
    for action in ("take_control", "release_control"):
        r = act(c, action, {"drone": "D-09"}, who="dispatch")
        rejected(r, "FORBIDDEN_ROLE", 403)


def test_role_not_allowed_action(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-01"), who="fleet"), "FORBIDDEN_ROLE", 403)
    rejected(act(c, "rtb", {"drone": "D-09"}, who="intel"), "FORBIDDEN_ROLE", 403)
    ok(act(c, "launch", inc1_launch("D-01"), who="dispatch"))


def test_claimed_role_must_match_token(c):
    at(c, "17:14")
    r = c.post(
        "/actions",
        json={"role": "dispatch", "action": "launch", "args": inc1_launch("D-01")},
        headers=auth("fleet"),
    )
    rejected(r, "FORBIDDEN_ROLE", 403)
    # a human claiming a role they do not hold
    rejected(
        act(c, "take_control", {"drone": "D-09"}, who=("t2", "remote_pilot")), "FORBIDDEN_ROLE", 403
    )


def test_human_role_mapping_t2_cannot_approve_track(c):
    at(c, "17:40:30")
    aid = approval_id(
        propose(c, "track", {"incident": "INC-2", "scope": "suspect vehicle only", "minutes": 30})
    )
    rejected(approve(c, aid, who="t2"), "FORBIDDEN_ROLE", 403)
    ok(approve(c, aid, who="kazum"))


def test_missing_or_unknown_token_401(c):
    at(c, "17:14")
    body = {"role": "dispatch", "action": "launch", "args": inc1_launch("D-01")}
    rejected(c.post("/actions", json=body), "FORBIDDEN_ROLE", 401)
    rejected(
        c.post("/actions", json=body, headers={"Authorization": "Bearer nope"}),
        "FORBIDDEN_ROLE",
        401,
    )
    rejected(c.post("/approvals/A-0001/approve", json={}), "FORBIDDEN_ROLE", 401)


# ---------- NOT_FOUND ----------


def test_not_found(c):
    at(c, "17:14")
    rejected(act(c, "launch", inc1_launch("D-99")), "NOT_FOUND", 404)
    rejected(approve(c, "A-9999"), "NOT_FOUND", 404)
    rejected(act(c, "rtb", {"drone": "D-09", "dock": "P99"}), "NOT_FOUND", 404)
    rejected(act(c, "launch", {**inc1_launch("D-01"), "incident": "INC-99"}), "NOT_FOUND", 404)
    ok(act(c, "launch", inc1_launch("D-01")))


def test_future_incident_is_not_found(c):
    """INC-2 doesn't exist at 17:14; naming it must not reveal it (rule 8)."""
    at(c, "17:14")
    r = act(c, "assign_perimeter", {"drone": "D-01", "incident": "INC-2", "to": "market_street"})
    rejected(r, "NOT_FOUND", 404)
    assert "robbery" not in r.text and "market" not in r.json()["reason"].lower()
    # neighbour: the same action on the open INC-1 works (so the 404 is the incident, not the route)
    ok(act(c, "assign_perimeter", {"drone": "D-01", "incident": "INC-1", "to": "ring_road_j9"}))


# ---------- shape ----------


def test_malformed_body_422(c):
    at(c, "17:14")
    r = c.post("/actions", json={"role": "dispatch", "args": {}}, headers=auth("dispatch"))
    assert r.status_code == 422, r.text
    b = r.json()
    assert b.get("ok") is False and b.get("error") == "invalid request", b


def test_every_code_has_a_case():
    """Guard: this file names every §4.2 code (keep it complete)."""
    from pathlib import Path

    text = Path(__file__).read_text(encoding="utf-8")
    missing = [code for code in CODES if f'"{code}"' not in text]
    assert missing == []
    assert ACTOR  # identity table in tests/sim/p3.py
