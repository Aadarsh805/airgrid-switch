# Phase 03 interface (sim-engineer builds, test-engineer tests against it)

Contract sources: 04-CONTRACTS §4.2 (codes), §4.3 (roles/approvals), §4.5 (API), §4.6 (schema); 03-SIM-SPEC §4–6; `config/roles.yaml`. Decisions: L31.

## Auth
- Header `Authorization: Bearer <token>`. Tokens file: `AIRGRID_TOKENS` env, default `config/tokens.local.yaml` (gitignored):
  ```yaml
  agents: {dispatch: <tok>, fleet: <tok>, airspace: <tok>, intel: <tok>, relay: <tok>, privacy: <tok>, comms: <tok>}
  humans: {kazum: <tok>}
  ```
- Human roles from `AIRGRID_HUMANS` env, default `config/humans.yaml`, falling back to `config/humans.example.yaml`.
- `python -m sim.auth --init` writes a tokens file with random tokens (Phase 05's `make_tokens.sh` wraps it).
- Missing or unknown token → 401 `FORBIDDEN_ROLE`. Agent token whose role isn't allowed the action → 403 `FORBIDDEN_ROLE`. Any agent token on approve/reject/take_control/release_control → 403 `FORBIDDEN_ROLE`.

## Responses
- ok: 200 `{"ok": true, "result": {...}}`
- rule rejection: 409 `{"ok": false, "code": "<§4.2>", "reason": "...", "hint": "..."}`
- forbidden: 401/403 with `FORBIDDEN_ROLE` · unknown drone/incident/approval/dock/zone: 404 `NOT_FOUND` · malformed body: 422 `{"ok": false, "error": "invalid request", ...}`
- Every write and every rejection appends a `decisions_log` row (`GET /decisions`).

## POST /actions `{role, action, args}`
`role` must equal the token's agent role (or be a human role the human holds, for human actions).

| action | args | notes |
|---|---|---|
| launch | drone, mission (incident/perimeter/corridor/patrol/search), incident?, to ([x,y] or landmark), pilot?, band?, for_min?, why? | DRONE_NOT_READY, BATTERY_RESERVE (projected landing after to + for_min + return < 25), WEATHER_GROUNDED, NO_FLY_ZONE (target or straight route crosses RED or active VIP), OUT_OF_RANGE (> 10 km from dock), PILOT_SATURATED, SEPARATION (explicit band and target within 0.1 km of another airborne target in that band), MISSION_EXPIRED (closed incident) |
| divert | drone, incident, mission | off an open incident → AUTH_REQUIRED unless a `divert_off_incident` approval |
| assign_track | drone, incident | AUTH_REQUIRED without an active track authorisation; PRIVACY_POLICY if the incident isn't open; MISSION_EXPIRED if closed or auth expired |
| assign_perimeter | drone, incident, to? | MISSION_EXPIRED on a closed incident |
| corridor_watch | drone, incident, to | NO_FLY_ZONE if the target or route is inside RED |
| hold | drone, at_boundary (zone id) | |
| rtb | drone, dock? | forced dock: DOCK_FULL / DOCK_OFFLINE |
| dock_at | drone, dock | DOCK_FULL / DOCK_OFFLINE |
| take_control / release_control | drone | human `remote_pilot` only; PILOT_SATURATED at 4 |
| broadcast | drone, text | AUTH_REQUIRED without an approved `broadcast` |
| request_footage_release | incident, clip, to | creates an approval |
| release_footage | approval_id | AUTH_REQUIRED unless approved |
| set_recording | drone, on | on outside an open incident geofence or inside NR-HOSPITAL → PRIVACY_POLICY |
| request_face_id | request, by | always PRIVACY_POLICY, logged in privacy_log and decisions_log |
| activate_zone | zone, until | AUTH_REQUIRED without an approved `zone` |
| open_incident / close_incident | from_alert / incident, note | |
| issue_advisory, task_unit, release_unit, retime_signal, stand_down, rotate_patrol | per 03 §6 | minimal now; ground units are Phase 04 |
| log_refusal | about, code, by | code must be in §4.2 |
| simulate | action, args | validates and applies to a deep copy; returns `{would: ok|rejection, kpi_delta}` for the immediate state only (no forward run, so no future leak); changes nothing |

Role → actions mapping: `config/roles.yaml` names map onto these actions in `sim/actions.py` (table in code).

## Approvals
- `POST /approvals` (agent token) `{type, args, summary, hold_clock?}`. `type` ∈ track, broadcast, footage_release, zone, divert_off_incident, out_of_service, standing_policy. Returns `{ok, result: {id: "A-0001", status: "pending"}}`; ids sequential per run.
- `POST /approvals/{id}/approve` (human token, body `{}` or `{note}`) · `/reject` `{reason}`. The human must hold the role `config/roles.yaml` gives that type, else 403 FORBIDDEN_ROLE. Deciding an already-decided approval → 409 MISSION_EXPIRED.
- On approve: track → authorisation `{scope, expires = approved + args.minutes}`; broadcast → loudspeaker runs; zone → zone active until `until`; standing_policy → auto-launch enabled.
- `GET /approvals?status=pending|approved|rejected`.
- Demo/live clock holds while any `hold_clock` approval is pending.
- Auto-launch runs only while the standing policy is approved. The golden runner approves it at 17:00 (A-0001, no timeline line), so loudspeaker = A-0002 and track = A-0003, as in the demo script.

## Privacy
- `recording = drone on incident mission AND inside that incident's geofence (policies.geofence_km, 0.6) or the authorised track AND NOT inside NR-HOSPITAL`. Patrols never record.
- Every on/off transition → `privacy_log` row (append-only; no update/delete path). `GET /privacy-log?since=HH:MM`.
- Invariant: recording outside a geofence → violation.
- Authorisation expiry → tracking drones RTB + MISSION_EXPIRED logged.
- Never a timeline line, so golden stays byte-identical.

## Golden
`sim/golden.py` sends every golden action through `sim.actions.execute(...)` as the right identity. Refusal timeline lines are rendered from the real rejection:
- 17:52 `request_face_id` → PRIVACY_POLICY
- 17:55 tracker `corridor_watch` into RED → NO_FLY_ZONE
- 18:04 launch under kazum → PILOT_SATURATED, then retried under DP-2
- 18:08 `assign_perimeter` D-14 on closed INC-2 → MISSION_EXPIRED

Output must stay 99 lines, 0 diffs, same event_hash, or explain the change.
