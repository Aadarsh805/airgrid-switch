# 12 · Switch Setup and Runbook (the parts Claude Code can't do)

## Discord server (kazum, Oct 30)
- Server **AirGrid — Northgate (simulated)**, category OPS: `#dispatch`, `#fleet-hangar`, `#airspace`, `#incident-1`, `#incident-2`, `#field-units`, `#privacy-log`, `#shift-report`, plus private `#test-room`.
- Roles: Watch Commander, Remote Pilot, Field Officer (all on kazum in solo), Agent, World Bot.
- Pin in each room: purpose, who works there, Rules text from `05-AGENTS.md`.

## Switch (kazum, Oct 30–31)
1. Docker Desktop running; install Switch Console (from Switch's official releases; follow the docs MCP); start the local server.
2. Connect the Discord bridge.
3. Hello-agent: one Claude Code agent posts a one-line intro in `#test-room`. Screenshot → `docs/switch-proof.png`.
4. **Decisions/Tasks probe (L14):** in `#test-room` try creating one Decision and one Task (a) from an agent, (b) from a custom client like the World Bot, (c) by hand. Write which works in `docs/DECISIONS.md` L14.
5. Add the Switch docs MCP to Claude Code (and Codex if used).

## Installing the runtime agents (after Phase 06)
1. `scripts/install_agents.sh` copies each `agents/<name>/` template to `~/airgrid-agents/<name>/` with `.env` (role, token, API URL). Nothing else (L11).
2. Switch Console: create the 7 agents with those working directories. Providers: Claude Code for all, or Codex for fleet/airspace/relay.
3. Put each agent in its rooms (05-AGENTS), set Rules text.
4. `config/humans.yaml`: map your Discord username to the three roles. Restart the World Bot.

## Team later (config only)
Copy the team block in `config/humans.example.yaml`. Teammates run agents in their own Console with `AIRGRID_API_URL` pointing at the host (LAN IP or a tunnel). Nothing else changes.

## Demo day (Nov 7)
1. T-60: laptop on power, Docker + Console up, 7 agents green, `scripts/reset.sh FRIDAY-RUSH`, dashboard on a second screen, the recorded backup run open.
2. T-30: smoke test (accident beat only) in `#test-room`.
3. Clear test entries from Decisions panels.
4. Record master, cut trim from the same run.
5. Check every spoken number against the scorecard.
6. Submit repo + video + blog by ~6 PM, then #HowISwitch.

## If things break
| Failure | Fallback |
|---|---|
| Switch/Docker down | play the recorded run (same seed) and narrate; FakeRoom transcript as proof |
| Agent off-format live | restate in format ("confirming: …"), fix the prompt after |
| Agent slow, clock held too long | hold releases after 45 s; Dispatch catches up from `airgrid events --since` |
| Sim crash | `scripts/reset.sh`, `/control/skip` to the last beat |
| Approval not picked up | World Bot log; exact text `approve A-xxxx` from a mapped user |
| Decisions panel not writable | kazum creates the entry from the World Bot's one-line summary (L14) |
