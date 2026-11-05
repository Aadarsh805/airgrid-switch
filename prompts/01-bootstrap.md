# Phase 01 · Bootstrap (Oct 30, ~1 h)
Read: `CLAUDE.md` · `docs/02-ARCHITECTURE.md` (repo layout) · `docs/11-TOOLS-STACK.md`.
Subagents: `sim-engineer`, `test-engineer`.

Build:
- `pyproject.toml` (uv; deps from 11-TOOLS-STACK; `airgrid` console script; pytest markers `agents`), ruff config.
- Empty package skeleton exactly as the repo layout; `sim/app.py` with `/health`; `cli/airgrid.py` with `status` stub.
- `tests/test_world_data.py` wraps `tools/validate_world.py` and `tools/fleet_check.py --quiet`.
- `docs/PROGRESS.md` (phase table, gates), `HUMAN_TODO.md`.
- `scripts/` stubs: `run_golden.sh`, `run_baseline.sh`, `run_demo.sh`, `reset.sh`.

Gate: `uv sync && uv run pytest -q` green; `uv run uvicorn sim.app:app --port 8740` answers `/health`; ruff clean. Commit.
