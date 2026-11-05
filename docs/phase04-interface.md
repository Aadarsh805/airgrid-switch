# Phase 04 interface (sim-engineer builds, test-engineer tests against it)

Sources: prompts/04-world-dynamics.md · 03-SIM-SPEC §7–9 · 07-SCENARIOS · `scenarios/triggers.yaml` · DECISIONS L24, L29–L35 · the baseline section of `tools/fleet_check.py`.

## Ground units
- 10 `patrol_car_1..10`, 3 `ambulance_1..3`, 2 `fire_engine_1..2`. Initial positions and on-scene minutes per incident kind live in `world/airgrid.yaml` (`ground_units.start`, `ground_units.on_scene_min`). Manhattan movement at `road_speed_km_per_min`.
- `task_unit {unit, incident}` → en route, ETA = 1-min dispatch + Manhattan / speed. `release_unit {unit, incident, evidence}` is an early release: allowed only while the unit isn't yet needed on scene; the evidence must quote a revealed feed fact.
- Golden: `units_golden` entries run as comms actions. Unit events are **not** timeline lines, so the golden stays byte-identical.
- `GET /units` → `[{id, kind, status (available/en_route/on_scene/returning), pos, incident, eta_min}]`.

## Incidents and feed
- `GET /incidents` and `GET /incidents/{id}` → `{id, kind, at, priority, status, geofence_km, opened, closed, drones, units, authorisations, recording_drones, facts}`.
- `facts` holds only scenario `feed` entries with `t <= now` **and** after a drone of that incident has been on station. Never identities. Unrevealed facts never appear anywhere.
- Triggered incidents are `INC-T<n>` (L33).

## Triggers — `POST /trigger {kind, at?}` (operator control, logged, L33)
| kind | effect (tested) |
|---|---|
| fire | INC-T fire; auto-launch prefers a thermal drone (no thermal penalty) |
| missing_child | INC-T missing_person; auto-launches a search pair (2 drones, mission `search`) |
| underpass_flood | INC-T flood; auto-launch |
| drone_fault | tightest airborne drone: safety RTB now + an `out_of_service` approval card (pending) |
| unauthorised_drone | NW SEPARATION hold for `policies.intrusion_hold_min` (5): launches into NW → SEPARATION; airborne NW drones hold position |
| vip_zone | `zone` approval card for VIP-TEMP (until now + 60). Once approved: active; P1 unusable (D-01 can't launch or land; landings reroute); routes through it → NO_FLY_ZONE |
| officer_request | `room_message` event `{room: field-units, text}` for the World Bot; no sim effect |
Unknown kind → 404 NOT_FOUND.

## Baseline
- `sim/baseline.py` ports the checker's baseline arithmetic exactly. Its parameters (van park point, pack-up min, accident on-scene min, swap allowance, weather allowance) move into `world.baseline` (L35).
- Ground units in the baseline: per call, nearest unit, no early release.
- `POST /baseline/run` → runs the baseline for the loaded scenario. `GET /kpi` → `{managed: {...}, baseline: {...} | null, wins: {line: bool}}`. `GET /kpi/history` → per-minute KPI snapshots up to now.
- During a managed run, `/kpi.baseline` holds only the final summary numbers. No baseline timeline or internals.
- `scripts/run_baseline.sh SCENARIO` prints the scorecard (managed vs baseline). It exits 1 unless managed wins every counted line.

## Scorecard
- `sim.golden.run().scorecard` must now equal the checker's whole scorecard block in `docs/golden-timeline.txt`, baseline lines included (eyes-on 114 s vs 17.0 min; robbery vs van 18:08:42 → LOST; released 1 vs 0; coverage 93.9% vs ~9%; loops; refusals; fleet; ready floor). `scripts/run_golden.sh` diffs that block too.
- Counted lines for "managed wins": accident eyes-on, robbery outcome, units released early, unit-minutes saved, coverage. Reserve breaches and red-zone entries are safety checks (managed must be 0), not wins (L37k). "First ground unit on scene, accident" is an uncounted info row (L38).
- The measured first-unit numbers replace `[sim]` in 01-PRD / 07-SCENARIOS (lead does the doc edit).

## QUIET-SHIFT golden
`sim.golden.run("QUIET-SHIFT")`: 0 violations, coverage ≥ 95%. Fleet narration count (🔄 rotations + 🔋 every 30 min) ≤ 14 per `expected.agent_messages_max` (L36), computed from events.
