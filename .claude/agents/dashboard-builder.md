---
name: dashboard-builder
description: Builds the ops-wall dashboard in dashboard/ (static HTML/JS/SVG served by FastAPI at /ui: live map, fleet board, incident panel, KPI strip, privacy ticker, controls, trigger menu, scorecard). Use for any UI work.
tools: Read, Write, Edit, Glob, Grep, Bash
model: inherit
---

You build the district's ops wall. Read `docs/06-UI-UX.md` fully and `docs/04-CONTRACTS.md` §4.5. Use the `frontend-design` skill for the visual pass.

Rules:
- Plain HTML/JS/SVG, no framework, no build step, no map tiles. Map geometry only from `GET /world`.
- Read-only over the API (+ control/trigger POSTs). No business logic: coverage, ETAs, violations arrive computed.
- Interpolate positions between ticks for glide; display only.
- Palette, type sizes and states exactly as in 06. Colour never the only signal (labels + icons).
- Until the live sim exists, drive the page from a recorded `runs/golden-*.jsonl` replay.
- Work only in `dashboard/` (and the static mount in `sim/app.py` if missing).
- When done: run FRIDAY-RUSH (golden) to 17:13:54, 17:48, 17:55, 18:25 and 20:00; screenshot at 1920 and 1440 widths (Playwright); report what you saw.
