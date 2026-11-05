# HUMAN TODO (kazum)

## Oct 30
- [ ] Discord server **AirGrid — Northgate (simulated)**, category OPS: `#dispatch` `#fleet-hangar` `#airspace` `#incident-1` `#incident-2` `#field-units` `#privacy-log` `#shift-report` + private `#test-room`. Pin the Rules text from `docs/05-AGENTS.md` in each.
- [ ] Docker Desktop + Switch Console (Windows), Discord bridge. Post one message in each channel (on Discord, posting creates the Switch room).
- [ ] Hello-agent: one Claude Code agent posts a one-line intro in `#test-room`. Screenshot → `docs/switch-proof.png`.

## Oct 31
- [ ] L14/L26 probe in `#test-room`. Answer each and write the results under L14 in `docs/DECISIONS.md`:
  1. Can an agent create and then update a room document ("Flight authorisation log")? (`@agent create a room document called ...`)
  2. Can you see that document's content on screen (Gateway / Console)? Screenshot if yes.
  3. Does `!list-documents` in Discord list it?
  4. Can a custom client (registered agent under your user) post into the room? (Defer to Phase 08 if Console makes this awkward.)
- [ ] Start the dashboard lane in a second Claude Code session: `Read CLAUDE.md and docs/06-UI-UX.md. Run /phase 07 (dashboard lane). Work only in dashboard/.`

## Open
- [ ] Switch Console → Your Rooms → test-room: can you open "Flight authorisation log" and read both lines? Screenshot or "can't find it".

## Phase 08 (live Switch)
Run from the repo in Git Bash. ⚠ marks what no doc confirms; the list is at the end.

1. Gateway → **API Keys**: create an API key of type `registration`. You own what it registers.
2. Find the bridge URL: `curl -i http://localhost:56020/health`. 200 is right. 401 means right host, wrong path. The bridge is often not the Gateway URL, so if this fails, use the bridge address from your Console's server settings.
3. Register the bot. The key goes in the environment, never on the command line; `read -rs` hides it:
   ```
   read -rs AIRGRID_SWITCH_REG_KEY; export AIRGRID_SWITCH_REG_KEY
   uv run python scripts/register_worldbot.py --url http://localhost:56020
   unset AIRGRID_SWITCH_REG_KEY
   ```
   This writes gitignored `config/switch.local.yaml`.
4. In each of the 9 rooms, from Discord: `!invite-agent @airgrid-worldbot`.
5. `scripts/install_agents.sh` (if `config/tokens.local.yaml` is missing, `scripts/make_tokens.sh` first).
6. ⚠ Put `_bin` on PATH: add `%USERPROFILE%\airgrid-agents\_bin` to your Windows user PATH (`uv` must be on it too), then quit Switch Console fully and reopen it. Docs: Console reads PATH once at start and passes agents only a fixed set of variables. There is no env or PATH setting on an agent, and Console doesn't read `.env`. That's fine for the rest: the `airgrid` wrapper reads `.env` from the agent's directory itself.
7. Switch Console → **Your Agents**: create 7 agents. Directory is fixed at creation, so use the full path.
   | Name | Directory | Provider | Who can talk to your agent |
   |---|---|---|---|
   | `dispatch` | `~/airgrid-agents/dispatch` | Claude Code | Me and my agents |
   | `fleet` | `~/airgrid-agents/fleet` | Claude Code | Me and my agents |
   | `airspace` | `~/airgrid-agents/airspace` | Claude Code | Me and my agents |
   | `intel` | `~/airgrid-agents/intel` | Claude Code | Me and my agents |
   | `relay` | `~/airgrid-agents/relay` | Claude Code | Me and my agents |
   | `privacy` | `~/airgrid-agents/privacy` | Claude Code | Me and my agents |
   | `comms` | `~/airgrid-agents/comms` | Claude Code | Me and my agents |

   Pick **Me and my agents**, not **Only me**: "Only me" shuts out the World Bot and the other agents.
8. Add each agent to its rooms per `agents/switch/agent-rooms.yaml`: `!invite-agent @dispatch` in `#dispatch`, `#incident-1`, `#incident-2`, and so on.
9. Each room in Switch Console → **Configuration** → Instructions: paste `agents/switch/rooms/<room>.txt` (9 rooms).
10. Create `config/humans.yaml` from `config/humans.example.yaml` with your Discord name, if you haven't.
11. Get your sender id:
    ```
    uv run python -m worldbot.switch_room --who --seconds 180
    ```
    Say anything in `#test-room` from Discord. Copy the `switch_sender:` value into `config/humans.yaml`. If you'll also approve from Console, say something there too: that's a second id, so use a list. Rerun: it must show `mapped to <you>`.
12. Start: `scripts/run_demo.sh --switch` (`--no-start` leaves the clock stopped).
13. Smoke in `#test-room`: `@dispatch run airgrid status and reply with the clock`. If `airgrid` isn't found, step 6 didn't work. Fallback: instruction files call `../_bin/airgrid` (from the agent's directory); ask Claude Code to change them, rerun `uv run pytest -m agents`, then `scripts/install_agents.sh`.
14. ⚠ If Auto mode posts a permission request for `airgrid` calls, choose: turn on **Bypass permissions** for these seven agents, or ask Claude Code for an allow-rule route.
15. In `#dispatch`, type `approve A-9999`: from you it must answer `NOT_FOUND`; from a second, unmapped account `FORBIDDEN_ROLE`.
16. Check for the log: `!list-documents` in `#dispatch` should show "Flight authorisation log" after the first decision. The docs say Console has no document page, so the video beat stays on `#privacy-log` + `airgrid decisions` (L26) unless you can read it on screen.

⚠ Unconfirmed until run live:
- `_bin` on PATH in Console sessions (steps 6, 13) and Auto mode's handling of `airgrid` (step 14).
- Argument names of `list_rooms`, `connect_to_room` and the document ops: from the public repo's `main`, not the docs, and possibly newer than your release. The bot checks them against `GET /ops` at start.
- One `scope=all` connection that claims `#dispatch` with `rooms=` still covering the other rooms: read from repo source, no docs page states it. If `#fleet-hangar` messages don't reach the bot, this is why.
- Whether `@dispatch` inside text sent by `POST /message` addresses the agent. Docs say so for `post_message`. If agents stay silent on alerts, this is why.
- Room names "AirGrid: dispatch". A different form stops the bot at start and names the rooms.

## Game mode (quiet Discord, optional)
The game plays without any of this. Requires Phase 08 steps 1–3, 7, 10, 11.

1. In Discord, create `#ops` and post one message in it (posting creates the Switch room).
2. In `#ops`: `!invite-agent @airgrid-worldbot`, then `@dispatch`, `@intel`, `@airspace`, `@privacy`, one command each.
3. In `#privacy-log`: `!invite-agent @airgrid-worldbot` and `!invite-agent @privacy`, if not already there.
4. Switch Console → `#ops` → **Configuration** → Instructions: paste `agents/switch/rooms/ops-game.txt`.
5. Run `scripts/run_game.sh --game --discord` and open the printed URL. The key in it is the operator key: don't share it.

## City mode (default demo)
PowerShell, from the repo. The city runs without any of this: Discord is the commentary. Needs Phase 08 steps 1–3, 10, 11.

1. Add tokens for the three new agents and reinstall:
   ```powershell
   $bash = "C:\Program Files\Git\bin\bash.exe"
   & $bash scripts/make_tokens.sh --missing
   & $bash scripts/install_agents.sh
   ```
2. In Discord, create `#ops` if it doesn't exist and post one message in it.
3. Switch Console → **Your Agents**: create 3 agents, provider Claude Code, **Me and my agents**:
   | Name | Directory |
   |---|---|
   | `fire-station` | `C:\Users\Aadarsh\airgrid-agents\fire-station` |
   | `hospital` | `C:\Users\Aadarsh\airgrid-agents\hospital` |
   | `police` | `C:\Users\Aadarsh\airgrid-agents\police` |
4. In `#incident-1` and again in `#incident-2`, send each on its own line: `!invite-agent @airgrid-worldbot`, `!invite-agent @dispatch`, `!invite-agent @fire-station`, `!invite-agent @hospital`, `!invite-agent @police`. In `#ops`, invite the same five if they aren't there.
5. Switch Console → **Configuration** → Instructions: paste `agents/switch/rooms/ops.txt` in `#ops`, and `agents/switch/rooms/incident-city.txt` in both incident rooms.
6. `config/humans.yaml` already gives kazum the role `incident_lead` (for a teammate later, move it to them; they need a `switch_sender` too). So the YOUR CALL line reaches you, add `discord_mention: "<@YOUR_DISCORD_USER_ID>"` beside `discord:` (Discord → Developer Mode → right-click your name → Copy User ID). ⚠ Whether Switch passes `<@id>` through to Discord as a ping is unconfirmed; if it shows as text, try `@yourname`.
7. Run it: `& $bash scripts/run_game.sh --discord`, then open the printed URL. Don't share it: the key in it is the operator key.
8. Trigger a fire. `#ops` gets the start and the report; 🙋 your calls come in the incident room (`config/humans.yaml` now also gives kazum `duty_doctor`; an injured accident adds a `transport` or `treat` call). Crews talk in the incident room. Type `leave` or `stay`, no @mention, in the incident room. Also try `@dispatch ask police to stay until the tow arrives` in the incident room.

   If agents are silent on handoffs from each other, check each is on **Me and my agents** (the bot and all four must have one owner).

⚠ Unconfirmed live: the same list as Phase 08, plus step 6.

