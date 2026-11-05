# AirGrid build kit — start here

Unzip into the root of your `airgrid` repo. Everything below is already in place.

## What's in the box
| Path | What it is | You do |
|---|---|---|
| `CLAUDE.md` | project memory Claude Code reads every session | nothing |
| `CONTEXT-HANDOFF.md` | history + status for a fresh session | read once |
| `docs/00…12`, `DECISIONS.md`, `PARKING-LOT.md` | the full spec | skim 00, 01, 09 |
| `docs/golden-timeline.txt` | the computed Friday Rush, minute by minute | the demo numbers live here |
| `world/airgrid.yaml` | district, docks, fleet, drone model, pilots | don't hand-edit |
| `scenarios/` | friday_rush, quiet_shift, triggers | — |
| `tools/validate_world.py`, `tools/fleet_check.py` | data check + reference model | run both once |
| `config/humans.example.yaml` | Discord user → roles (solo default) | copy to `humans.yaml` on Nov 5 |
| `.claude/agents/` | 6 builder subagents | nothing |
| `.claude/commands/` | `/phase`, `/verify`, `/demo-run` | use them |
| `.claude/settings.json` | permissions + ruff hook (needs `jq`) | nothing |
| `prompts/00-HUMAN-SETUP.md` | your steps | do first |
| `prompts/MASTER-PROMPT.md` | the one prompt to paste | paste once |
| `prompts/01…09` | phase prompts, run by `/phase` | nothing |

## Quick check
```
uv run --with pyyaml python tools/validate_world.py   # 125 checks, 0 errors
uv run --with pyyaml python tools/fleet_check.py      # full timeline, ends "OK — 0 violations"
```

## Then
`prompts/00-HUMAN-SETUP.md` → open Claude Code in the repo → paste `prompts/MASTER-PROMPT.md`.
