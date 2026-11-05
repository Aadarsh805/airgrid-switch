"""Engine-level rules: auto-launch policy, approval_needed, suspect visibility, golden helpers,
DB write-through, determinism (sim-engineer)."""

import json

import pytest

from sim.actions import decide, propose
from sim.auth import Identity
from sim.db import DB
from sim.engine import Engine
from sim.geo import tmin
from sim.golden import diff, parse_timeline, run


def demo(**kw):
    """Demo-mode engine (no golden actions) with the standing auto-launch policy approved."""
    eng = Engine.load("FRIDAY-RUSH", mode="demo", **kw)
    a = propose(eng, Identity.agent("dispatch"), "standing_policy", {}, "brief")
    assert decide(eng, Identity.human("kazum", ["watch_commander"]), a.result["id"], True).ok
    return eng


EXPECTED = (
    "=== X ===\n"
    "17:08:06  🔄 D-03 launches for NORTH (relief for D-02 at 48%)\n"
    "   17:20  🌬 wind 13 m/s > 12 in ['NW', 'NE'] → WEATHER_GROUNDED until 18:50\n"
    "\n=== Scorecard (managed vs baseline) ===\n"
    "17:30  not an event\n"
)


def test_parse_timeline_stops_at_scorecard():
    rows = parse_timeline(EXPECTED)
    assert rows == [
        (pytest.approx(tmin("17:08:06")), "🔄 D-03 launches for NORTH (relief for D-02 at 48%)"),
        (1040.0, "🌬 wind 13 m/s > 12 in ['NW', 'NE'] → WEATHER_GROUNDED until 18:50"),
    ]


def test_diff_tolerances():
    ok = [
        "17:08:00  🔄 D-03 launches for NORTH (relief for D-02 at 47%)",
        "   17:20  🌬 wind 13 m/s > 12 in ['NW', 'NE'] → WEATHER_GROUNDED until 18:50",
    ]
    assert diff(ok, EXPECTED) == []
    late = ["17:08:30  🔄 D-03 launches for NORTH (relief for D-02 at 48%)", ok[1]]
    assert len(diff(late, EXPECTED)) == 1
    bat = ["17:08:06  🔄 D-03 launches for NORTH (relief for D-02 at 46%)", ok[1]]
    assert len(diff(bat, EXPECTED)) == 1
    text = [ok[0], "   17:20  🌬 wind 14 m/s > 12 in ['NW', 'NE'] → WEATHER_GROUNDED until 18:50"]
    assert len(diff(text, EXPECTED)) == 1
    assert len(diff(ok[:1], EXPECTED)) == 1  # missing line


def test_auto_launch_policy_without_golden_actions():
    eng = demo()  # demo: no golden actions applied
    assert not eng.golden
    eng.run_until("17:12.4")
    assert eng.drones["D-09"].state == "docked"
    eng.run_until("17:12.5")
    d = eng.drones["D-09"]
    assert (d.state, d.mission, d.incident, d.pilot) == ("airborne", "incident", "INC-1", "kazum")
    launch = [e for e in eng.events if e["type"] == "launch"][-1]
    assert launch["payload"]["basis"] == "auto_policy" and launch["clock"] == "17:12:30"


def test_robbery_never_auto_launches_and_raises_approval_needed():
    eng = demo()
    eng.run_until("17:45")
    assert not [d for d in eng.drones.values() if d.incident == "INC-2"]
    need = [e for e in eng.events if e["type"] == "approval_needed"]
    assert len(need) == 1
    assert need and need[0]["payload"]["incident"] == "INC-2" and not need[0]["timeline"]
    assert all(e["type"] != "approval_needed" for e in eng.events if e["timeline"])


def test_golden_auto_policy_launch_is_an_assertion_not_a_second_launch():
    g = run("FRIDAY-RUSH")
    d09 = [ln for ln in g.lines if "🚀 D-09" in ln]
    assert d09 == ["17:12:30  🚀 D-09 → incident INC-1 (100%, pilot kazum, basis auto_policy)"]
    assert g.violations == []


def test_suspect_served_only_in_view_and_outside_red_zone():
    g = run("FRIDAY-RUSH")
    seen = [f for f in g.frames if f["suspect"] is not None]
    assert seen
    for f in seen:
        assert 17 * 60 + 41 <= f["t"] <= 18 * 60 + 8
        sx, sy = f["suspect"]
        assert not (5.6 <= sx <= 6.0 and 2.2 <= sy <= 4.2)
        trackers = [d for d in f["drones"].values() if d["mission"] == "track"]
        assert any(
            ((d["pos"][0] - sx) ** 2 + (d["pos"][1] - sy) ** 2) ** 0.5 <= 0.5 + 1e-3
            for d in trackers
        )


def test_state_view_is_json_and_never_leaks_scenario_internals():
    eng = Engine.load("FRIDAY-RUSH", mode="demo")
    eng.run_until("17:41")
    blob = json.dumps(eng.state_view()) + json.dumps(eng.events)
    for bad in (
        "suspect_path",
        "golden_actions",
        "expected",
        "detained",
        "18:20",
        "dock_restored",
        "WEATHER_GROUNDED",
        "units_golden",
    ):
        assert bad not in blob, bad
    assert eng.state_view()["suspect"] is None


def test_db_write_through_in_memory():
    db = DB(":memory:")
    eng = demo(db=db)
    eng.run_until("17:13")
    assert db.count("drones") == 14 and db.count("slot_state") == 18
    assert db.count("events") == len(eng.events)
    assert db.count("approvals") == 1 and db.count("decisions_log") == 2
    assert db.count("kpi_snapshots") >= 13
    row = db.conn.execute("SELECT state, mission FROM drones WHERE id='D-09'").fetchone()
    assert row == ("airborne", "incident")


def test_determinism_same_hash_and_quiet_shift_clean():
    assert run("FRIDAY-RUSH").event_hash == run("FRIDAY-RUSH").event_hash
    q = run("QUIET-SHIFT")
    assert q.violations == [] and q.kpi["coverage_pct"] >= 95
