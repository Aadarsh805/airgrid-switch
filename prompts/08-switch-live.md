# Phase 08 · Live in Switch (Nov 5)
Read: `docs/05-AGENTS.md` · `docs/12-RUNBOOK.md` · `docs/04-CONTRACTS.md` §4.4 · DECISIONS L11, L13, L14.
Subagent: `integration-engineer`. kazum needed.
Before coding: confirm with the `switch-docs` MCP the custom-client protocol, posting to a room, reading human messages, Decisions/Tasks writes, template fields, env for agent sessions. Use the L14 probe result.

Build:
- `SwitchRoom` transport; `DecisionSink` for Switch (Decisions on approve/reject/refusal; Tasks = patrol plan) or the L14 manual fallback.
- `agents/switch/*.yaml` room/agent templates with Rules text from 05-AGENTS.
- `HUMAN_TODO.md`: exact Console steps (7 agents → `~/airgrid-agents/<name>`, providers, rooms, Rules, `config/humans.yaml`).
- Touch agent instruction files only to fix live issues; re-run `pytest -m agents` after any change.

⛔ **G3** (solo; must also pass with a second Discord account mapped as field_officer to prove team mode):
- Accident beat: auto-launch, eyes-on, SITREP, loudspeaker approved from Discord, unit released.
- Chase: track approved in `#dispatch` shows on the dashboard and as a Decisions entry; handoff narrated.
- FO face-recognition push refused live; airfield route refused live.
- QUIET-SHIFT 2 sim-hours: only Fleet speaks (message counts logged).
