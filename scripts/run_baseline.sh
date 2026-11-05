#!/usr/bin/env bash
# Usage: scripts/run_baseline.sh [SCENARIO]
# Runs the managed golden run and the baseline (patrol cars + one van drone team, no network),
# prints the scorecard (managed vs baseline) plus the counted / info rows.
# Exit 1 unless managed wins every counted line (and the managed run has 0 violations).
set -euo pipefail
cd "$(dirname "$0")/.."
SCENARIO="${1:-FRIDAY-RUSH}"
export PYTHONIOENCODING=utf-8
exec uv run python -m sim.baseline "$SCENARIO"
