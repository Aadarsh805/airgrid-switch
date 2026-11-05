# AirGrid — project memory for Claude Code

## What this is
A city drone response network run from Discord. A Python simulator plays the fictional Northgate District (6 × 6 km, 14 drones, 14 docks / 18 slots, 3 patrol loops, airspace zones, weather, ground units, incidents). Seven AI agents work the desks in 8 Switch rooms; kazum (solo) is Watch Commander, Remote Pilot and Field Officer and approves. A static HTML/SVG dashboard is the ops wall. Built for Flip the Switch (Nov 4–7, 2026, Best Business Workflow), then a CV project.

It's a **drone-operations training and prototyping simulator**: fictional district, simplified rules modelled on real drone-first-responder practice, no real hardware or dispatch, no identification of people. Never claim otherwise.

**Two kinds of agents:** builder subagents in `.claude/agents/` build this repo; the seven runtime agents in `agents/` are the product and run in Discord via Switch from `~/airgrid-agents/`, never from the repo.

## Read before working
- `CONTEXT-HANDOFF.md` — how we got here, status, next steps (first, in a new session)
- `docs/DECISIONS.md` — settled decisions and fixes to Muse's pack (before any doc)
- `docs/00-PROBLEM-AND-SWITCH.md` · `01-PRD.md` (scope, cut order) · `02-ARCHITECTURE.md` · `03-SIM-SPEC.md`
- `docs/04-CONTRACTS.md` — CLI, reason codes, approvals, formats, API, schema — **change only with a DECISIONS entry**
- `docs/05-AGENTS.md` · `06-UI-UX.md` · `07-SCENARIOS.md` · `09-DEMO-SCRIPT.md` · `10-TESTING-EVALS.md` · `11-TOOLS-STACK.md` · `12-RUNBOOK.md`
- `docs/golden-timeline.txt` — the computed FRIDAY-RUSH run every number comes from

## Canonical data
`world/airgrid.yaml`, `scenarios/*.yaml`. Validated by `tools/validate_world.py`. `tools/fleet_check.py` is the reference model; the sim's golden run must reproduce `docs/golden-timeline.txt`. These win over any doc.

## Stack
Python 3.12 + uv · FastAPI + sse-starlette · SQLite (WAL) · Pydantic v2 · Typer · httpx · PyYAML · pytest · ruff · dashboard = plain HTML/JS/SVG served by FastAPI. No Streamlit, no Docker for our services, no SimPy, no Faker, no agent frameworks, no map tiles.

## Commands
```
uv sync
uv run pytest -q                              # all non-LLM tests
uv run python tools/validate_world.py         # world data
uv run python tools/fleet_check.py            # reference timeline
uv run uvicorn sim.app:app --port 8740        # simulator (+ dashboard at /ui)
uv run airgrid status                         # CLI
scripts/run_golden.sh FRIDAY-RUSH             # must match docs/golden-timeline.txt
scripts/run_baseline.sh FRIDAY-RUSH
scripts/run_demo.sh                           # sim + World Bot (FakeRoom or Switch) + dashboard
uv run pytest -m agents                       # headless runtime-agent tests (costs tokens)
```

## Non-negotiable design rules
1. The simulator is the only owner of state.
2. Every write is validated; rejections use only the codes in 04-CONTRACTS §4.2.
3. Agents propose; only humans approve, only via the World Bot with a human token.
4. Time-critical work (auto-launch, rotation, backfill, slot booking, relay, safety RTB) is sim code, never an agent.
5. No LLM inside the simulator. Duty pilots, CAD and radio lines are templates.
6. Deterministic: same scenario + seed + decisions → same numbers.
7. Recording is derived state. Face identification does not exist; `request_face_id` is always rejected and logged.
8. Future events, the suspect's future path and baseline internals never appear in any API response. Grep-tested.
9. Label simulated things as simulated in UI, rooms and README.

## How to work
- Phases `prompts/01…09`, **one phase per session**. ⛔ gates G1 (end of 02), G2 (end of 06), G3 (in 08), G4 (in 09) need kazum's sign-off in `docs/PROGRESS.md`.
- Phase 07 (dashboard) runs in a parallel session from Oct 31, folder `dashboard/` only.
- Plan in 3–5 lines before coding; builder subagents in parallel where folders don't overlap; `reviewer` at the end of each phase.
- After building, run it and look at it (tests, CLI output, golden diff, dashboard screenshots). Report what you saw.
- Ambiguity → simplest option that keeps the contracts → log in `docs/DECISIONS.md` → continue.
- Needs a human (Switch Console, Discord, keys) → exact steps in `HUMAN_TODO.md`, stop.
- Behind schedule → cut order (DECISIONS L20), no debate.

## Switch
Use the `switch-docs` MCP for every Switch detail. Don't invent Switch APIs. Unknowns go behind an interface with a fake.

## Definition of done (every phase)
Tests green · feature run and observed (output quoted) · docs/contracts updated · `docs/PROGRESS.md` updated · stop at ⛔ gates.
