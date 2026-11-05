# 00 · The Problem, the Solution, and Why Switch

## One line
Cities don't lose drone programmes in the air. They lose them in coordination: launches that miss the two-minute window, batteries and landing slots run by habit, no-fly lines remembered instead of enforced, and authorisations nobody can produce afterwards.

AirGrid is that coordination layer, run as a team in Switch rooms: one human on watch, seven AI agents on the desks, and a simulator that owns the truth and refuses anything illegal.

## This is happening now (verified, Oct 2026 — cite these, nothing else)
| Fact | Source to cite in README |
|---|---|
| Chula Vista (CA) drone-as-first-responder: 25,000+ calls flown by May 2026; drone first on scene for 17,170 of them, average 96.98 s; 4,629 calls cleared with no patrol car sent (~1 in 5) | Chula Vista PD DFR public dashboard / city reporting |
| Chula Vista priority-1 calls: drone ~3.5 min vs ~8 min for a patrol car | same |
| Montgomery County (MD) DFR: ~64 s average to scene | county DFR reporting |
| 214 FAA DFR waivers approved by June 2025 (after the 2025 waiver change); waivers allow one remote pilot to supervise up to 4 drones | FAA / industry reporting |
| FAA Part 108 (BVLOS rule) still a proposed rule; final review mid-2026 | FAA |
| Skydio DFR Command: 10M+ calls handled across customers | Skydio |
| Las Vegas Metro: 75 drones across 13 skyports | LVMPD |
| Shenzhen: 8 police drone units; drone school patrols in 11 districts; pole-mounted dock network on roads | Shenzhen reporting (the video this project started from) |
| Wuhan: 146 automated drone docks | Chinese municipal reporting |
| Bengaluru traffic police use drones + AI for congestion | Indian press |
| India Drone Rules 2021: green / yellow / red airspace zones | MoCA Digital Sky |
| DJI Dock 3: 54 min max flight, 10 km operating radius, 15→95% charge in ~27 min, 12 m/s wind limit, IP56 | DJI spec sheet |

**Do not use** (unverified in Muse's pack): "Futian 65 docking stations by March 2026", "~6,000 US police drone programmes", "signal retiming cut queues ~50 m". If one gets a real source later, add it here first.

Our model is calibrated to the DJI Dock 3 class and to the Chula Vista eyes-on figures. Every number on screen comes from our simulation, labelled as such. We never present a real programme's numbers as our result.

## The six coordination failures
1. **Dispatch latency.** The whole value is live eyes in ~1.5–3.5 min instead of ~8. That window is lost on the ground: a dispatcher checking battery by eye, phoning for authorisation, launching late.
2. **Battery and reserve run by habit.** Drones flown until empty, or everyone charging at the evening peak, so coverage is zero exactly when it matters. Worse: a drone eats its return reserve over an incident.
3. **Landing slots are infrastructure.** A drone coming off a chase finds its home dock full or offline and has to be sent somewhere else, now.
4. **The airspace is legal, not just physical.** Airfield red zone, a temporary VIP closure, a hospital where transit is fine but recording isn't. One shortcut during a chase and the programme is grounded.
5. **Privacy and authorisation end programmes.** Tracking people without authorisation, recording outside the incident, face-recognition creep, footage shared with no record. Programmes get paused over this, not over crashes.
6. **Nobody can reconstruct the shift.** "Who approved this flight, for what, what did it record, when did recording stop?" Real DFR policies require that answer.

## Mechanism and proof per failure (numbers from `tools/fleet_check.py`, same seed DRONE-01)
| Failure | Mechanism in AirGrid | Friday Rush proof (managed vs baseline) |
|---|---|---|
| Latency | Standing auto-launch policy (approved at the 17:00 brief) for accident/fire/flood alerts: the sim launches the nearest capable drone itself; agents narrate and coordinate after | Ring Road J9 accident: eyes-on **114 s** vs **17.0 min** for the baseline van team |
| Battery | Rotation relief at ≤48% launches *before* the tired drone leaves; 25% reserve is a hard floor; track relay launches at ≤45% from battery projection | 27 flights, **0 reserve breaches**, lowest landing **42%**; chase handoff D-13 → D-12 with 0 s gap |
| Slots | Slots are booked when RTB is ordered ("confirm slot, then RTB"); full/offline docks reroute in code | D-09 off the accident: P9 `DOCK_OFFLINE`, P10 `DOCK_FULL` → lands HUB-B at 62%. Never circles |
| Airspace | Red zone has no override; weather grounds sectors at wind > 12 m/s | 17:55 straight route `NO_FLY_ZONE`; tracker holds the line, D-04 reacquires on the far side 18:01. 18:20 wind 13 m/s grounds the north for 30 min, honestly shown |
| Privacy | Track needs a named approval with scope + expiry; face ID doesn't exist in the system; recording only inside an open incident geofence | 17:52 face-recognition request refused `PRIVACY_POLICY` mid-chase, lawful alternative given; suspect detained 18:07 anyway; 18:08 "keep it up just in case" → `MISSION_EXPIRED` |
| Record | Every launch basis, approval and refusal → Switch Decisions + `#privacy-log` + sim `decisions_log` | The shift reconstructs from the Decisions panel on one screen |

Also: pilot load is capped at 4 drones per remote pilot (the real waiver ratio). At 18:04 kazum already flies four, so a fifth is refused `PILOT_SATURATED` and goes to simulated duty pilot DP-2.

## Why Switch (the judging question)
Switch is the ops centre, not a chat skin.
1. **Rooms are the ops structure.** `#dispatch`, `#fleet-hangar`, `#airspace`, `#incident-1`, `#incident-2`, `#field-units`, `#privacy-log`, `#shift-report`. Agents hold desks the way duty staff do; an agent's context is its room. The Relay plan is visible to the field officer because they share `#incident-2`, not because we wrote glue.
2. **Handoffs happen in the open.** Dispatch → Relay when a chase needs a relay plan; Relay → Comms → ground units in `#field-units`; Intel → Dispatch when a scene report changes what rolls.
3. **Decisions / Tasks / Rules are load-bearing.** Decisions = the flight-authorisation log (failure #6 is solved by a Switch panel). Tasks = patrol plan + incident action items. Rules = flight and privacy rules posted verbatim where the work happens.
4. **A human and seven agents in one room.** Solo: kazum is Watch Commander, remote pilot and field officer; the agents are his team. The Console shot shows seven agents (Claude Code, Codex optional) in one workspace.
5. **Counterfactual.** Without Switch: seven isolated CLI sessions, no shared picture, no incident rooms, no authorisation log, no way to approve a chase inside the flow of work. The simulator is the system of record *behind* the rooms (like a programme's safety case behind its operators); every act of dispatch, judgement and record happens in Switch.

## Framing and boundaries (README + on camera)
City emergency and traffic response with privacy enforced by code. Fictional Northgate District and agency. Simplified rules modelled on real DFR practice and the DJI Dock 3 class. No real hardware, no real dispatch, no real map, no identification of people (the capability does not exist in the system). Not police-first: accidents, fire, floods and traffic are most of the day job.

## Buyers in the pitch
City police and traffic departments, smart-city operators, highway authorities, event security, private sites (ports, campuses, industrial parks).
