---
description: Full health check of the repo against the docs
---
1. `uv run pytest -q`, `uv run python tools/validate_world.py`, `uv run python tools/fleet_check.py --quiet`.
2. Golden FRIDAY-RUSH twice: matches `docs/golden-timeline.txt`, identical event-log hash.
3. Baseline twice: identical KPIs; print them next to managed.
4. CLI spot checks: at 17:52 `airgrid privacy check --request "face recognition on the crowd"` → PRIVACY_POLICY; at 17:55 a route through RED-AIRFIELD → NO_FLY_ZONE; at 18:04 a 5th drone for kazum → PILOT_SATURATED; leak grep over all endpoints.
5. Run the `reviewer` subagent over the repo.
Report a PASS/FAIL checklist with evidence.
