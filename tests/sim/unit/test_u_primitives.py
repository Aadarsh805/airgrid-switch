"""Unit tests for sim primitives: geo, docks, pilots, fleet, airspace, weather (sim-engineer)."""

import pytest

from sim.airspace import Airspace
from sim.docks import Dock, book_slot
from sim.fleet import Drone, charge, drain, move
from sim.geo import fmt, sector_of, tmin
from sim.pilots import Pilots
from sim.weather import Weather
from sim.world import load_world

W = load_world()
R = W.policies["slot_log_radius_km"]


def test_time_format_roundtrip():
    assert tmin("17:12.5") == pytest.approx(1032.5)
    assert fmt(1032.5) == "17:12:30"
    assert fmt(1032.0) == "17:12"
    assert fmt(tmin("18:08:42")) == "18:08:42"


def test_sector_split_matches_checker():
    assert sector_of([3.0, 3.0]) == "SE"  # y > 3 is north; x >= 3 is east
    assert sector_of([2.99, 3.01]) == "NW"


def _docks():
    return {k: Dock(k, v) for k, v in W.raw["docks"].items()}


def test_book_slot_logs_offline_and_full_then_reroutes():
    docks = _docks()
    docks["P9"].online = False
    docks["P10"].occupants.append("D-10")
    slot, skipped = book_slot(docks, "D-09", [1.5, 1.0], "P9", R)
    assert skipped == [("P9", "DOCK_OFFLINE"), ("P10", "DOCK_FULL")]
    assert slot == "HUB-B" and docks["HUB-B"].reserved == ["D-09"]


def test_book_slot_home_breaks_ties_and_far_skips_not_logged():
    docks = _docks()
    slot, skipped = book_slot(docks, "D-13", [3.0, 4.0], "HUB-A", R)
    assert slot == "HUB-A" and skipped == []
    for k in docks:
        if k != "P12":
            docks[k].online = False
    slot, skipped = book_slot(docks, "D-01", [0.75, 5.0], "P1", R)
    assert slot == "P12"
    assert all(k in ("P1", "P2", "P5") for k, _ in skipped)  # only docks < 1.6 km logged


def test_offline_dock_has_no_free_slots():
    d = Dock("P9", W.raw["docks"]["P9"])
    d.online = False
    assert d.free_slots() == 0
    assert [s["status"] for s in d.slot_states()] == ["OFFLINE"]


def test_pilot_caps_no_auto_pool():
    P = Pilots.from_world(W.raw["pilots"])
    assert P.pilot_for(["DP-2"]) == "DP-2"
    P["DP-2"].drones |= {"a", "b", "c", "d"}
    assert P.pilot_for(["DP-2"]) == "DP-1"  # falls back to a simulated duty pilot
    P["DP-1"].drones |= {"e", "f", "g", "h"}
    assert P.pilot_for(["DP-2"]) is None  # nobody unlimited
    assert P.first_human_remote_pilot() == "kazum"
    P["kazum"].drones |= {"i", "j", "k", "l"}
    assert P.first_human_remote_pilot() is None


def test_move_drain_charge():
    d = Drone("D-01", {"home": "P1"})
    d.pos = [0.0, 0.0]
    assert move(d, [0.05, 0.0], 0.9) is True and d.pos == [0.05, 0.0]
    assert move(d, [1.0, 0.0], 0.9) is False and d.pos[0] == pytest.approx(0.14)
    d.bat = 50.0
    drain(d, 1.8)
    assert d.bat == pytest.approx(49.82)
    d.bat = 99.9
    charge(d, 3.0, 100)
    assert d.bat == 100


def test_red_zone_and_boundary_point():
    A = Airspace(W.raw["zones"])
    assert A.in_zone([5.75, 2.6]) == "RED-AIRFIELD"
    assert A.boundary_point([5.75, 2.6]) == [pytest.approx(5.55), 2.6]
    assert A.in_zone([0.75, 5.0], "vip") is None  # VIP-TEMP inactive until approved
    assert A.in_zone([2.1, 4.0], "no_record") == "NR-HOSPITAL"


def test_weather_grounds_and_reopens():
    w = Weather(12, W.raw["district"]["sectors"])
    w.ground(["NW", "NE"], 13, "18:50")
    assert w.grounded == {"NW", "NE"}
    assert w.expire(tmin("18:49.9")) == []
    assert w.expire(tmin("18:50")) == [["NW", "NE"]]
    assert w.grounded == set()
