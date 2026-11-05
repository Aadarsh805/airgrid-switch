"""Phase 04 · scorecard (managed vs baseline), run_baseline.sh, /kpi, QUIET-SHIFT golden."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.sim.helpers import ROOT
from tests.sim.p3 import load, rows, skip
from tests.sim.p4 import QUIET, get_ok, golden_scorecard_block, minutes, now

# L37k counted lines (managed must win each). Reserve breaches and red-zone entries are safety
# checks (must be ok), and the first-unit row is info.
COUNTED = 5
SAFETY = ("reserve_breaches", "red_zone_entries")


@pytest.fixture(scope="module")
def golden():
    from sim import golden as g

    return g


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def _scorecard_lines(scorecard) -> list[str]:
    out = [ln.rstrip() for ln in scorecard]
    if out and out[0].startswith("=== Scorecard"):
        out = out[1:]  # header wording is not compared
    while out and not out[-1].strip():
        out.pop()
    return out


# ---------- sim.golden.run().scorecard == checker block ----------


def test_scorecard_equals_checker_block(golden):
    r = golden.run(name="FRIDAY-RUSH")
    got = _scorecard_lines(r.scorecard)
    want = golden_scorecard_block()
    assert len(got) == len(want), "scorecard lines:\n" + "\n".join(got)
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        assert g == w, f"scorecard line {i + 1}:\n  sim     {g!r}\n  checker {w!r}"
    assert list(r.violations) == []


def test_golden_zero_safety_rtbs(golden):
    """L36: a safety RTB in the golden run is a plan failure (counted apart from violations)."""
    r = golden.run(name="FRIDAY-RUSH")
    assert r.kpi["safety_rtbs"] == 0, r.kpi.get("safety_rtbs")
    assert list(r.violations) == []


# ---------- scripts/run_baseline.sh ----------


def _bash() -> str:
    """Git Bash, not the WSL launcher in System32."""
    cands = [shutil.which("bash")]
    for base in (os.environ.get("ProgramFiles"), r"C:\Program Files"):
        if base:
            cands.insert(0, str(Path(base) / "Git" / "bin" / "bash.exe"))
    for cand in cands:
        if cand and Path(cand).exists() and "system32" not in cand.lower():
            return cand
    pytest.fail(f"no Git Bash found (tried {cands})")


@pytest.fixture(scope="module")
def baseline_script():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [_bash(), "scripts/run_baseline.sh", "FRIDAY-RUSH"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=600,
    )
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def test_run_baseline_exits_0(baseline_script):
    rc, out, err = baseline_script
    assert rc == 0, f"run_baseline.sh exit {rc}\nstdout:\n{out[-3000:]}\nstderr:\n{err[-3000:]}"


def test_run_baseline_prints_managed_vs_baseline_scorecard(baseline_script):
    _, out, err = baseline_script
    printed = {ln.rstrip() for ln in out.splitlines()}
    for line in golden_scorecard_block():
        assert line in printed, f"missing scorecard line {line!r}\nstdout:\n{out[-3000:]}{err}"
    # the counted head-to-head lines, managed numbers first
    assert "eyes-on accident: 114 s  vs baseline van 17.0 min" in printed
    assert any("suspect LOST" in ln and "detained 18:07" in ln for ln in printed)
    assert "ground units released early: 1 vs 0" in printed
    assert any("93.9%  vs baseline ~9%" in ln for ln in printed)
    assert "managed wins 5/5 counted lines · 0 violations" in printed
    assert "[ok  ] reserve breaches: managed 0  vs baseline n/a" in printed
    assert "[ok  ] red-zone entries: managed 0  vs baseline n/a" in printed


# ---------- /kpi ----------


def _walk_keys(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (*path, str(k)), v
            yield from _walk_keys(v, (*path, str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, (*path, str(i)))


def _first_unit_paths(obj):
    return [
        p
        for p, _ in _walk_keys(obj)
        if "first" in p[-1].lower() and ("unit" in p[-1].lower() or "ground" in p[-1].lower())
    ]


def test_kpi_baseline_null_before_baseline_run(c):
    load(c)
    k = get_ok(c, "/kpi")
    assert "managed" in k and isinstance(k["managed"], dict), k
    assert k.get("baseline") is None, k


@pytest.fixture(scope="module")
def kpi_after_baseline(c):
    load(c)
    skip(c, "20:00")
    r = c.post("/baseline/run", json={})
    assert r.status_code == 200, r.text
    return get_ok(c, "/kpi")


def test_kpi_has_managed_baseline_wins(kpi_after_baseline):
    k = kpi_after_baseline
    assert isinstance(k.get("managed"), dict) and k["managed"], k
    assert isinstance(k.get("baseline"), dict) and k["baseline"], k
    assert isinstance(k.get("wins"), dict), k


def test_kpi_managed_wins_every_counted_line(kpi_after_baseline):
    wins = kpi_after_baseline["wins"]
    assert len(wins) == COUNTED, f"want {COUNTED} counted lines, got {sorted(wins)}"
    assert all(v is True for v in wins.values()), wins
    assert not _first_unit_paths(wins), f"first-unit row must not be counted: {wins}"
    assert not set(SAFETY) & set(wins), f"safety checks are not wins: {wins}"
    assert kpi_after_baseline["safety"] == dict.fromkeys(SAFETY, "ok")


def test_kpi_first_ground_unit_is_info_row(kpi_after_baseline):
    k = kpi_after_baseline
    found = _first_unit_paths({"managed": k["managed"], "baseline": k["baseline"], **k})
    assert found, f"no first-ground-unit row in /kpi: {sorted(k)}"
    assert not any(p[0] == "wins" for p in found), found


def test_kpi_baseline_numbers(kpi_after_baseline):
    """Baseline holds summary numbers only (no timeline or internals) and the known values."""
    b = kpi_after_baseline["baseline"]
    banned = {"timeline", "lines", "events", "log", "frames", "van_path", "flights_log"}
    for p, v in _walk_keys(b):
        assert p[-1] not in banned, f"baseline internals served: {'.'.join(p)}"
        assert not (isinstance(v, list) and len(v) > 10), f"baseline list {'.'.join(p)}"
    assert any(v == 17.0 for _, v in _walk_keys(b)), f"baseline eyes-on 17.0 min missing: {b}"
    assert any(v == 0 for p, v in _walk_keys(b) if "releas" in p[-1].lower()), b
    m = kpi_after_baseline["managed"]
    assert any(v == 114 for _, v in _walk_keys(m)), m
    assert any(v == 93.9 for _, v in _walk_keys(m)), m


def test_kpi_history_past_only(c):
    load(c)
    skip(c, "17:30")
    t = now(c)
    hist = rows(get_ok(c, "/kpi/history"), "history")
    assert hist, "no KPI history at 17:30"
    for h in hist:
        when = h.get("t", h.get("clock"))
        assert when is not None, h
        assert minutes(when) <= t + 1e-6, f"history row from the future: {h}"


# ---------- QUIET-SHIFT golden ----------


@pytest.fixture(scope="module")
def quiet(golden):
    return golden.run(name="QUIET-SHIFT")


def test_quiet_shift_zero_violations(quiet):
    assert list(quiet.violations) == []


def test_quiet_shift_coverage(quiet):
    assert quiet.kpi["coverage_pct"] >= QUIET["expected"]["managed"]["coverage_pct_min"] == 95


def test_quiet_shift_fleet_narration_count(quiet):
    """🔄 rotations + 🔋 status every 30 min, from the run's events, ≤ agent_messages_max.fleet."""
    evs = quiet.events
    rotations = [e for e in evs if e["type"] == "rotation" or str(e["text"]).startswith("🔄")]
    battery = [e for e in evs if str(e["text"]).startswith("🔋")]
    if battery:
        n_battery = len(battery)
    else:  # Fleet's cadence: one 🔋 post every 30 sim-min, start and end inclusive
        n_battery = len(range(int(minutes(QUIET["start"])), int(minutes(QUIET["end"])) + 1, 30))
    limit = QUIET["expected"]["agent_messages_max"]["fleet"]
    total = len(rotations) + n_battery
    assert total <= limit, (  # limit from scenarios/quiet_shift.yaml (L36)
        f"{len(rotations)} rotations + {n_battery} 🔋 = {total} > {limit}: "
        + "; ".join(e["text"] for e in rotations)
    )
