# CONTEXT HANDOFF (read first in a new Claude Code session)

## Who
kazum (Aadarsh, Bengaluru). Learning agentic AI in Claude Code; this is also his CV final project. Wants things brief, direct, practical. Checks every number. Hates unnecessary files and AI-sounding text.

## The event
**Flip the Switch** hackathon (WeMakeDevs × DevTools Academy, powered by Switch from SandboxAQ): AI agents join Slack/Discord rooms as teammates. Nov 4–7, 2026. Target: **Best Business Workflow** ($2,000). Side tracks: Best Blog, #HowISwitch. Code before Nov 4 is allowed. Video length cap: unknown, plan 5:00.

## How we got here
1. Truck/freight idea → too generic.
2. **SkyControl OCC** (airline ops centre) → full kit v3.1 built. Parked.
3. **Meridian & Cross LLP** (law firm OS) → full kit v1 built. Parked.
4. Oct 29: kazum saw a video of Shenzhen's pole-mounted drone docks → **AirGrid**, city drone response network. Muse (another AI) wrote a pack; we reviewed it, kept the good parts (problem brief, formats, agent rules, UI direction, trigger menu), fixed the rest (DECISIONS L7–L20) and **computed** the whole Friday Rush timeline with `tools/fleet_check.py`. **LOCKED.**

## Settled
- Solo default: kazum = Watch Commander + Remote Pilot + Field Officer. The seven agents are his team. Team later via config.
- Sim owns state and does the time-critical work; agents narrate, propose, refuse, talk to humans.
- 14 drones / 18 slots / 3 loops / 2 simulated duty pilots / no auto pool / max 4 per pilot.
- Dashboard = static HTML/SVG + SSE, started Oct 31 in parallel.
- 8 rooms + `#test-room`.

## Status (Oct 29)
Kit complete: docs, world data (validator: 125 checks, 0 errors), scenarios, reference checker (0 violations), builder subagents, phase prompts. **No app code yet.**

## Next
1. kazum: `prompts/00-HUMAN-SETUP.md` (repo, unzip, validators, Discord, Switch).
2. Claude Code: `prompts/MASTER-PROMPT.md` → `docs/BUILD-PLAN.md` → wait for "go" → `/phase 01`.
3. Oct 31: start the dashboard lane in a second session (`/phase 07`).

## Open unknowns
- Switch Decisions/Tasks write mechanism (probe in `#test-room`, L14).
- Switch custom-client protocol details for the World Bot (docs MCP).
- Video length cap.
- Whether Codex is worth it (cut item 1).
