# Phase 06 interface (agent-author, integration-engineer, test-engineer)

Sources: prompts/06-agents-headless.md · 05-AGENTS · 04-CONTRACTS §4.1–4.4 · 09-DEMO-SCRIPT · 10-TESTING-EVALS Layer 5 · DECISIONS L11, L41–L44.

## Files and owners
| Owner | Writes |
|---|---|
| agent-author | `agents/<name>/CLAUDE.md`, `agents/<name>/.env.example` for dispatch, fleet, airspace, intel, relay, privacy, comms |
| integration-engineer | `scripts/install_agents.sh`, `agents/bin/airgrid` wrapper, isolation test in `tests/integration/unit/` |
| test-engineer | `agents/harness/run_agent.py`, `agents/harness/formats.py`, `tests/agents/`, `scripts/headless_run.py` |

All seven use Claude Code (cut item 1, L44). Fleet, Airspace and Relay get `CLAUDE.md`, not `AGENTS.md`.

## Isolation (L11)
- Install root: `AIRGRID_AGENTS_HOME`, default `~/airgrid-agents`. Each `<name>/` holds only `CLAUDE.md` and `.env` (`AIRGRID_API_URL`, `AIRGRID_ROLE`, `AIRGRID_TOKEN`), plus a shared `_bin/`.
- `_bin/` holds a standalone copy of `cli/airgrid.py` and a wrapper running it with `uv run --no-project --with typer --with httpx`. No `sim/`, `world/`, `scenarios/`, `tools/` or `docs/` is copied, and the wrapper must work with the repo not importable.
- The token comes from `config/tokens.local.yaml` and only lands in `.env`.

## Harness
`run_agent(agent, room_transcript, at, scenario="FRIDAY-RUSH", setup=None) -> Result(reply, cli_calls, exit_codes, cost)`
- Starts a sim on a free port with temp tokens, loads the scenario headless, skips to `at`, applies `setup`.
- Installs to a temp `AIRGRID_AGENTS_HOME`, runs `claude -p` from that agent's dir, with `_bin` first on PATH and the transcript as the prompt, tools limited to Bash(airgrid:*).
- Model from `AIRGRID_AGENT_MODEL`, default `claude-sonnet-5-5`; `--max-budget-usd` capped per run if the flag exists.
- The wrapper logs each `airgrid` call and its output to a file the harness reads.

## Checks per case
Format regex (one per §4.4 line in `formats.py`, also tested on hand-written samples with no LLM), only role-allowed CLI calls, correct code quoted, number provenance (every number in the reply appears in that turn's CLI output or the transcript).

## Cases
The 10 in 05-AGENTS, marked `agents`. Gate: 3 consecutive passes. To limit cost, develop on one run per case; run the 3× gate once at the end.

## Headless shift
`scripts/headless_run.py`: FRIDAY-RUSH through FakeRoom. Real agents answer only owned, agent-relevant beats (at most ~15 turns). Golden conductor plays kazum. Writes `runs/headless-<ts>.md` + scorecard.
