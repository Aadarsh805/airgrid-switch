# 10 · Testing and Evals (definition of done)

## Layer 1 · World data
`uv run python tools/validate_world.py` → 0 errors. `uv run python tools/fleet_check.py --quiet` → "OK — 0 violations".

## Layer 2 · Simulator unit tests (`tests/sim/`)
- Battery: each drain mode, charge cap, 15→95 ≈ 27 min.
- Movement: cruise/sprint, launch delay, arrival tolerance.
- Slot booking: nearest-first, DOCK_FULL/DOCK_OFFLINE logged, reservation exclusivity, never zero slots for a returning drone.
- Rotation: relief launched at ≤ 48%, on station before RTB, thermal penalty, pilot preference.
- Backfill on diversion, same tick.
- Relay: trigger ≤ 45%, sprint, handoff ≤ 0.15 km, gap 0.
- Red zone: boundary hold, no position inside ever.
- Weather: grounding, RTB, no launch, reopen.
- Every reason code has at least one passing and one rejecting test.
- Recording derived state: on only inside open geofence, off in NR-HOSPITAL, every transition logged.
- `request_face_id` → PRIVACY_POLICY always, logged.
- Determinism: two runs, same seed → identical event logs (hash).

## Layer 3 · Golden + baseline
- `scripts/run_golden.sh FRIDAY-RUSH` reproduces `docs/golden-timeline.txt`: same event lines in order, times ±0.1 min, batteries ±1%. Test diff ignores formatting.
- `scripts/run_baseline.sh FRIDAY-RUSH` → managed beats baseline on every scorecard line.
- QUIET-SHIFT golden: coverage ≥ 95%, 0 violations.
- Invariants checked every tick in all runs: landing ≥ 25%, slot exclusivity, ≤ 4 per pilot, no red-zone position, ready floor ≥ 3, no recording outside geofence.

## Layer 4 · API / CLI / World Bot
- Every CLI command: ok, rejected (exit 2 + code), error (exit 1).
- Role checks: no agent token can approve (FORBIDDEN_ROLE). Human token via World Bot only.
- Leak test: grep every API response in a golden run for future event times, suspect future waypoints, baseline internals → none.
- FakeRoom end-to-end: approvals by stdin, cards rendered, Decisions sink records.

## Layer 5 · Agents headless (`pytest -m agents`, costs tokens)
The 10 cases in `05-AGENTS.md`. Each asserts: format regex matches, only allowed CLI calls, correct code quoted, no number not present in CLI output (number-provenance check). 3 consecutive passes. Plus a full headless FRIDAY-RUSH through FakeRoom → `runs/headless-<ts>.md` transcript + scorecard.

## Layer 6 · Live (G3)
In Switch: accident beat end to end; chase approval from Discord shows on dashboard and as a Decisions entry; face-recognition refusal live; NO_FLY_ZONE live; QUIET-SHIFT 2 sim-hours quiet. Message counts logged.

## Layer 7 · Ship (G4)
Fresh clone → README steps work on a clean machine in < 15 min (without Switch); video numbers equal scorecard; repo has no tokens (`git grep -i token` reviewed); licences noted.

## Format regexes
`agents/harness/formats.py` holds one regex per format in 04 §4.4; also run on hand-written sample messages without LLM calls.
