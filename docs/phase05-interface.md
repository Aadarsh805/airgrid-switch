# Phase 05 interface (integration-engineer builds, test-engineer tests against it)

Sources: prompts/05-cli-worldbot.md · 04-CONTRACTS §4.1–4.5 · 05-AGENTS · 12-RUNBOOK · DECISIONS L14, L26, L31–L40.

## CLI (`cli/airgrid.py`)
- Every command in §4.1, same flags. Env `AIRGRID_API_URL`, `AIRGRID_ROLE`, `AIRGRID_TOKEN`.
- Compact JSON by default, `--text` for one line. Exit 0 ok · 2 rejected (prints `{"ok":false,"code","reason","hint"}`) · 1 error (unreachable, malformed, 5xx).
- HTTP mapping: 2xx → 0; any response with a §4.2 `code` (401/403/404/409) → 2; everything else → 1.
- Thin client: no rule logic, no state. Sim-side gaps found while wiring a command are fixed in `sim/` as read endpoints or action mappings, without changing the golden output.
- `plan rotation`, `relay plan`, `route check` and `track status` are read-only projections of the present state. They never expose future events or the suspect's future path. `relay plan` uses no suspect prediction beyond what's served now.

## World Bot (`worldbot/`)
- `RoomTransport` (abstract): `post(room, text) -> msg_id`, `messages() -> async iterator of RoomMessage(room, author, text)`.
- `FakeRoom`: prints `[#room] text`, reads stdin lines `author: text`, or takes a script (list of `(sim_time, author, room, text)`) for tests.
- `SwitchRoom`: stub raising NotImplementedError (Phase 08).
- `bot.py`: subscribes to `/events/stream`. Posts event alerts, duty-pilot and radio lines, and approval cards in §4.4 formats to the owning room. On each event it @mentions the owning agent (Switch agents act only when addressed).
- Approvals: `approve A-0007` / `reject A-0007 <reason>` from a human mapped in `config/humans.yaml` → sim call with that human's token. Unmapped or wrong-role author → a refusal line, no sim call. Never an approval path for an agent.
- Result lines `✅ APPROVED …` / `❌ REJECTED …` per §4.4.
- `DecisionSink` (abstract) with `FakeSink` (records to memory and `runs/`), `record(entry)` on every approval, rejection and refusal. Real sink is Phase 08 (L40: one writer owns the document).
- `lines.py`: templates only, labelled simulated. No LLM.
- Agent activity: `POST /control/agent-activity {agent, state}` releases demo holds (max 45 s). Add it to the sim if missing.
- Room routing table lives in `worldbot/rooms.py` (event type → room, owning agent).

## Tokens
`scripts/make_tokens.sh` wraps `python -m sim.auth --init`, writing gitignored `config/tokens.local.yaml`. Tokens never printed or logged.

## /demo-run
`scripts/demo_run.py` (or the `/demo-run` command): FRIDAY-RUSH in demo logic at headless speed with FakeRoom, kazum played from golden actions via `approve`/`FO:` messages, runtime agents as stubs. Saves `runs/demo-dryrun-<ts>.md`: timeline, every approval + decision, every refusal + code, simulated lines, final KPIs vs baseline, diffs from `docs/golden-timeline.txt`.

## Gate
CLI ok/rejected/error test per command · leak grep over all endpoints and CLI output across a golden run · FakeRoom end-to-end FRIDAY-RUSH reproduces the golden outcome with approvals through chat · dry-run report saved.
