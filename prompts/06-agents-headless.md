# Phase 06 · Runtime agents, tested headlessly (Nov 4)
Read: `docs/05-AGENTS.md` · `docs/04-CONTRACTS.md` §4.1–4.4 · `docs/09-DEMO-SCRIPT.md` · `docs/10-TESTING-EVALS.md` Layer 5.
Subagents: `agent-author` (instruction files), `test-engineer` (harness + cases), `integration-engineer` (`scripts/install_agents.sh`).

Build:
- `agents/<name>/CLAUDE.md` (dispatch, intel, privacy, comms; plus fleet, airspace, relay if no Codex) or `AGENTS.md` (fleet, airspace, relay with Codex). `.env.example` each.
- `scripts/install_agents.sh` → `~/airgrid-agents/<name>/` + isolation test.
- `agents/harness/run_agent.py`: sim at a scenario time, room transcript in, run the agent headless from its install dir, capture reply + `airgrid` calls; `formats.py` regexes; number-provenance check.
- `tests/agents/` (marked `agents`): the 10 cases in 05-AGENTS.
- Full headless FRIDAY-RUSH through FakeRoom with golden kazum → `runs/headless-<ts>.md` + scorecard.

Gate:
- `uv run pytest -m agents` passes 3 consecutive runs.
- Headless run saved; managed beats baseline on every line.
- ⛔ **G2:** stop. kazum reads scorecard + transcript, signs off; numbers in `docs/09-DEMO-SCRIPT.md` updated to the measured run if they differ.
