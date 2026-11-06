# AirGrid

**A city's drone response team, run from team chat.** Drones patrol a fictional district. When something goes wrong, AI agents for Drone Ops, Fire Station, Hospital and Police coordinate in Switch rooms, and the people on the team make the calls in the same chat.

Built for **Flip the Switch** (Nov 4–7, 2026), Best Business Workflow track.

> AirGrid is a training and prototyping simulator. The district, agency and every number on screen are simulated. No real drones, no real dispatch, no identification of people.

![AirGrid city mode](docs/city-two-incidents.png)

## The problem
Cities already send drones first to emergency calls. Chula Vista's programme has flown more than 25,000 calls, with the drone first on scene in about 97 seconds on average. The hard part isn't flying; it's coordination. Fire, police and medical teams each hold part of the picture, decisions get made by phone, and afterwards nobody can show who decided what.

AirGrid puts that coordination in one team chat: the agents do the legwork, the people decide, and the record writes itself.

## What happens
1. Drones roam the city on patrol, always moving.
2. A fire or accident starts. Either it's reported (**Simulate fire**, **Simulate accident**) or a patrolling drone finds it with its simulated thermal sensor (**Random fire**).
3. The nearest drone gets eyes on and tells the right service in the incident room. Engines, police cars and ambulances drive there on the roads, and you can watch them on the map.
4. The agents hand off to each other in the open: Police check for injuries, Hospital recommends treating on scene or taking the patient in, Drone Ops escorts the ambulance and asks Police to clear a traffic jam.
5. **People decide in chat.** The duty doctor replies `transport` or `treat`. The incident lead replies `leave` or `stay` when the scene looks clear. Nothing waits on the dashboard.
6. A report lands in `#ops`: timeline, who went, response time, recording window, minutes saved.
7. **The whole fleet is visible in `#fleet`**, live: every drone taking off, heading to a dock, charging and coming back, plus a fleet board every 2 minutes.
8. **People can steer the fleet from chat.** The Watch Commander types `watch market` to put a drone over an area, or `dock D-4` to ground one. The simulator checks every order before it happens.

## What you can do
**On the dashboard** (the ops wall, watch-only for the drone monitor team):
- **🔥 Simulate fire** and **💥 Simulate accident**: a reported incident at a city scene. The nearest drone diverts, and you watch the crews drive there.
- **🎲 Random fire**: a fire nobody reported. It burns on the map, and a patrol drone has to find it with its simulated thermal sensor.
- Watch progress rings at scenes, the escort drone flying ahead of the ambulance, traffic jams being cleared, watch rings over areas, and grounded drones.

**In Discord** (type the whole message, no @mention needed):

| Where | Type | Who can |
|---|---|---|
| Incident room | `transport` · `treat` | Duty doctor |
| Incident room | `leave` · `stay` · `stay 2 more minutes` · `leave market` (two calls open) | Incident lead |
| `#ops` | `watch market` · `watch ring road 30 min` · `increase surveillance on north road for 45 minutes` · `stop watching market` | Watch commander |
| `#ops` | `dock D-4` · `release D-4` | Watch commander |
| Any room | `@dispatch what's happening?` · `@dispatch ask police to stay until the tow arrives` | Anyone |
| `#ops` | `!list-documents` (Switch's own command): shows the **Flight authorisation log** the World Bot writes, one entry per incident | Anyone |

Places: Central Crossing, East Avenue, Market Street, Mill Lane shops, North Road, Northgate Flats, Ring Road, South Depot; any unique part of the name works. Drones: D-01 to D-14.

Try a refusal too: it shows the rules live in the simulator, not in an agent. `dock D-3` while D-3 escorts an ambulance, `leave` from someone who isn't the incident lead, or `watch market` when every patrol drone is low all get a plain `❌ REJECTED` with the reason.

**The full walkthrough, with screenshots, example messages and demo flows:** [docs/AirGrid-Demo-Guide.pdf](docs/AirGrid-Demo-Guide.pdf).

## Why Switch
- **Rooms are the operation.** `#ops` shows the headlines and the reports. `#incident-1` and `#incident-2` hold each incident's work, so two emergencies stay readable.
- **Agents and people work the same task together.** Agents report, recommend and hand off by @mention; Switch delivers each one. The human answers in plain words.
- **Decisions sit with roles, not one screen.** The incident lead, the duty doctor and the watch commander are roles in `config/humans.yaml`. Solo, one person holds them all; with a team, each person gets their own.
- **The fleet has its own room.** `#fleet` shows every drone, not only the ones at incidents, without pinging anyone.
- **Without Switch** this is a pile of separate scripts and a dashboard nobody can act from: no shared picture, no handoffs, no record of who decided.

## How Switch is set up
**Agents connected** (all Claude Code, run by Switch Console on the laptop, each in its own folder with only its instructions and a token):

| Agent | Job in the room |
|---|---|
| `dispatch` (Drone Ops) | Reports what the drones see, tags the right service, escorts the ambulance, answers "what's happening?" and relays requests between people and services |
| `fire-station` | Acknowledges fires, sends engines, reports progress |
| `hospital` | Sends ambulances, recommends treating on scene or taking the patient in |
| `police` | Checks scenes for injuries, clears roads and traffic jams |
| `airgrid-worldbot` | Plain code, no AI. A registered Switch client that carries simulator events into the rooms and people's decisions and orders back. The only member that can change the simulation, and only for a mapped person |

**Channels** (Discord, through Switch's Discord bridge):

| Channel | What happens there |
|---|---|
| `#ops` | Information only for incidents: one line when one starts and the final report, two messages each. Also where the Watch Commander types fleet orders |
| `#fleet` (optional) | The whole fleet, World Bot only, no pings: a live log of every drone change about every 10 seconds, a board every 2 minutes (in the air, charging, ready, what changed), a ⚠️ line when a patrol drone goes home with no spare to relieve it, and every order a person gave |
| `#incident-1`, `#incident-2` | One incident each: the agents' handoffs and every call for a person (the doctor's `transport`, the lead's `leave`). Two overlapping emergencies stay readable |
| `#test-room` | A quick check before a demo: `@dispatch reply with one word: ready` |

**Orders from the room** (the Watch Commander, typed in `#ops` with no @mention). The sim checks each one and says what happened; `#fleet` and the map show it:

| Type | What happens |
|---|---|
| `watch market` or `watch market 30 min` | The nearest spare patrol drone flies loops over that place until the time is up (default 60 clock-minutes, about 2 real minutes). No recording: it isn't an incident. Another drone takes over if its battery runs low |
| `stop watching market` | Ends the watch; the drone goes back on patrol |
| `dock D-4` | The drone goes to the nearest free dock and stays grounded, e.g. for a fault report or to charge before a busy hour. Refused while it's at an incident or escorting |
| `release D-4` | The drone can fly again once charged |

The 3 drones kept ready for emergencies are never used for a watch, so a watch can be refused when the fleet is stretched.

**People and roles** live in `config/humans.yaml`:

| Role | Decides |
|---|---|
| Incident lead | `leave` or `stay` when a scene looks clear |
| Duty doctor | `transport` or `treat` for the injured |
| Watch commander | Fleet orders: `watch`, `dock`, `release` |

Each person is matched by their Switch sender id, so a display name can't impersonate them. Solo, one person holds every role; with a team, give each role to a different person.

**Setting it up yourself:** `HUMAN_TODO.md` has the exact steps. Register the World Bot (`scripts/bootstrap_worldbot.py` on a local Switch server), create the four agents in Switch Console pointing at `~/airgrid-agents/<name>` (from `scripts/install_agents.sh`), invite them to `#ops`, `#incident-1` and `#incident-2`, invite only the World Bot to `#fleet`, paste the room instructions from `agents/switch/rooms/`, then run `scripts/run_game.sh --discord`.

## Rules the code enforces
- The simulator alone owns the truth. Agents read and propose through the `airgrid` CLI; they never change state on their own.
- Only mapped humans decide, and only in the room, via the World Bot.
- Timing, battery, landing slots, relief drones and no-fly zones are simulator code. An AI model never sits in the critical path, and the city keeps running if every agent goes offline.
- A 25% battery reserve, a red no-fly zone with no override, and a no-record zone are hard rules.
- Recording happens only at an active scene and is logged; a watch never records. Face identification does not exist in the system.
- Three charged drones always stay ready for emergencies: no patrol, watch or order can take them.
- No AI model inside the simulator. The "thermal sensor" is simulated and labelled as such.

## Architecture
```mermaid
flowchart LR
  SIM["Simulator (Python, FastAPI, SQLite)<br/>source of truth"]
  DASH["3D dashboard<br/>three.js, watch-only"]
  BOT["World Bot (plain code)"]
  ROOMS["Switch rooms in Discord<br/>#ops · #incident-1 · #incident-2 · #fleet"]
  AG["Agents (Claude Code)<br/>Drone Ops · Fire · Hospital · Police"]
  P["People<br/>incident lead · duty doctor · watch commander"]
  SIM -->|state| DASH
  SIM -->|events| BOT --> ROOMS
  ROOMS <--> AG
  AG -->|airgrid CLI, read| SIM
  P -->|leave / transport / watch / dock| ROOMS --> BOT -->|decision or order| SIM
```

## Run it
Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), and Git Bash on Windows.

```bash
uv sync
uv run pytest -q
scripts/run_game.sh
```

Open the printed URL (it includes a one-time key) and press a simulate button. The city runs without Discord; Discord is where the team works.

**With Switch and Discord:** follow `HUMAN_TODO.md` (*Phase 08* and *City mode*), then run `scripts/run_game.sh --discord`.

The original full simulator, a three-hour scripted shift with seven desks and a golden timeline it reproduces exactly, is still here: `scripts/run_golden.sh FRIDAY-RUSH`, `scripts/run_demo.sh`. It's also on the `cv-full-version` branch.

## Repo map
| Path | What |
|---|---|
| `sim/` | Simulator: city mode, rules, approvals, privacy log, golden run |
| `dashboard/` | 3D city view (static HTML/JS) |
| `worldbot/` | World Bot: sim events to rooms, human decisions to the sim |
| `cli/` | `airgrid` CLI the agents use |
| `agents/` | Agent instruction files and room instructions |
| `world/`, `scenarios/` | The fictional district and scenarios |
| `tests/` | Simulator, integration and live agent tests |
| `docs/` | Specs and decision log (`DECISIONS.md`) |


## Build and commit timeline

- **Before the hackathon (Oct 29 – Nov 1), my own tools:** the city simulator from my existing 3d world repo and its rules, the `airgrid` CLI, the 3D map, and the agent instructions and test harness. These are the existing tools and libraries used.
- **Also before the hackathon, while learning Switch:** a first version of the Switch integration, built in a private learning repo to understand how Switch works: the World Bot as a Switch client, the `#ops` and incident-room workflow, and the ambulance escort with the doctor's call and Police clearing traffic (Nov 1).
- **During the hackathon (Nov 4–7):** this repo, and everything committed from Nov 5 onward. The commits show what came in when:
  - **Random fire found on patrol:** a hidden fire that a roaming drone detects with its simulated thermal sensor, drawn burning on the map before it's found.
  - **Realtime `#fleet` on Discord:** every drone taking off, docking, charging and coming back, a fleet board every 2 minutes, and alerts when a route has no spare drone.
  - **Orders from Discord:** `watch <place>` to put a drone over an area, and `dock` / `release` to ground a drone, all checked by the simulator.
  - **Cleaner rooms:** every decision asked only in the incident's own room, `#ops` kept to headlines and reports, and the escort drone named in its lines.
  - The README, the [demo guide](docs/AirGrid-Demo-Guide.pdf), the demo video, and the live runs in Discord.

This repo's first commit holds the pre-hackathon tools without the Switch integration. The second brings the integration over from my learning repo, labelled as such. That history is private, but I'm happy to share it with the judges on request.


## Sources for the context above
- Chula Vista Police Department, Drone as First Responder programme reporting (calls flown, time to scene).
- DJI Dock 3 specifications (flight time, charge time, wind limit), used to calibrate the simulated drones.

These are real programmes for context only. No real programme's numbers are presented as AirGrid's results.

## Credits
- City models: City Kit (Commercial) and City Kit (Roads) by [Kenney](https://kenney.nl), CC0.
- [three.js](https://threejs.org), MIT.
- Built with Claude Code.
