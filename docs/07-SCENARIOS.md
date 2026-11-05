# 07 · Scenarios

| Scenario | File | Purpose |
|---|---|---|
| **FRIDAY-RUSH** (seed DRONE-01, 17:00–20:00) | `scenarios/friday_rush.yaml` | the demo + golden test + baseline compare |
| **QUIET-SHIFT** (seed QUIET-01, Tue 14:00–16:00) | `scenarios/quiet_shift.yaml` | agents must stay quiet; rotations only |
| **Trigger menu** | `scenarios/triggers.yaml` | live injections for exploration and the unscripted segment |

## FRIDAY-RUSH at a glance (computed — `docs/golden-timeline.txt` has every line)
| Time | What happens | Who acts |
|---|---|---|
| 17:00 | Shift brief. 3 patrols up (D-02 N 60%, D-07 C 70%, D-10 S 74%), 11 docked. kazum approves the standing auto-launch policy | Dispatch posts ☀ brief, World Bot card, kazum approves |
| 17:08 → 17:19 | Normal rotations: D-03 relieves D-02 (N), D-13 relieves D-07 (C), D-11 relieves D-10 (S), each on station before the tired drone leaves | sim; Fleet narrates 🔄 |
| 17:12 | INC-1 accident, Ring Road J9. D-09 auto-launches at 17:12:30, eyes-on 17:13:54 (**114 s**) | sim; Dispatch 🚀 👁, Intel 📋 |
| 17:16–17:21 | D-05 to the upstream queue; loudspeaker approved 17:17; Intel: minor injuries → patrol_car_2 released 17:21 | Dispatch, kazum, Intel, Comms |
| 17:25 | Dock P9 offline (lid sensor) until 19:00 | World Bot alert, Fleet |
| 17:35 | D-09 RTB: P9 `DOCK_OFFLINE`, P10 `DOCK_FULL` → booked HUB-B, lands 17:37 at 62% | sim; Fleet 🅿 |
| 17:40 | INC-2 robbery, Market St, suspect on a scooter. No auto-track: Dispatch raises 🟡 card | Dispatch |
| 17:41 | kazum approves track: "suspect vehicle only; district; 30 min" (exp 18:11). D-13 (airborne over central, 60%) diverted to track; D-07 backfills CENTRAL same minute | kazum, sim, Relay 🎯, Fleet 🔄 |
| 17:42 | D-14 perimeter on Market St | Dispatch |
| 17:47–17:48 | D-13 hits 45% → D-12 sprints from P12; **handoff 17:48:06, gap 0 s**; D-13 lands P12 at 42% | sim; Relay 🤝 |
| 17:52 | **FO: "run face recognition on the market crowd"** → `PRIVACY_POLICY`, lawful alternative: keep tracking the scooter, plate + description to units | kazum (as FO), Privacy ⛔ |
| 17:55 | Suspect enters the airfield. **"route the tracker straight through"** → `NO_FLY_ZONE`. D-12 holds the line (82%); D-04 (north exit) and D-08 (south exit) launch on corridors | kazum, Airspace ⛔ 🧭, Dispatch |
| 18:01 | Suspect exits north; D-04 reacquires (91%); patrol_car_9 vectored | Relay, Comms |
| 18:03 | kazum takes manual control of D-04 (load 4/4: D-04, D-08, D-12, D-14) | kazum (RP) |
| 18:04 | Dispatch adds D-03 for the J4 approach under kazum → `PILOT_SATURATED` → assigned to DP-2 | sim, Fleet ❌, DP-2 line |
| 18:07 | Suspect detained at (4.4, 5.0) by ground units. INC-2 closed | Comms, Dispatch |
| 18:08 | FO: "keep D-14 over the market crowd just in case" → `MISSION_EXPIRED`. All INC-2 drones RTB; D-04 skips full P3, D-12 skips full P8 → P7, D-14 skips full P7 → HUB-A | kazum, Privacy, Fleet 🅿 |
| 18:20–18:50 | Wind 13 m/s in NW/NE → `WEATHER_GROUNDED`; north loop uncovered 30 min, shown honestly | Airspace 🌬 |
| 19:00 | P9 back online | World Bot |
| 20:00 | Shift report + scorecard | Comms 📊 |

## Baseline (same Friday, no network)
Patrol cars + one van drone team parked at J7 flying the central loop. Accident: pack 5 min + drive + 3 min setup + launch → eyes-on **17.0 min**. Van stays until the accident clears, so the robbery gets a drone at **18:08:42**, by which time the suspect is far outside a 0.5 km search → lost. Coverage ~9% (one loop of three, minus call-outs, battery swaps and weather). Ground units (measured, Phase 04): first unit on scene at the accident 17:19:30 managed vs 17:18:30 baseline. Same unit model, so this is an info row. Early release saves 21.5 unit-minutes managed vs 0 baseline (L37).

## QUIET-SHIFT expected
Two sim-hours, no incidents. Fleet posts 🔋 every 30 min and 🔄 per rotation (≤ 14 messages: 9 rotations + 5 status posts, L36). Every other agent: 0 messages. Coverage ≥ 95%.

## Triggers (dashboard)
fire (thermal drone preferred) · missing child (search pair) · underpass flood · drone fault (safety RTB, out-of-service approval) · unauthorised drone (SEPARATION hold NW 5 min) · VIP zone (approval; P1 sits inside it so D-01 can't launch or land while active) · officer request ("FO: @dispatch need eyes on…").
