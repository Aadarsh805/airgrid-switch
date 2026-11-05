"""Pure validators: every §4.2 code has a rejecting and a passing case (sim-engineer)."""

import pytest

from sim import rules
from sim.rules import CODES, OK, Rejection
from sim.world import load_world

W = load_world()
RED = next(z for z in W.raw["zones"] if z["kind"] == "red")
VIP = dict(next(z for z in W.raw["zones"] if z["kind"] == "vip"), active=True)


def rej(r, code):
    assert isinstance(r, Rejection) and r.code == code, r
    assert r.reason.strip() and r.hint.strip()
    assert r.body() == {"ok": False, "code": code, "reason": r.reason, "hint": r.hint}


def test_only_contract_codes_exist():
    assert len(CODES) == 14
    with pytest.raises(ValueError):
        Rejection("BAD_CODE", "x", "y")


def test_battery_reserve():
    rej(rules.battery_reserve("D-01", 24.9, 25), "BATTERY_RESERVE")
    assert rules.battery_reserve("D-01", 25.0, 25) is OK


def test_drone_not_ready():
    rej(rules.drone_not_ready("D-02", "airborne", 90, 50, True), "DRONE_NOT_READY")
    rej(rules.drone_not_ready("D-02", "docked", 49, 50, True), "DRONE_NOT_READY")
    rej(rules.drone_not_ready("D-09", "docked", 99, 50, False), "DRONE_NOT_READY")
    rej(
        rules.drone_not_ready("D-09", "docked", 99, 50, True, out_of_service=True),
        "DRONE_NOT_READY",
    )
    assert rules.drone_not_ready("D-01", "docked", 50, 50, True) is OK


def test_no_fly_zone_target_route_and_vip():
    rej(rules.no_fly_zone("D-12", [5.3, 3.0], [5.8, 3.2], [RED]), "NO_FLY_ZONE")
    assert rules.no_fly_zone("D-08", [5.25, 2.0], [5.25, 4.5], [RED]) is OK  # skirts the fence
    # crossing (both ends outside) is still a crossing
    rej(rules.no_fly_zone("D-08", [5.5, 3.0], [6.0, 4.5], [RED]), "NO_FLY_ZONE")
    rej(rules.no_fly_zone("D-01", [0.75, 5.0], [2.0, 5.0], [RED, VIP]), "NO_FLY_ZONE")
    assert rules.no_fly_zone("D-04", [5.25, 5.0], [5.45, 4.45], [RED]) is OK
    assert rules.no_fly_zone("D-01", [0.75, 5.0], [2.0, 5.0], [RED]) is OK  # VIP inactive


def test_weather_grounded():
    rej(rules.weather_grounded("D-01", ["NW"], {"NW", "NE"}), "WEATHER_GROUNDED")
    assert rules.weather_grounded("D-13", ["NE"], {"SW"}) is OK


def test_dock_full_and_offline():
    rej(rules.dock_available("P9", False, 1), "DOCK_OFFLINE")
    rej(rules.dock_available("P10", True, 0), "DOCK_FULL")
    assert rules.dock_available("HUB-B", True, 3) is OK


def test_pilot_saturated():
    four = ["D-04", "D-08", "D-12", "D-14"]
    rej(rules.pilot_saturated("kazum", four, 4, "D-03"), "PILOT_SATURATED")
    assert rules.pilot_saturated("kazum", four, 4, "D-04") is OK  # already his
    assert rules.pilot_saturated("DP-2", ["D-10"], 4, "D-03") is OK


def test_separation_only_with_explicit_band():
    others = [("D-05", [1.0, 1.0], "A")]
    rej(rules.separation("D-01", [1.05, 1.0], "A", others, 0.1), "SEPARATION")
    assert rules.separation("D-01", [1.05, 1.0], "B", others, 0.1) is OK
    assert rules.separation("D-01", [1.05, 1.0], None, others, 0.1) is OK


def test_out_of_range():
    rej(rules.out_of_range("D-01", [0.0, 0.0], [8.0, 8.0], 10), "OUT_OF_RANGE")
    assert rules.out_of_range("D-01", [0.0, 0.0], [6.0, 6.0], 10) is OK


def test_auth_required():
    rej(rules.auth_required("track", False, "INC-2"), "AUTH_REQUIRED")
    assert rules.auth_required("track", True, "INC-2") is OK


def test_privacy_policy():
    rej(rules.privacy_face_id("who is that"), "PRIVACY_POLICY")
    rej(rules.privacy_recording("D-01", True, "NR-HOSPITAL"), "PRIVACY_POLICY")
    rej(rules.privacy_recording("D-01", False, None), "PRIVACY_POLICY")
    rej(rules.privacy_track_needs_incident(None), "PRIVACY_POLICY")
    assert rules.privacy_recording("D-01", True, None) is OK
    assert rules.privacy_track_needs_incident("INC-2") is OK


def test_mission_expired():
    rej(rules.mission_expired("INC-2", "closed"), "MISSION_EXPIRED")
    rej(rules.mission_expired("INC-2", "open", auth_expired=True), "MISSION_EXPIRED")
    assert rules.mission_expired("INC-2", "open") is OK


def test_access_codes():
    rej(rules.forbidden_role("fleet", "launch"), "FORBIDDEN_ROLE")
    rej(rules.not_found("drone", "D-99"), "NOT_FOUND")


def test_segment_rect_geometry():
    r = RED["rect"]
    assert rules.segment_hits_rect([5.0, 3.0], [5.7, 3.0], r)
    assert not rules.segment_hits_rect([5.0, 3.0], [5.5, 3.0], r)
    assert rules.segment_hits_rect([5.8, 3.0], [5.8, 3.0], r)  # point inside
