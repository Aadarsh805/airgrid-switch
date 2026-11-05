# 03 · Simulation Spec

`world/airgrid.yaml` and `scenarios/*.yaml` are canonical. If this doc and the YAML disagree, the YAML wins. `tools/fleet_check.py` is the reference model: the real sim's `golden` run on FRIDAY-RUSH must reproduce `docs/golden-timeline.txt` (same events, same minutes ±0.1, batteries ±1).

## 1. Key facts
| Thing | Value |
|---|---|
| District | Northgate, 6 × 6 km, sectors NW/NE/SW/SE split at (3, 3) |
| Junctions | J1–J12 at x ∈ {0.75, 2.25, 3.75, 5.25}; J1–J4 y=5, J5–J8 y=3, J9–J12 y=1 |
| Landmarks | market_street (3.6, 3.0), ring_road_j9 (1.5, 1.0), hospital (2.1, 4.0), old_airfield (5.8, 3.2) |
| Zones | RED-AIRFIELD x 5.6–6.0, y 2.2–4.2, no override · NR-HOSPITAL x 1.6–2.6, y 3.6–4.4, transit OK, recording forced OFF · VIP-TEMP x 0–1.2, y 4.4–6, inactive until approved |
| Docks | P1–P12 pole docks at J1–J12 (1 slot each) · HUB-A (3, 4) and HUB-B (3, 2), 3 slots each → 14 docks, 18 slots |
| Fleet | D-01…D-12 home P1…P12, D-13/D-14 home HUB-A; thermal: D-06, D-12 |
| Patrol loops | NORTH centre (3, 5) J1–J4 · CENTRAL (3, 3) J5–J8 · SOUTH (3, 1) J9–J12 |
| Pilots | kazum (human; max 4) · DP-1 Asha Menon, DP-2 Tom Okoye (simulated; max 4). **No auto pool.** Max airborne = 12 |
| Ground units | 10 patrol cars, 3 ambulances, 2 fire engines; Manhattan, 0.5 km/min |
| Baseline | patrol cars + one van drone team (setup 3 min, 40 min flight, 27 min charge) |

## 2. Drone model (DJI Dock 3 class, simplified)
| | |
|---|---|
| Speeds | cruise 0.9 km/min, sprint 1.2 km/min (relay catch-up only) |
| Launch delay | 0.5 min (lid + climb), hover drain |
| Drain %/min | patrol 1.5 · hover 1.5 · cruise 1.8 · track 2.4 · sprint 3.0 |
| Charge | +3.0 %/min docked, cap 100 (≈ 15→95 in 27 min) |
| Reserve | 25% hard floor. Safety RTB when projected landing at nearest free slot ≤ 27% |
| READY | docked, dock online, ≥ 80% |
| Launch gates | patrol 80 · incident 50 · track 60 · perimeter 50 · corridor 60 |
| Relief triggers | patrol ≤ 48% · track ≤ 45% |
| Weather | wind > 12 m/s grounds a sector: no launches there, airborne patrols there RTB |
| Range | 10 km from launch dock (OUT_OF_RANGE) |

## 3. Automatic policies (sim code — order matters, mirror the checker)
1. **Auto-launch (standing policy):** on `cad_alert` of kind accident/fire/flood, launch the nearest READY-or-≥incident-gate drone not in a grounded sector, pilot = first human remote pilot with capacity, else a duty pilot. Missing-person also auto-launches a search pair. **Robbery/suspect never auto-launches a track** (`AUTH_REQUIRED`); it raises an approval card.
2. **Patrol rotation:** each loop wants exactly one on-station patrol drone. If the on-station drone is ≤ 48%, or the loop is uncovered, and no relief is already inbound, launch the nearest READY drone ≥ 80% (thermal drones get +0.75 km penalty so they're kept for night/track). Pilot: SOUTH prefers DP-2, others DP-1 then DP-2. When the relief arrives on station, the old drone RTBs. **Relief is airborne before the tired drone leaves.**
3. **Backfill:** when a patrol drone is diverted, its loop is uncovered → rule 2 launches a backfill the same tick.
4. **Slot booking at RTB:** order docks by distance (home breaks ties). The first dock with a free, unreserved slot is reserved. Every nearer dock skipped within 1.6 km is logged as `DOCK_OFFLINE` or `DOCK_FULL`. No free slot anywhere = invariant violation.
5. **Track relay:** while a track is active and the tracker ≤ 45%, launch the nearest drone ≥ 60% to the suspect position predicted 3 min ahead, sprint; handoff when within 0.15 km; old tracker RTBs. Gap 0 s by construction.
6. **Red zone:** a tracker whose target enters the red zone moves to the nearest boundary point and holds. Reacquisition is a corridor launch (human/agent action).
7. **Safety RTB:** any airborne drone whose projected landing ≤ reserve + 2 RTBs now. In the golden run this must never fire (it's flagged as a plan failure).
8. **Weather:** grounded sectors: patrols RTB, loops show uncovered, no launches; coverage KPI drops and stays dropped until reopened.
9. **Incident close:** all drones on that incident RTB; recording off; any later request to keep one up → `MISSION_EXPIRED`.

## 4. Rules (validators → `ok` or `Rejection(code, reason, hint)`)
| Code | When |
|---|---|
| `BATTERY_RESERVE` | planned task's projected landing < 25% |
| `DRONE_NOT_READY` | launch of a drone that is airborne, charging below the gate, or on an offline dock |
| `NO_FLY_ZONE` | any route/position inside RED-AIRFIELD or an active VIP zone. No override, any role |
| `WEATHER_GROUNDED` | launch into / patrol in a grounded sector |
| `DOCK_FULL` / `DOCK_OFFLINE` | `dock_at` a full/offline dock (auto-booking logs them, then reroutes) |
| `PILOT_SATURATED` | assigning a 5th drone to a pilot |
| `SEPARATION` | two drones' targets within 0.1 km at the same altitude band (sim offsets bands automatically; only explicit `goto` can trip it) |
| `OUT_OF_RANGE` | target > 10 km from launch dock |
| `AUTH_REQUIRED` | track, loudspeaker, footage release, VIP zone, divert-off-incident without an approval on record |
| `PRIVACY_POLICY` | `request_face_id` (always), `set_recording on` outside an open incident geofence or inside NR-HOSPITAL, track without an open incident |
| `MISSION_EXPIRED` | action on an incident that is closed or whose authorisation expired |

## 5. Recording (derived, never set freely)
`recording = drone on incident mission AND inside that incident's geofence (r = 0.6 km, or the track corridor while a track is authorised) AND NOT inside NR-HOSPITAL`. Transit = off. Every on/off transition is a `privacy_log` row. A recording-on outside a geofence is a P1 bug and an invariant failure.

## 6. Validated actions
`launch, divert, assign_track, assign_perimeter, corridor_watch, rotate_patrol, rtb, dock_at, take_control, release_control, broadcast(loudspeaker), request_footage_release, release_footage, set_recording, retime_signal, issue_advisory, task_unit, release_unit, stand_down, open_incident, close_incident, activate_zone, request_face_id (always rejected), log_refusal, simulate(what-if, no effect)`.

## 7. Incidents and suspect
- `cad_alert` creates an incident with kind, location, priority, geofence. Intel facts (vehicle counts, injuries, lanes, smoke class, suspect vehicle description) come from scenario `feed` entries revealed only once a drone is on station. Never identities.
- Suspect path: waypoints in the scenario, linear interpolation. The API serves only the **current** suspect position, and only while a drone has it in view.

## 8. Ground units
Tasked by Comms (`task_unit`), Manhattan movement, on-scene timers per incident kind, early release allowed when the feed shows it isn't needed (`release_unit` with evidence). Baseline sends units per call with no early release.

## 9. KPIs
Eyes-on per incident (alert → first drone on station), first ground unit on scene, units released early, patrol junction coverage (% of loop-minutes with an on-station patrol drone, 17:00–20:00), reserve breaches, red-zone entries, refusals by code, flights, lowest landing battery, ready-floor minimum (≥ 3 required; golden min is 4), privacy-log completeness (every launch has a basis, every recording window has open/close).

## 10. Friday Rush computed numbers (from the checker — the demo uses only these)
See `docs/golden-timeline.txt` for every line. Headline: eyes-on INC-1 114 s · D-09 rerouted P9 offline → P10 full → HUB-B · D-13 diverted at 60% · relay D-12 17:47:24, handoff 17:48:06 at 43% · refusals 17:52 PRIVACY_POLICY, 17:55 NO_FLY_ZONE, 18:04 PILOT_SATURATED, 18:08 MISSION_EXPIRED, 18:20 WEATHER_GROUNDED · D-04 reacquires 18:01 · detained 18:07 · coverage 93.9% · 27 flights, 12/14 drones flown · lowest landing 42% · ready floor min 4 at 17:56.

## 11. Determinism
One seeded RNG (`seed` from scenario) used only for CAD caller wording and small feed jitter; no randomness in physics. Integer tick counter; floats rounded for display only.
