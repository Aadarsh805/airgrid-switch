# 00 · Human setup (kazum, before the master prompt)

Settled: code before Nov 4 is allowed; solo by default, team later via config.

## Today (Oct 29–30)
1. Install Claude Code, `uv`, `jq`, Git, `gh`.
2. Create a GitHub repo `airgrid`, clone it.
3. Unzip this kit into the repo root. Commit "Add AirGrid build kit v1".
4. Check the data:
   `uv run --with pyyaml python tools/validate_world.py` → "125 checks, 0 errors"
   `uv run --with pyyaml python tools/fleet_check.py --quiet` → "OK — 0 violations"
5. Add the Switch docs MCP: `claude mcp add switch-docs --transport http https://docs.switchagents.ai/mcp`
6. Open the repo in Claude Code and type:
   > Read CONTEXT-HANDOFF.md, CLAUDE.md and docs/DECISIONS.md. Summarise in 5 lines where we are, then run prompts/MASTER-PROMPT.md.

## Oct 30–31 (while Claude builds the sim)
- Discord server + 8 rooms + `#test-room`, pins = Rules text (`docs/12-RUNBOOK.md`).
- Docker Desktop + Switch Console, Discord bridge, hello-agent in `#test-room`, screenshot.
- Decisions/Tasks probe → write the result in `docs/DECISIONS.md` L14.

## Oct 31: second Claude Code session for the dashboard
> Read CLAUDE.md and docs/06-UI-UX.md. Run /phase 07 (dashboard lane). Work only in dashboard/.

## Optional
Codex CLI + `codex mcp add switch-docs --url https://docs.switchagents.ai/mcp` (cut item 1 if it costs time).
