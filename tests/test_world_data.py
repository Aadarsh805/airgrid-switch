"""Layer 1: world data and the reference model (docs/10-TESTING-EVALS.md)."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8", **os.environ},
    )


def test_validate_world():
    r = _run("tools/validate_world.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 errors" in r.stdout


def test_fleet_check_reference_model():
    r = _run("tools/fleet_check.py", "--quiet")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 violations" in r.stdout
