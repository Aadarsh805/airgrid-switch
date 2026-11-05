"""The FakeRoom end-to-end run (scripts/demo_run.py) in its own process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("dryrun")
    p = subprocess.run(
        [sys.executable, "scripts/demo_run.py", "--quiet", "--out", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    reports = list(out.glob("demo-dryrun-*.md"))
    return p, reports[0].read_text(encoding="utf-8") if reports else ""


def test_exit_zero_and_golden_hash(dry_run):
    p, _ = dry_run
    assert p.returncode == 0, p.stdout + p.stderr
    assert "RESULT: golden outcome reproduced" in p.stdout
    assert "event_hash: 0d7f86b7cb8e6582 (golden 0d7f86b7cb8e6582)" in p.stdout


def test_report_sections(dry_run):
    _, md = dry_run
    for head in (
        "## Summary",
        "## Approvals and decisions",
        "## Refusals",
        "## Simulated lines",
        "## Final KPIs vs baseline",
        "## Differences from docs/golden-timeline.txt",
        "## Event timeline",
    ):
        assert head in md


def test_report_has_each_refusal_and_label(dry_run):
    _, md = dry_run
    for code in ("PRIVACY_POLICY", "NO_FLY_ZONE", "MISSION_EXPIRED", "PILOT_SATURATED"):
        assert f"**{code}**" in md
    assert "(duty pilot, simulated)" in md and "(simulated):" in md
    assert "visitor42" in md  # an unmapped author stopped by the bot


def test_no_token_in_the_report(dry_run):
    p, md = dry_run
    assert "Bearer" not in md + p.stdout
