# Phase 02 · Sim core: world, fleet, docks, policies, clock (Oct 30–31)
Read: `docs/03-SIM-SPEC.md` §1–3, §9–11 · `docs/04-CONTRACTS.md` §4.5–4.7 · `tools/fleet_check.py` · `docs/golden-timeline.txt` · DECISIONS L6–L12, L21–L23.
Subagents: `sim-engineer` (sim/), `test-engineer` (tests/).

Build:
- `sim/world.py` (load + validate YAML), `geo.py`, `fleet.py` (battery, movement, launch delay), `docks.py` (slots, reservations, booking with DOCK_FULL/OFFLINE logs), `pilots.py` (caps), `airspace.py` (zones, boundary point), `weather.py`, `suspect.py` (interpolation, current position only), `policies.py` (auto-launch, rotation, backfill, relay, safety RTB, grounding, incident close), `clock.py` (tick, demo holds, skip), `scenario.py`, `golden.py` (applies golden_actions), `kpi.py`, `events.py`, `db.py`.
- Tick order identical to the checker. Invariants every tick.
- `/world`, `/state`, `/fleet`, `/docks`, `/pilots`, `/events`, `/events/stream`, `/control/*`, `/scenario/load`.
- `scripts/run_golden.sh FRIDAY-RUSH` → `runs/golden-<ts>.txt` in the checker's line format + `runs/golden-<ts>.jsonl` (state per tick, for the dashboard replay).
- Tests: Layer 2 items for battery, movement, slots, rotation, backfill, relay, red zone, weather, determinism.

Gate:
- Golden output diff vs `docs/golden-timeline.txt`: same lines in order, times ±0.1 min, batteries ±1%. Quote the diff summary.
- Determinism: two runs, same hash.
- ⛔ **G1:** stop. kazum reads the golden output next to the checker's and signs off in PROGRESS.md.
