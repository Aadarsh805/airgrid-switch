#!/usr/bin/env bash
# Usage: scripts/run_golden.sh [SCENARIO] [--out DIR]
# Runs the scenario's golden actions headlessly, writes runs/golden-<ts>.txt + .jsonl,
# diffs FRIDAY-RUSH against docs/golden-timeline.txt: the event lines AND the scorecard block
# ("=== Scorecard" to the end, exact text). Exit 1 on any diff or violation.
set -euo pipefail
cd "$(dirname "$0")/.."
SCENARIO="${1:-FRIDAY-RUSH}"
shift || true
export PYTHONIOENCODING=utf-8
exec uv run python -m sim.golden "$SCENARIO" "$@"
