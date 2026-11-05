# Game mode: "Friday Rush", a 3-minute playable shift

kazum approved this Oct 31 (L54). The full simulator, golden run and CV branch stay as they are: `mode: game` is added on top, and `run_golden.sh` must stay at 0 diffs.

## Flow
A judge understands it in 10 seconds: you're the Watch Commander, three crises, one score. Real time about 3 minutes.

| Step | Sim time | What happens | Player |
|---|---|---|---|
| start | jump to 17:11 | FRIDAY-RUSH runs headless to 17:11 with the standing policy already approved | press START |
| crash | 17:12–17:21 | alert, auto-launch, eyes-on 114 s | RELEASE the unit: the minor-injuries fact is the evidence |
| robbery | 17:40 | "track needs your approval" | APPROVE or REJECT track |
| trap | 17:52 and 17:55 | FO asks for face recognition, then a route through the airfield; each is refused by the real validator | pick "try it": sees the refusal + lawful alternative |
| finale | 18:01–18:07 | suspect exits north; D-04 available | TAKE CONTROL and fly D-04 to the scooter |
| score | end | scorecard vs "the old way" | PLAY AGAIN |

## Sim (`sim/game.py`, sim-engineer)
- `POST /game/start {}`, `GET /game` → `{step, prompt, options, clock, score_so_far, done}`, `POST /game/choose {choice}`, `POST /game/fly {dx, dy}` (unit vector or 0,0), `GET /game/score`.
- Clock: `game` mode runs 0.5 sim-min/s around beats and auto-skips dead time; it holds at each prompt until the player acts. A fallback timer (20 real s) picks the safe default so nobody gets stuck (recorded as "timed out").
- All choices go through the existing validators, approvals and logs. Nothing bypasses `actions.execute`, the privacy log or decisions_log.
- Flying: only the drone under `take_control`. Move at cruise speed per tick; battery drains; a move entering RED-AIRFIELD or an active VIP zone is blocked at the boundary, with a NO_FLY_ZONE event. No fly input for 20 real s: the drone holds.
- Catch: tracked drone within `policies.game_catch_km` (0.15) of the suspect → "caught by you". If missed, ground units detain at 18:07 as usual and the score is lower.
- Score out of 100, weights in `world.policies.game_score`: response, approval, rules kept, catch, coverage. Rank: Rookie / Officer / Commander. Numbers from the run; shown beside "the old way" (17.0 min, suspect lost).
- Deterministic: same choices and same fly inputs → same result.

## Operator key (design rule 3 change, L54)
Rule 3 stays: only humans approve. `POST /game/*` needs `X-Game-Key`, a random key made at start by `run_game.sh`, passed to the sim by env and opened as `/ui/?key=...`. It isn't in any agent's directory. No key → 403 FORBIDDEN_ROLE. The actor is logged as `kazum (dashboard)`.

## Discord (`worldbot`, integration-engineer)
- `--profile game`: rooms `#ops` and `#privacy-log` only.
- Only the moments in the table post, about 15 messages a shift. No rotations, RTB, landing, unit or feed lines.
- Mentions only for `dispatch`, `intel`, `airspace`, `privacy`. Agent beats must not hold the game clock over 8 s: agents narrate, never gate.
- If agents are offline the game still plays. Discord is the commentary.
- Typed `approve A-000n` in `#ops` still works.

## Dashboard (`dashboard/`, dashboard-builder)
- `?game=1` (default when `key` is present). Start screen: title, one sentence, START.
- City fills the screen. One big instruction card with big buttons at the bottom. Small score chip. No fleet table, log wall or KPI strip in game mode; the "Ops" toggle reveals them.
- Camera moves to each moment. Alarm flash at incidents. Sounds optional and off by default.
- Take control: click TAKE CONTROL, chase-camera behind D-04, WASD/arrows, battery bar, distance to the scooter. Red wall flashes on NO_FLY_ZONE.
- End screen: score, rank, 5 lines vs the old way, Play again. Footer stays "Fictional district · simplified rules · simulated".

## Run
`scripts/run_game.sh [--discord]` starts sim + bot (optional) and prints the URL.

## Fallback
If flying isn't ready, the finale is one click: "Take D-04", sim steers. Nothing else depends on it.
