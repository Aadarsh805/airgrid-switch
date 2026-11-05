# 01 · Product Requirements

## What we ship (Nov 7)
1. **Simulator** (`sim/`): Northgate District, 14 drones, 14 docks / 18 slots, 3 patrol loops, 2 simulated duty pilots, ground units, incidents, suspect path, weather, dock faults. Deterministic, seeded, the only owner of state. Matches `tools/fleet_check.py` on FRIDAY-RUSH.
2. **`airgrid` CLI** the agents call; every write validated, rejections carry a reason code.
3. **World Bot** (plain code): sim events → rooms, approval cards, human `approve A-0007` → sim, Decisions entries.
4. **Seven runtime agents** in Switch rooms, tested headlessly first.
5. **Dashboard**: live map (hero), fleet board, incident panel, KPI strip, privacy ticker, controls, trigger menu, scorecard.
6. **Baseline** (the same Friday with patrol cars + one van drone team) and a scorecard.
7. **Repo + README + 5-min video + blog.**

## Users
- **kazum (solo default):** Watch Commander, Remote Pilot and Field Officer. Roles are per room and per message, mapped in `config/humans.yaml`.
- **Team later (config only):** T1 = Remote Pilot, T2 = Field Officer. Nothing in code assumes solo or team.
- **Simulated people (no LLM):** duty pilots DP-1 Asha Menon and DP-2 Tom Okoye (sim-run, accept drones up to 4 each), CAD caller lines, ground-unit radio lines (templates).

## Must-have (the demo cannot happen without these)
- Auto-launch policy, patrol rotation with relief-before-leave, slot booking, track relay — all **sim code**, not agents (latency).
- Reason codes: `BATTERY_RESERVE NO_FLY_ZONE WEATHER_GROUNDED DOCK_FULL DOCK_OFFLINE PILOT_SATURATED SEPARATION OUT_OF_RANGE AUTH_REQUIRED PRIVACY_POLICY MISSION_EXPIRED DRONE_NOT_READY`.
- Approvals only from a mapped human via the World Bot.
- Privacy: recording is derived state (on only inside an open incident geofence, never inside NR-HOSPITAL); `request_face_id` always rejected and logged.
- Friday Rush beats exactly as in `docs/golden-timeline.txt`.
- Decisions entries for every approval/refusal (or the manual fallback, L14).
- Scorecard managed vs baseline.

## Should-have
Trigger menu (fire, missing child, underpass flood, drone fault, unauthorised drone, VIP zone, manual officer request). QUIET-SHIFT scenario (agents stay quiet). Codex as second provider for Fleet/Airspace/Relay. Tasks panel = patrol plan.

## Won't (this hackathon)
Real maps, real drones, video feeds, computer vision, face anything, Part 108 compliance claims, multi-district, mobile app. Ideas → `PARKING-LOT.md`.

## Cut order (apply without debate when behind — L20)
1. Codex second provider → all agents on Claude Code.
2. Unscripted live-trigger segment in the video.
3. Footage-release flow (keep the refusal path).
4. Signal retiming / traffic advisories beyond one line.
5. Tasks panel (keep Decisions).
6. Dashboard polish: trails, auto-zoom, scorecard animation (keep map + fleet board + KPI strip).
7. `#incident-2` as its own room → merge into `#incident-1`.

**Never cut:** auto-launch eyes-on, track approval with scope, the 0-gap handoff, the PRIVACY_POLICY refusal, the NO_FLY_ZONE hold + reacquire, PILOT_SATURATED, MISSION_EXPIRED, Decisions log, baseline scorecard.

## Success metrics (from the golden run; G2 replaces any `[sim]` with measured)
| Metric | Managed | Baseline |
|---|---|---|
| Accident eyes-on | 114 s | 17.0 min (van) |
| Robbery outcome | tracked, detained 18:07 | van airborne 18:08:42, suspect lost |
| Ground units released early | 1 | 0 |
| Patrol junction coverage 17:00–20:00 | 93.9% | ~9% (one van, one loop, between call-outs) |
| Reserve breaches / red-zone entries | 0 / 0 | n/a |
| Refusals enforced on camera | 5 codes | — |
| First ground unit on scene, accident (info row, same unit model; L37) | 17:19:30 (+7.5 min, tasked by Comms 17:13) | 17:18:30 (+6.5 min, dispatched at alert) |

## Judging map (Best Business Workflow)
| Criterion | Where it shows |
|---|---|
| Real business workflow | 00-PROBLEM: six failures, real programmes, buyers |
| Uses Switch deeply | rooms as desks, agent→agent handoffs, Decisions = authorisation log, Rules = posted law, Tasks = patrol plan |
| Works live | headless tests 3×, live run in rooms, seed replay |
| Measurable impact | scorecard vs baseline, every number traceable to a log entry |
| Craft | dashboard map, fixed formats, README, video |
