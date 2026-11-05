# Phase 04 · Incidents, ground units, triggers, baseline, KPIs (Nov 2)
Read: `docs/03-SIM-SPEC.md` §7–9 · `docs/07-SCENARIOS.md` · `scenarios/triggers.yaml` · DECISIONS L24.
Subagents: `sim-engineer`, `test-engineer`.

Build:
- `sim/incidents.py` (CAD alerts, geofences, feed facts revealed only once a drone is on station), `ground.py` (units, Manhattan movement, on-scene timers, early release), `units_golden` in the golden run.
- `POST /trigger` for every entry in `triggers.yaml`; each spawns events at "now"; VIP zone takes P1 out of use.
- `sim/baseline.py` (patrol cars + one van team) → `scripts/run_baseline.sh`; `/kpi` with managed vs baseline.
- QUIET-SHIFT golden.
- Replace every `[sim]` ground-unit number in docs with measured values and log it.

Gate: scorecard prints managed vs baseline; managed wins every line; triggers each produce their effect in a test; invariants pass over 20 seeds with random triggers. Commit.
