# Phase 07 · Dashboard (parallel lane from Oct 31; final pass Nov 6)
Read: `docs/06-UI-UX.md` · `docs/04-CONTRACTS.md` §4.5.
Subagent: `dashboard-builder` only. Folder: `dashboard/` only.

Build in order (06 §Build order): static map from `/world` + recorded replay → fleet board + KPI strip → SSE live → incident panel + privacy ticker → controls + triggers → scorecard → polish.
Until Phase 02 passes, drive it from the checker's replay: `uv run python tools/fleet_check.py --quiet --dump-jsonl dashboard/fixtures/golden.jsonl` (one frame per 0.5 sim-min: drones, docks, grounded sectors, suspect while in view, plus the event log as the last line). Serve it with a tiny replay mode in the page (`?replay=fixtures/golden.jsonl`).

Gate: Playwright screenshots at 1920 and 1440 for 17:13:54 (eyes-on), 17:48 (handoff), 17:55 (red-line hold), 18:25 (weather gap), 20:00 (scorecard). No console errors. kazum looks at them. Commit.
