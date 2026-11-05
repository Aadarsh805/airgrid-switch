# BUILD PLAN

Written Oct 29 from the kit. One phase per session, `/phase NN`. ⛔ = kazum signs in `docs/PROGRESS.md`.

## Phases
| # | Day | What | Gate (run and observed, not assumed) |
|---|---|---|---|
| 01 | Oct 29–30 | pyproject (uv, deps, `airgrid` script, `agents` marker), ruff, package skeleton per 02 repo layout, `/health`, CLI `status` stub, world-data test, PROGRESS.md, HUMAN_TODO.md, script stubs | `uv sync && uv run pytest -q` green · `/health` answers · ruff clean |
| 02 | Oct 30–31 | Sim core: world, geo, fleet, docks + booking, pilots, airspace, weather, suspect, policies, clock, scenario, golden, kpi, events, db; read endpoints + SSE + control; `run_golden.sh` → `.txt` + `.jsonl` | golden diff vs `golden-timeline.txt` (lines in order, ±0.1 min, ±1%) · same hash twice · **⛔ G1** |
| 03 | Nov 1 | `rules.py` (14 codes), `actions.py` + `POST /actions`, `privacy.py` (derived recording, append-only log, face ID always rejected), approvals + human token, authorisations + expiry, `decisions_log` | every code: one reject + one pass test · golden still matches · 17:52/17:55/18:04/18:08 refusals come from real validators |
| 04 | Nov 2 | incidents + feed reveal, ground units, `POST /trigger` (all of `triggers.yaml`), baseline, `/kpi`, QUIET-SHIFT golden, replace `[sim]` numbers | scorecard managed vs baseline, managed wins every line · each trigger tested · invariants over 20 seeds |
| 05 | Nov 3 | full `airgrid` CLI (§4.1), World Bot on FakeRoom (cards, approvals by role, lines, fake decision sink, activity → holds), `make_tokens.sh`, `/demo-run` | CLI ok/2/1 per command · leak grep · FakeRoom FRIDAY-RUSH = golden outcome · dry-run report saved |
| 06 | Nov 4 | 7 instruction files + `.env.example`, `install_agents.sh` + isolation test, headless harness, format regexes, number-provenance, 10 cases | `pytest -m agents` 3× green · headless run saved · **⛔ G2** |
| 07 | Oct 31 → Nov 6 | dashboard lane (parallel session) | Playwright shots at 1920/1440 for 17:13:54, 17:48, 17:55, 18:25, 20:00 · no console errors |
| 08 | Nov 5 | `SwitchRoom`, Switch decision sink (or fallback), room templates, Console steps in HUMAN_TODO | **⛔ G3** live beats + team-mode check with a 2nd Discord account |
| 09 | Nov 6–7 | README, `run_demo.sh`, `reset.sh`, blog, fresh-clone test | **⛔ G4** rehearse ×2, record, submit |

## Folder ownership (builder subagents)
| Owner | Writes only |
|---|---|
| `sim-engineer` | `sim/`, `world/`, `scenarios/`, `tools/` |
| `test-engineer` | `tests/`, `evals/`, `agents/harness/` |
| `integration-engineer` | `cli/`, `worldbot/`, `agents/switch/`, `config/`, `scripts/install_agents.sh`, `scripts/make_tokens.sh` |
| `agent-author` | `agents/<name>/` (the seven) |
| `dashboard-builder` | `dashboard/` (+ the `/ui` static mount in `sim/app.py` if missing) |
| lead (me) | `pyproject.toml`, other `scripts/`, `docs/`, `HUMAN_TODO.md`, `README.md`, commits |
| `reviewer` | nothing; read-only at the end of every phase |

Parallel where folders don't overlap: sim + tests in 02–04; CLI/World Bot + tests in 05; agent-author + harness + install script in 06.

## Dashboard lane (Phase 07, from Oct 31, separate session, `dashboard/` only)
- Until G1: drive it from `tools/fleet_check.py --quiet --dump-jsonl dashboard/fixtures/golden.jsonl` (flag exists, checked) via `?replay=fixtures/golden.jsonl`.
- After G1: switch the fixture to `runs/golden-*.jsonl` from the real sim (same frame shape, or a small adapter in the page).
- After 02's SSE: live mode. After 03/04: incident panel, ticker, triggers, scorecard. Build order = 06-UI-UX.
- Only shared file: the `/ui` mount in `sim/app.py`. Phase 01 adds it so the lane never edits `sim/`.

## Human gates
- **G1** (end of 02, Oct 31): golden output next to the checker, signed in PROGRESS.md.
- **G2** (end of 06, Nov 4): headless scorecard + transcript; demo-script numbers swapped to measured if they differ.
- **G3** (in 08, Nov 5): live in Switch, solo + second account as field_officer.
- **G4** (in 09, Nov 7): rehearsals, recording, submit.
Rule: a gate failing twice → stop and explain. Any gate slipping > half a day → cut order.

## Cut order (L20, no debate)
1. Codex → all Claude Code. 2. Unscripted segment. 3. Footage release (keep refusal). 4. Retiming/advisories beyond one line. 5. Tasks panel. 6. Dashboard polish. 7. Merge `#incident-2` into `#incident-1`.
Never cut: auto-launch eyes-on, track approval with scope, 0-gap handoff, PRIVACY_POLICY, NO_FLY_ZONE hold + reacquire, PILOT_SATURATED, MISSION_EXPIRED, Decisions log, baseline scorecard.

## Risks
1. **Switch has no Decisions or Tasks panel in its current docs.** I searched the switch-docs MCP: rooms have Instructions, room documents, references, packages and roles. Nothing called Decisions or Tasks. What does exist: `create_room_document` / `update_room_document` agent operations (room-scoped docs are agent-writable), and a custom-client protocol (`POST /agents`, WebSocket connection, `post_message`). Proposed **L26** (I'll log it on your "go"):
   - Rules → **Room instructions** (verbatim 05-AGENTS text).
   - Decisions → a room document **"Flight authorisation log"** in `#dispatch`, appended by the World Bot (a registered custom client) on every approval, rejection and refusal. Fallback: World Bot posts the one-line summary in-room.
   - Tasks → a room document **"Patrol plan"**, or cut (item 5).
   - Docs 00, 01, 05, 09 say "Decisions panel". They need rewording once your probe confirms. The Oct 31 probe should test exactly this: can a registered client create/update a room document, and can you see it (docs say documents can't be opened from the messaging app, only via the Gateway/Console). If nobody can show it on screen, the video uses `#privacy-log` + `airgrid decisions` instead.
2. **Golden must match the checker to ±0.1 min.** Port `fleet_check.py`'s structure and tick order rather than re-deriving; write the diff tool first, in 02.
3. **Agents act only when addressed** (Switch delivers every message but agents act on `payload.addressed`). Good for "stay quiet", but the World Bot must @mention the owning agent on each event. It must also be registered under kazum so "Me and my agents" lets it address them. Team mode needs addressing widened per agent.
4. **Demo holds** need the World Bot to see "agent posted". It sees every room event as a member, so release on the owner's next post or 45 s. Verify in 08.
5. **Windows host.** `.sh` scripts run in Git Bash; `~/airgrid-agents` = `C:\Users\Aadarsh\airgrid-agents`. Paths via `pathlib`, LF pinned already. Switch Console + Docker on Windows is unverified until your Oct 30 setup.
6. **Token cost/time of Phase 06** (10 cases × 3 runs × several agents). Keep cases short, scenario-time snapshots, one retry budget.
7. **Leaks** (future events, suspect path, baseline) through `/state` or SSE diffs. The serialisers will whitelist fields, with a grep test from 02 onward, not just 05.
8. **Schedule.** 02 is the biggest phase and G1 is due Oct 31 alongside your Discord/Switch setup. If G1 slips to Nov 1, 03 and 04 compress into one day and cut items 2–4 go early.

## Not doing
No Streamlit, Docker for our services, SimPy, Faker, agent frameworks, map tiles, LLM in the sim. New ideas → `PARKING-LOT.md`.
