# Phase 03 · Rules and validated actions (Nov 1)
Read: `docs/03-SIM-SPEC.md` §4–6 · `docs/04-CONTRACTS.md` §4.1–4.3 · DECISIONS L19.
Subagents: `sim-engineer`, `test-engineer`.

Build:
- `sim/rules.py`: one pure validator per code in §4.2.
- `sim/actions.py`: every action in 03 §6 via `POST /actions` (role-checked), `simulate` what-if (no effect, returns KPI delta).
- `sim/privacy.py`: recording derived state, geofences, NR-HOSPITAL, privacy_log append-only, `request_face_id` always PRIVACY_POLICY + log.
- Approvals table + `POST /approvals`, `/approve`, `/reject` (human token only); `hold_clock`; authorisations with scope + expiry → MISSION_EXPIRED.
- `decisions_log` row for every write and every rejection.

Gate: every reason code has a rejecting and a passing test; the FRIDAY-RUSH golden still matches; at 17:52/17:55/18:04/18:08 the golden refusals come from the real validators (not scripted strings). Commit.
