"""Phase 06 additions: every number an agent states comes from CLI output (L45)."""

import re

import pytest

from sim.geo import mmss, tmin
from tests.integration.harness import assert_ok, assert_rejected, run_cli
from tests.sim.p3 import approval_id, approve, at, ok, propose, skip


def get_events(c):
    return c.get("/events").json()["events"]


def fleet_one(c, drone):
    return c.get(f"/fleet/{drone}").json()["drone"]


def landing_event(c, drone):
    for e in reversed(get_events(c)):
        if e["type"] == "landing" and e["payload"]["drone"] == drone:
            return e
    raise AssertionError(f"{drone} never landed")


def test_mmss_rounds_up():
    assert mmss(6.5) == "6:30"
    assert mmss(1.01) == "1:01"
    assert mmss(0) == "0:00"


def test_unit_task_returns_eta_both_ways(sim, c):
    at(c, "17:20")
    r = run_cli(sim.url, ["unit", "task", "--unit", "patrol_car_2", "--incident", "INC-1"], "comms")
    res = assert_ok(r)["result"]
    m, s = res["eta"].split(":")
    assert int(m) * 60 + int(s) == pytest.approx(res["eta_min"] * 60, abs=1.01)


def test_rtb_projection_matches_the_real_landing(sim, c):
    at(c, "17:20")
    before = fleet_one(c, "D-09")
    assert "lands_at" not in before and "lands_pct" not in before
    res = assert_ok(run_cli(sim.url, ["rtb", "--drone", "D-09"], "fleet"))["result"]
    assert re.fullmatch(r"\d\d:\d\d(:\d\d)?", res["lands_at"]), res
    view = fleet_one(c, "D-09")
    assert (view["lands_at"], view["lands_pct"]) == (res["lands_at"], res["lands_pct"])

    skip(c, "17:40")
    e = landing_event(c, "D-09")
    assert e["t"] == pytest.approx(tmin(res["lands_at"]), abs=0.11)
    assert e["payload"]["battery"] == pytest.approx(res["lands_pct"], abs=0.2)
    assert "lands_at" not in fleet_one(c, "D-09"), "landed drones have no projection"


def test_repeat_rtb_and_forced_dock_also_project(sim, c):
    at(c, "17:20")
    run_cli(sim.url, ["rtb", "--drone", "D-09"], "fleet")
    again = assert_ok(run_cli(sim.url, ["rtb", "--drone", "D-09"], "fleet"))["result"]
    assert again["already_returning"] is True and "lands_at" in again
    forced = assert_ok(run_cli(sim.url, ["rtb", "--drone", "D-09", "--dock", "HUB-B"], "fleet"))
    assert forced["result"]["dock"] == "HUB-B" and "lands_pct" in forced["result"]


def test_sim_booked_return_is_projected_too(c):
    """Golden RTB at 17:35 lands at 17:37 with 62% (docs/golden-timeline.txt)."""
    at(c, "17:35:06")
    v = fleet_one(c, "D-09")
    assert v["booked_slot"] == "HUB-B"
    assert tmin(v["lands_at"]) == pytest.approx(tmin("17:37"), abs=1)
    assert round(v["lands_pct"]) == 62


def test_projection_is_read_only(c):
    at(c, "17:35:06")
    first = c.get("/fleet").json()
    events = len(get_events(c))
    assert c.get("/fleet").json() == first
    assert len(get_events(c)) == events


def test_set_recording_cli(sim, c):
    at(c, "17:20")
    argv = ["set-recording", "--drone", "D-09"]
    assert assert_ok(run_cli(sim.url, [*argv, "--off"], "privacy"))["result"]["recording"] is False
    assert assert_ok(run_cli(sim.url, [*argv, "--on"], "privacy"))["result"]["recording"] is True
    assert_rejected(run_cli(sim.url, [*argv, "--on"], "fleet"), "FORBIDDEN_ROLE")
    assert_rejected(
        run_cli(sim.url, ["set-recording", "--drone", "D-02", "--on"], "privacy"), "PRIVACY_POLICY"
    )
    assert run_cli(sim.url, argv, "privacy").code == 1, "--on or --off is required"


def test_release_footage_cli(sim, c):
    at(c, "17:20")
    args = {"incident": "INC-1", "clip": "C-3", "to": "traffic court"}
    aid = approval_id(propose(c, "footage_release", args))
    argv = ["release-footage", "--approval", aid]
    assert_rejected(run_cli(sim.url, argv, "privacy"), "AUTH_REQUIRED")
    ok(approve(c, aid))
    assert_rejected(run_cli(sim.url, argv, "fleet"), "FORBIDDEN_ROLE")
    assert assert_ok(run_cli(sim.url, argv, "privacy"))["result"]["released"] is True
    assert_rejected(run_cli(sim.url, argv, "privacy"), "AUTH_REQUIRED")


def test_route_check_accepts_a_dock_id():
    from sim.engine import Engine
    from sim.projections import route_check

    eng = Engine.load("FRIDAY-RUSH", mode="headless")
    eng.run_until("17:14")
    assert route_check(eng, "D-09", "HUB-B")["to"] == eng.docks["HUB-B"].at
