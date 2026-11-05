"""Layer 3 · golden FRIDAY-RUSH reproduces docs/golden-timeline.txt; QUIET-SHIFT; determinism."""

import re

import pytest

from tests.sim.helpers import event_lines, golden_expected, load_yaml, read_text, tmin

PCT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
GOLDEN_REFUSALS = [
    "MISSION_EXPIRED",
    "NO_FLY_ZONE",
    "PILOT_SATURATED",
    "PRIVACY_POLICY",
    "WEATHER_GROUNDED",
]


@pytest.fixture(scope="module")
def golden():
    from sim import golden as g

    return g


@pytest.fixture(scope="module")
def run(golden):
    return golden.run(name="FRIDAY-RUSH")


@pytest.fixture(scope="module")
def expected_managed():
    return load_yaml("scenarios/friday_rush.yaml")["expected"]["managed"]


# ---------- timeline ----------


def test_diff_vs_golden_timeline_is_empty(golden, run):
    problems = golden.diff(run.lines, read_text("docs/golden-timeline.txt"), t_tol=0.1, bat_tol=1.0)
    assert problems == [], "golden diff:\n" + "\n".join(problems[:40])


def test_timeline_independent_check(run):
    """Independent of sim.golden.diff: same lines in order, ±0.1 min, ±1% batteries, text exact."""
    actual = event_lines(run.lines)
    expected = golden_expected()
    assert len(expected) == 99  # sanity: the golden file parsed
    assert len(actual) == len(expected), f"{len(actual)} event lines vs {len(expected)} expected"
    for i, ((ta, ma), (te, me)) in enumerate(zip(actual, expected, strict=True)):
        assert abs(ta - te) <= 0.1 + 1e-6, f"line {i}: time {ta:.2f} vs {te:.2f}: {me}"
        assert PCT_RE.sub("%", ma) == PCT_RE.sub("%", me), f"line {i}: {ma!r} != {me!r}"
        for pa, pe in zip(PCT_RE.findall(ma), PCT_RE.findall(me), strict=True):
            assert abs(float(pa) - float(pe)) <= 1.0 + 1e-6, f"line {i}: {pa}% vs {pe}%: {me}"


def test_no_violations(run):
    assert list(run.violations) == []


# ---------- KPIs vs scenario expected.managed ----------


def test_kpi_eyes_on(run, expected_managed):
    assert run.kpi["eyes_on_inc1_s"] == expected_managed["eyes_on_inc1_s"] == 114


def test_kpi_coverage(run, expected_managed):
    assert round(run.kpi["coverage_pct"], 1) == expected_managed["coverage_pct"] == 93.9


def test_kpi_coverage_by_loop(run):
    by_loop = run.kpi["coverage_by_loop_min"]
    for loop, minutes in {"NORTH": 149, "CENTRAL": 179, "SOUTH": 180}.items():
        assert abs(by_loop[loop] - minutes) <= 0.5, f"{loop}: {by_loop[loop]} vs {minutes}"


def test_kpi_flights_and_drones(run, expected_managed):
    assert run.kpi["flights"] == expected_managed["flights"] == 27
    assert run.kpi["drones_flown"] == 12


def test_kpi_lowest_landing(run, expected_managed):
    assert round(run.kpi["lowest_landing_pct"]) == expected_managed["lowest_landing_pct"] == 42
    assert run.kpi["lowest_landing_pct"] >= 25


def test_kpi_ready_floor(run, expected_managed):
    assert run.kpi["ready_floor_min"] == expected_managed["ready_floor_min"] == 4
    at = run.kpi["ready_floor_at"]
    at_min = tmin(at) if isinstance(at, str) else float(at)
    assert abs(at_min - tmin("17:56")) <= 0.1 + 1e-6, f"ready floor at {at}"


def test_kpi_zero_breaches(run, expected_managed):
    assert run.kpi["reserve_breaches"] == expected_managed["reserve_breaches"] == 0
    assert run.kpi["red_zone_entries"] == expected_managed["red_zone_entries"] == 0


def test_kpi_released_early(run):
    assert run.kpi["released_early"] == 1


def test_kpi_refusals(run):
    assert list(run.kpi["refusals"]) == GOLDEN_REFUSALS


def test_refusals_match_timeline_lines(run):
    """Each of the 5 refusal codes has its line in the run's timeline, at the golden minute."""
    lines = event_lines(run.lines)
    want = {
        "PRIVACY_POLICY": "17:52",
        "NO_FLY_ZONE": "17:55",
        "PILOT_SATURATED": "18:04",
        "MISSION_EXPIRED": "18:08",
        "WEATHER_GROUNDED": "18:20",
    }
    refusal_lines = [(t, m) for t, m in lines if m.startswith(("⛔", "❌", "🌬"))]
    for code, at in want.items():
        hits = [t for t, m in refusal_lines if code in m]
        assert hits, f"no refusal line for {code}"
        assert abs(min(hits) - tmin(at)) <= 0.1 + 1e-6, f"{code} first at {min(hits)}"
    codes = set()
    for _, m in refusal_lines:
        codes |= {c for c in GOLDEN_REFUSALS if c in m}
    assert sorted(codes) == GOLDEN_REFUSALS


@pytest.mark.parametrize(
    "at,pattern",
    [
        ("17:48:06", r"🤝 HANDOFF D-13 → D-12 \(gap 0 s\)"),
        ("18:01", r"🎯 D-04 reacquires suspect"),
        ("18:07", r"🚓 INC-2 closed: suspect detained"),
    ],
    ids=["handoff", "reacquire", "detained"],
)
def test_key_moments_present(run, expected_managed, at, pattern):
    hits = [t for t, m in event_lines(run.lines) if re.search(pattern, m)]
    assert len(hits) == 1, f"{pattern}: {len(hits)} lines"
    assert abs(hits[0] - tmin(at)) <= 0.1 + 1e-6
    # the scenario file names the same moments
    blob = " ".join(str(expected_managed[k]) for k in ("handoff", "reacquire", "detained"))
    assert at in blob


# ---------- QUIET-SHIFT ----------


@pytest.fixture(scope="module")
def quiet(golden):
    return golden.run(name="QUIET-SHIFT")


def test_quiet_shift_no_violations(quiet):
    assert list(quiet.violations) == []


def test_quiet_shift_coverage(quiet):
    exp = load_yaml("scenarios/quiet_shift.yaml")["expected"]["managed"]
    assert quiet.kpi["coverage_pct"] >= exp["coverage_pct_min"] == 95
    assert quiet.kpi["reserve_breaches"] == exp["reserve_breaches"] == 0


# ---------- determinism ----------


def test_determinism_same_hash(golden, run):
    again = golden.run(name="FRIDAY-RUSH")
    assert again.event_hash == run.event_hash
    assert list(again.lines) == list(run.lines)
    assert again.kpi == run.kpi


def test_different_seed_same_physics(golden, run):
    other = golden.run(name="FRIDAY-RUSH", seed="NOT-DRONE-01")
    assert list(other.lines) == list(run.lines)
    assert other.kpi == run.kpi
    assert list(other.violations) == []
