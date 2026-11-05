# 06 · UI / UX

## Goal
A city operations wall for one district, readable from 3 m away in a video. Dark control-room cartography: deep slate, muted street greys, one accent for the network, status colours used sparingly. Not military cosplay, not startup neon. If a judge can read the chase off the map without narration, the dashboard did its job.

## Tech
Static `dashboard/index.html` + `app.js` + `map.js` + `style.css`, served by FastAPI at `/ui`. SVG for the map (district geometry from `GET /world`), state from `GET /state` once, then SSE diffs from `/events/stream`. Client interpolates positions between ticks (display only). No framework, no build step, no map tiles. Use the `frontend-design` skill for the visual pass. Playwright screenshots at gates.

## Palette and type
| Token | Hex | Use |
|---|---|---|
| bg | `#0D141B` | page |
| land | `#131E28` | map blocks |
| street / arterial | `#2A3947` / `#3A4E61` | roads |
| panel / hairline | `#16222D` / `#31485C` | boards |
| text / muted | `#E8EEF4` / `#8FA3B5` | |
| accent sky | `#57B7E8` | drones, routes, coverage |
| green | `#2FA36B` | ready / nominal |
| amber | `#D99A26` | charging, caution, NR-HOSPITAL, weather |
| red | `#C8452C` | incident, red zone, violations |
| gold | `#C6A15B` | awaiting a human decision (learn it once) |

System sans, tabular numerals. KPI numbers ≥ 32 px, drone labels ≥ 12 px.

## Layout (16:9, no scrolling)
```
┌ TOPBAR: AIRGRID · NORTHGATE DISTRICT (fictional) · Fri 17:41 · demo ▶ ⏸ ⏩ ↺ · Baseline: off ┐
├──────────────── LIVE MAP (62%) ──────────────────┬──── FLEET BOARD (38%) ───────────┤
│ 6×6 km SVG: streets, J1–J12 labels, queue dots    │ 14 rows: D-xx · state icon ·      │
│ docks as squares with slot pips (hub = 3 pips)    │ battery bar · mission · pilot ·   │
│ drones: dot + label + battery ring + 60 s trail   │ slot booked. Airborne first,      │
│ coverage circles on patrol drones                 │ tightest battery on top           │
│ zones always drawn (red hatch AIRFIELD, amber     ├──── INCIDENT PANEL ───────────────┤
│ NO-RECORD hospital, VIP hatch when active)        │ INC cards: drones, units, auth    │
│ incident markers pulse once; suspect = vehicle    │ (gold while pending, countdown to │
│ icon, only while in view; ground units = squares  │ expiry), recording state          │
├───────────────────────────────────────────────────┴──────────────────────────────────┤
│ KPI STRIP: eyes-on last · units released · coverage % · reserve breaches 0 · red-zone 0 │
│            airborne n · ready n · slots free n/18 · pilot load kazum n/4               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ PRIVACY TICKER: 🔐 17:41 track approved @kazum (INC-2, exp 18:11) · 17:52 PRIVACY_POLICY │
│ FOOTER: Fictional district · simplified rules modelled on real DFR practice · simulated │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## Map details
- Dock pips fill/empty as drones land/launch (the PATROL → RETURN → LAND → READY → RECHARGE loop, visible). Reserved slot = hollow pip with accent ring. Offline dock = grey with a slash.
- Coverage circles and the KPI coverage % agree (both from `/state`). At 18:20 the north loop's circle disappears and stays gone until 18:50.
- Chase: tracker and suspect ribbons converge; at the 17:48 handoff the ribbon continues in the relief drone; at the red line the trail stops and waits while D-04's trail picks the suspect up north of the zone.
- No fake video, no camera views. The "feed" is Intel's text in Discord.

## States
1. Calm (17:00): three patrol ribbons, charge pips ticking.
2. Incident: marker pulse, drones outlined, card opens, gold until decided.
3. Chase: gentle zoom-to-fit on track, auth countdown (gold → amber at T-5 min).
4. Weather: grounded sectors desaturate, RTB lines home, coverage drops and stays dropped.
5. Scorecard (20:00 or toggle): managed vs baseline over a dimmed map, screenshot-clean.
6. Scenario + trigger picker (pre-demo, or one fire trigger on camera).

## Controls and triggers
Run / pause / ⏩ skip / reset / speed (0.5, 1, 2 sim-min/s) / baseline toggle. Trigger menu: fire, missing child, underpass flood, drone fault, unauthorised drone, VIP zone, manual officer request. Triggers go through `POST /trigger`, logged as events.

## Discord UX
- Room names exactly as in 05. Pinned message = Rules text.
- Agent names + emoji: 🚀 Dispatch, 🔋 Fleet, 🧭 Airspace, 📋 Intel, 🎯 Relay, 🔐 Privacy, 📣 Comms. World Bot 📣. Simulated voices labelled.
- Approval is a chat message: card → "approve A-0003". No buttons.
- Terse, numbers first, no banter.
- Video: sim clock always on screen; ⏩ marker whenever skipped; caption "recorded from a live run, seed DRONE-01".

## Build order (dashboard lane starts Oct 31)
1. Static map from `/world` + drones from a recorded `runs/golden-*.jsonl` replay (no live sim needed).
2. Fleet board + KPI strip.
3. SSE live.
4. Incident panel + privacy ticker.
5. Controls + triggers.
6. Scorecard state.
7. Polish (trails, zoom, pulses) — cut item 6.
