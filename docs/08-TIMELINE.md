# 08 · Timeline (solo, today = Thu Oct 29)

Code before Nov 4 is allowed. The pre-week is where the project is won: sim, CLI, World Bot and dashboard exist before the hackathon starts, so Nov 4–7 is agents, Switch and the video.

One phase per Claude Code session. ⛔ = kazum signs off in `docs/PROGRESS.md`.

| Day | Claude Code (main lane) | Dashboard lane (parallel session) | kazum (human) |
|---|---|---|---|
| **Thu Oct 29** | — | — | read kit, `00-HUMAN-SETUP`, repo, unzip, validators green |
| **Fri Oct 30** | 01 bootstrap · start 02 sim core | — | Discord server + rooms; install Switch Console + Docker; hello-agent in `#test-room` |
| **Sat Oct 31** | 02 sim core → golden run equals checker · **⛔ G1** | 07a static map + fleet board from a recorded golden replay | Decisions/Tasks probe in `#test-room`, write result to DECISIONS L14 |
| **Sun Nov 1** | 03 rules + actions (all codes, privacy, recording) | 07b SSE live + KPI strip | review G1 output, check numbers |
| **Mon Nov 2** | 04 incidents, suspect, ground units, triggers, baseline, KPIs | 07c incident panel, ticker, controls | optional: Codex CLI + docs MCP |
| **Tue Nov 3** | 05 CLI + World Bot (FakeRoom) + `/demo-run` in terminal | 07d triggers + scorecard state | full FakeRoom dry run, note confusion |
| **Wed Nov 4** (hackathon day 1) | 06 agents headless, 10 cases × 3 runs · **⛔ G2** | polish if time | read transcripts, sign G2, replace `[sim]` numbers |
| **Thu Nov 5** | 08 Switch live (SwitchRoom, DecisionSink, templates, install agents) · **⛔ G3** | — | Console: create 7 agents, rooms, Rules text; G3 live checks |
| **Fri Nov 6** | 09 ship part 1: README, scripts, rehearsal fixes | dashboard final visual pass (Playwright screenshots) | 2 full rehearsals, time the script |
| **Sat Nov 7** | 09 ship part 2: final checks · **⛔ G4** | — | record master + trim, blog, submit by ~6 PM, #HowISwitch |

## Why not one day
Each layer needs the one below it to be *right*, not just present: the golden run has to equal the checker before agents can rely on numbers; agents need 3 green headless runs before Discord; Switch needs a human in the Console. Cramming it produces a demo that works once. The parallel dashboard lane is the time saver.

## Behind schedule?
Apply the cut order in 01-PRD (DECISIONS L20) without debate. The trigger: any gate slips more than half a day.

## After Nov 7 (CV version)
Team mode, second district, a real map behind a flag, replay viewer, write-up. Parking lot.
