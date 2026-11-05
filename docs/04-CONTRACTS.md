# 04 · Contracts (lock first; change only with a DECISIONS entry)

## 4.1 `airgrid` CLI (what agents call)
Env: `AIRGRID_API_URL` (default `http://localhost:8740`), `AIRGRID_ROLE`, `AIRGRID_TOKEN`. Compact JSON by default, `--text` for one line. Exit 0 ok · 2 rejected (`{"ok":false,"code","reason","hint"}`) · 1 error.

### Read (all agent roles)
```
airgrid status                      # clock, mode, scenario, pending approvals, KPI strip
airgrid fleet [--id D-09]           # drones: state, battery, pos, mission, pilot, slot booked
airgrid docks                       # slots FREE/OCCUPIED/RESERVED/OFFLINE
airgrid pilots                      # load per pilot (n/4)
airgrid incidents [--id INC-2]      # open incidents, geofence, drones, units, authorisations, feed facts so far
airgrid airspace                    # zones (active?), weather per sector
airgrid units                       # ground units, status, ETA
airgrid events --since 17:40
airgrid approvals [--status pending]
airgrid privacy-log [--since 17:00]
airgrid kpi                         # scorecard data (+ baseline if run)
airgrid decisions                   # sim decision log (for the shift report)
```

### Role commands
```
# dispatch
airgrid incident open --from-alert INC-2 | airgrid incident close --id INC-2 --note "..."
airgrid launch --drone D-05 --mission incident --incident INC-1 --to 0.75,2.2 --why "upstream queue"   # optional: --pilot DP-2 --for-min 20 (L41)
airgrid propose standing-policy --scope "auto-launch accident/fire/flood this shift"
airgrid divert --drone D-13 --incident INC-2 --mission track
airgrid track assign --drone D-13 --incident INC-2
airgrid propose track --incident INC-2 --scope "suspect vehicle only; district; 30 min"
airgrid propose broadcast --drone D-09 --text "Lane 2 closed, keep left"
# fleet
airgrid plan rotation [--loop NORTH]          # shows the sim's next reliefs (read-only)
airgrid rtb --drone D-09 [--dock HUB-B]       # slot booked first; rejected DOCK_FULL/OFFLINE if you force a dock
airgrid propose out-of-service --drone D-11 --why "motor temp"
# airspace
airgrid route check --drone D-04 --to 5.45,4.45     # zones crossed, recording on/off, ETA
airgrid hold --drone D-12 --at-boundary RED-AIRFIELD
airgrid corridor --drone D-04 --incident INC-2 --to 5.45,4.45
airgrid propose zone --id VIP-TEMP --until 21:00
# intel
airgrid feed --incident INC-1                 # structured facts from drones on station (no identities)
airgrid evidence tag --incident INC-1 --clip C-3 --what "lane 2 blocked"
# relay
airgrid track status --incident INC-2         # tracker, battery, next relief, handoff ETA
airgrid relay plan --incident INC-2           # read-only projection
# privacy
airgrid propose footage-release --incident INC-1 --clip C-3 --to "traffic court"
airgrid privacy check --request "<text>"      # classifies; face ID → PRIVACY_POLICY
airgrid set-recording --drone D-09 --on|--off   # on outside a geofence or in NR-HOSPITAL → PRIVACY_POLICY (L45)
airgrid release-footage --approval A-0005     # after the card is approved; else AUTH_REQUIRED (L45)
# comms
airgrid unit task --unit patrol_car_2 --incident INC-1
airgrid unit release --unit patrol_car_2 --incident INC-1 --evidence "D-09 feed: 2 minor, walking"   # must quote a full revealed fact (L38)
airgrid advisory --area J9 --text "Ring Road eastbound lane 2 closed" --until 18:00
# any agent
airgrid log-refusal --about "face recognition on the crowd" --code PRIVACY_POLICY --by kazum   # optional: --drone --pilot --fallback-pilot (L42)
airgrid simulate <same args as a write>       # what-if: result + KPI delta, changes nothing
```
Human-only (World Bot, human token): `approve <id>`, `reject <id> <reason>`, `take-control <drone>`, `release-control <drone>`.

## 4.2 Reason codes (the only ones)
`BATTERY_RESERVE` · `DRONE_NOT_READY` · `NO_FLY_ZONE` · `WEATHER_GROUNDED` · `DOCK_FULL` · `DOCK_OFFLINE` · `PILOT_SATURATED` · `SEPARATION` · `OUT_OF_RANGE` · `AUTH_REQUIRED` · `PRIVACY_POLICY` · `MISSION_EXPIRED` · `FORBIDDEN_ROLE` · `NOT_FOUND`

```json
{"ok": false, "code": "NO_FLY_ZONE",
 "reason": "Route D-12 → (5.8, 3.4) crosses RED-AIRFIELD (no override)",
 "hint": "airgrid hold --drone D-12 --at-boundary RED-AIRFIELD; airgrid corridor --drone D-04 --to 5.45,4.45"}
```

## 4.3 Roles and approvals (`config/roles.yaml`, `config/humans.yaml`)
| Agent role | Writes |
|---|---|
| `dispatch` | incident open/close, launch (incident/perimeter), propose track/broadcast |
| `fleet` | rtb, propose out-of-service |
| `airspace` | hold, corridor, propose zone |
| `intel` | evidence tag |
| `relay` | (read-only + log-refusal; relay is sim policy) |
| `privacy` | propose footage-release, privacy check |
| `comms` | unit task/release, advisory |
| any | log-refusal, simulate |

| Approval | Human role |
|---|---|
| track, loudspeaker, footage release, VIP zone, divert off incident, out of service | `watch_commander` |
| take-control / release-control | `remote_pilot` |
| request eyes / units ("need eyes on…") | `field_officer` (a request, not an approval) |

Solo: kazum holds all three. Team: edit `config/humans.yaml` only.

## 4.4 Room message formats (fixed; wording inside may vary)
```
Dispatch:  🚨 INCIDENT | INC-<n> <kind> | <place> | P<1-3> | report: <one line>
           🚀 LAUNCH | <drone> → INC-<n> <mission> | ETA <m:ss> | battery <n>% | pilot <who> | basis: <auto-policy / approval A-<id> @kazum>
           👁 EYES-ON | INC-<n> | <drone> on station <time> (<s> s after alert)
Fleet:     🔋 FLEET | airborne <n> · ready <n> · charging <n> · slots free <n>/18 | tightest: <drone> <n>%
           🔄 ROTATION | <relief> takes <LOOP> from <drone> (<n>%) | relief on station before RTB | coverage continuous
           🅿 SLOT | <drone> → <dock> | <DOCK_FULL at X / DOCK_OFFLINE at X / home> | lands ~<time> at ~<n>%
Airspace:  🧭 ROUTE | <drone> | <from> → <to> | zones: <clear / NR-HOSPITAL transit, recording OFF> | ETA <m:ss>
           ⛔ ROUTE DENIED | <drone> | crosses RED-AIRFIELD | reason: NO_FLY_ZONE | holding at boundary | alternative: <corridor plan>
           🌬 WEATHER | <sectors> wind <n> m/s (limit 12) | grounded until <time> | reason: WEATHER_GROUNDED | coverage gap: <loops>
Intel:     📋 SITREP | INC-<n> | <vehicles / injuries / lanes / smoke / suspect vehicle> | confidence: direct/partial
           🎥 EVIDENCE | INC-<n> | clip <id> | <what> | inside geofence ✓ | persons identified: none (capability absent)
Relay:     🎯 TRACK | INC-<n> | target: <vehicle> | held by <drone> (<n>%) | auth A-<id> @kazum, expires <time>
           🤝 HANDOFF | <A> → <B> | reason: <A> at <n>% | gap 0 s
Privacy:   🔐 AUTH | <what> approved by @kazum | scope: <scope> | expires <time> | logged
           ⛔ PRIVACY REFUSAL | request: <text> | by: <who> | reason: PRIVACY_POLICY | lawful alternative: <one line>
           📼 FOOTAGE | INC-<n> clip <id> | release to <who> | status: <pending A-<id> / released / denied>
Comms:     🚓 UNIT | <unit> → INC-<n> | ETA <m:ss> | <en route/on scene>
           ✅ RELEASED | <unit> from INC-<n> | evidence: <feed fact> | saves ~<n> min
           📣 ADVISORY | <area> | <text> | until <time>
           📊 SHIFT REPORT | flights <n> · eyes-on <s> s · units released <n> · coverage <n>% · reserve breaches 0 · red-zone 0 · refusals <n>
Any:       ❌ REJECTED | action: <action> | reason: <CODE> | instead: <lawful alternative / escalated to @kazum>
World Bot: 📣 <EVENT> · <what> · <room>
           🟡 APPROVAL A-0007 · <track/broadcast/...> · by @<agent> · needs: watch_commander
              <one-line summary> · Reply: approve A-0007 | reject A-0007 <reason>
           ✅ APPROVED A-0007 by @kazum · <sim result>   /   ❌ REJECTED A-0007 by @kazum: <reason>
           🛩 DP-1 Asha Menon (duty pilot, simulated): "<line>"
           📻 <unit> (simulated): "<line>"
```
Rules: numbers only from `airgrid` output · one message per step · end with owner or next step · speak only if you own the event or are @mentioned.

## 4.5 Sim API
```
GET  /world (static geometry)  /state  /status  /fleet[/id]  /docks  /pilots  /incidents[/id]  /airspace  /units
GET  /events?since=  /events/stream (SSE)  /approvals?status=  /privacy-log  /kpi  /kpi/history  /decisions
POST /actions               {role, action, args}      (agent token, role-checked)
POST /approvals             (agent proposals)
POST /approvals/{id}/approve | /reject                (human token only)
POST /control/{start|pause|resume|reset|speed|skip}
POST /scenario/load {name, seed, mode}    POST /trigger {kind, at}    POST /baseline/run
GET  /ui  (dashboard static files)
```
Never in any response: future events, suspect path ahead of now, baseline internals during a managed run.

## 4.6 Database (SQLite)
```sql
drones(id, home, thermal, state, battery, x, y, mission, incident, loop, pilot, slot, booked_slot, flights)
docks(id, x, y, slots, online) ; slot_state(dock, slot, drone, status)
pilots(id, name, kind, max_drones) ; zones(id, kind, rect_json, active, until)
weather(sector, wind_ms, grounded, until)
incidents(id, kind, x, y, priority, status, geofence_r, opened_min, closed_min)
authorisations(id, incident, kind, scope, approved_by, approved_min, expires_min, status)
feed_facts(incident, sim_min, fact_json) ; units(id, kind, x, y, status, incident, eta_min)
approvals(id, type, created_by, payload, status, hold_clock, decided_by, reason, created_min, decided_min)
privacy_log(seq, sim_min, drone, incident, kind, detail, actor)     -- append-only
decisions_log(seq, sim_min, actor, action, approval_id, code, rationale, kpi_before, kpi_after, switch_entry_id)
events(seq, sim_min, type, payload) ; kpi_snapshots(sim_min, payload) ; runs(id, scenario, seed, mode, final_kpi)
```

## 4.7 Scenario format
See `scenarios/friday_rush.yaml`: `name, seed, start, end, initial{airborne, docked}, events[{t, type, …}], suspect_path[{t, at}], golden_actions[{t, act, …}], feed[{incident, t, fact}]`. Event types: `shift_brief, cad_alert, dock_fault, dock_restored, weather, unit_radio, trigger`. Times `"HH:MM"` or `"HH:MM.m"`.
