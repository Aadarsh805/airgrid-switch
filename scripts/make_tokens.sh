#!/usr/bin/env bash
# Usage: scripts/make_tokens.sh [--out PATH] [--force | --missing]
# Writes config/tokens.local.yaml (gitignored) with one random token per agent role and human.
# Prints the path only, never a token. Refuses to overwrite without --force.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m sim.auth --init "$@"
