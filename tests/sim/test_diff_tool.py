"""Layer 3 · sim.golden.diff / parse_timeline on hand-made strings (tolerances: 0.1 min, 1%)."""

import pytest

from tests.sim.helpers import golden_expected, read_text


@pytest.fixture(scope="module")
def golden():
    from sim import golden as g

    return g


BASE = [
    "17:08:06  🔄 D-03 launches for NORTH (relief for D-02 at 48%)",
    "17:09:30  ↩ D-02 RTB relieved by D-03 on NORTH → P2 (battery 46%)",
    "17:10:24  🛬 D-02 lands at P2 with 44%",
    "   17:12  🚨 INC-1 accident at ring_road_j9",
    "   17:35  🅿 D-09 nearest dock P9 unavailable → DOCK_OFFLINE, rerouting",
    "   17:35  🅿 D-09 nearest dock P10 unavailable → DOCK_FULL, rerouting",
    "   17:35  ↩ D-09 RTB accident cleared → HUB-B (battery 66%)",
]
EXPECTED = "\n".join(BASE) + "\n"


def _with(i, line):
    out = list(BASE)
    out[i] = line
    return out


def test_identical_passes(golden):
    assert golden.diff(BASE, EXPECTED) == []


def test_default_tolerances_are_contract(golden):
    assert golden.diff(BASE, EXPECTED, t_tol=0.1, bat_tol=1.0) == []


def test_time_off_by_0_2_min_fails(golden):
    # 17:10:24 -> 17:10:36 is +12 s = 0.2 min
    actual = _with(2, "17:10:36  🛬 D-02 lands at P2 with 44%")
    assert golden.diff(actual, EXPECTED) != []


def test_time_early_by_0_2_min_fails(golden):
    actual = _with(3, "17:11:48  🚨 INC-1 accident at ring_road_j9")
    assert golden.diff(actual, EXPECTED) != []


def test_time_off_by_0_1_min_passes(golden):
    # +6 s = exactly 0.1 min (float edge: 10.1 - 10.0 > 0.1 in IEEE; must still pass)
    actual = _with(2, "17:10:30  🛬 D-02 lands at P2 with 44%")
    assert golden.diff(actual, EXPECTED) == []
    actual = _with(3, "17:12:06  🚨 INC-1 accident at ring_road_j9")
    assert golden.diff(actual, EXPECTED) == []
    actual = _with(3, "17:11:54  🚨 INC-1 accident at ring_road_j9")
    assert golden.diff(actual, EXPECTED) == []


def test_battery_off_by_2_fails(golden):
    actual = _with(2, "17:10:24  🛬 D-02 lands at P2 with 46%")
    assert golden.diff(actual, EXPECTED) != []
    actual = _with(1, "17:09:30  ↩ D-02 RTB relieved by D-03 on NORTH → P2 (battery 44%)")
    assert golden.diff(actual, EXPECTED) != []


def test_battery_off_by_1_passes(golden):
    actual = _with(2, "17:10:24  🛬 D-02 lands at P2 with 45%")
    assert golden.diff(actual, EXPECTED) == []
    actual = _with(0, "17:08:06  🔄 D-03 launches for NORTH (relief for D-02 at 47%)")
    assert golden.diff(actual, EXPECTED) == []


def test_missing_line_fails(golden):
    actual = BASE[:4] + BASE[5:]
    assert golden.diff(actual, EXPECTED) != []


def test_missing_last_line_fails(golden):
    assert golden.diff(BASE[:-1], EXPECTED) != []


def test_extra_line_fails(golden):
    actual = (
        BASE[:3] + ["17:11:00  🔄 D-01 launches for NORTH (backfill — loop uncovered)"] + BASE[3:]
    )
    assert golden.diff(actual, EXPECTED) != []


def test_extra_trailing_line_fails(golden):
    assert golden.diff(BASE + ["   17:36  🛬 D-09 lands at HUB-B with 62%"], EXPECTED) != []


def test_reordered_lines_fail(golden):
    actual = [BASE[1], BASE[0]] + BASE[2:]
    assert golden.diff(actual, EXPECTED) != []


def test_reordered_same_minute_lines_fail(golden):
    # P9 / P10 rejections at 17:35 swapped: order is part of the contract
    actual = BASE[:4] + [BASE[5], BASE[4]] + BASE[6:]
    assert golden.diff(actual, EXPECTED) != []


def test_changed_text_fails(golden):
    actual = _with(6, "   17:35  ↩ D-09 RTB accident cleared → HUB-A (battery 66%)")
    assert golden.diff(actual, EXPECTED) != []
    actual = _with(4, "   17:35  🅿 D-09 nearest dock P9 unavailable → DOCK_FULL, rerouting")
    assert golden.diff(actual, EXPECTED) != []


def test_changed_drone_fails(golden):
    actual = _with(2, "17:10:24  🛬 D-03 lands at P2 with 44%")
    assert golden.diff(actual, EXPECTED) != []


def test_formatting_ignored(golden):
    """Padding, trailing whitespace and CRLF are formatting, not content."""
    actual = [ln.strip() + "  " for ln in BASE]
    assert golden.diff(actual, EXPECTED) == []
    assert golden.diff(BASE, EXPECTED.replace("\n", "\r\n")) == []


def test_header_and_scorecard_ignored(golden):
    text = (
        "=== FRIDAY RUSH — golden managed run (computed) ===\n"
        + EXPECTED
        + "\n=== Scorecard (managed vs baseline) ===\n"
        + "eyes-on accident: 114 s  vs baseline van 17.0 min\n"
        + "ready floor: minimum 4 READY drones (at 17:56)\n"
        + "\nOK — 0 violations\n"
    )
    assert golden.diff(BASE, text) == []
    assert golden.diff(BASE[:-1], text) != []


def test_diff_messages_are_strings(golden):
    out = golden.diff(BASE[:-1], EXPECTED)
    assert out and all(isinstance(x, str) and x for x in out)


def test_parse_timeline_minutes_and_messages(golden):
    parsed = golden.parse_timeline(EXPECTED)
    assert len(parsed) == len(BASE)
    t0, m0 = parsed[0]
    assert abs(t0 - (17 * 60 + 8.1)) < 1e-6
    assert m0 == "🔄 D-03 launches for NORTH (relief for D-02 at 48%)"
    assert abs(parsed[3][0] - 17 * 60 - 12) < 1e-6
    assert parsed[3][1] == "🚨 INC-1 accident at ring_road_j9"


def test_parse_timeline_on_golden_file(golden):
    parsed = golden.parse_timeline(read_text("docs/golden-timeline.txt"))
    ours = golden_expected()
    assert len(parsed) == len(ours)
    for (ta, ma), (tb, mb) in zip(parsed, ours, strict=True):
        assert abs(ta - tb) < 1e-6 and ma.strip() == mb.strip()


def test_golden_file_matches_itself(golden):
    text = read_text("docs/golden-timeline.txt")
    lines = [ln for ln in text.splitlines() if ln[:8].strip()[:2].isdigit() and ":" in ln[:8]]
    assert len(lines) == len(golden_expected())
    assert golden.diff(lines, text) == []
