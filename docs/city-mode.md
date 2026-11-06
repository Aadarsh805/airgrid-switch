# City mode: the living city (hackathon version)

kazum's concept, Nov 1 (L56). A sim you watch, with one human decision per incident. Replaces the scripted shift as the default demo. The full sim, golden run and `cv-full-version` branch stay as they are; `run_golden.sh` must stay at 0 diffs. Vehicle takeover is a later phase.

## What the viewer sees
- **Calm:** drones constantly moving, roaming routes across the whole district. Never static. Ground vehicles parked at their stations. The airfield no-fly zone stays visible and drones route around it.
- **Three stations on the map:** Fire Station, Hospital, Police Station, each a distinct labelled building.
- **Only two triggers:** 🔥 Fire and 💥 Accident, at a scene picked from `world.city.scenes`. Click anywhere isn't needed. Two incidents may run at once.

## Incident flow
1. Trigger → alert marker. The nearest roaming drone diverts and reaches eyes-on.
2. The drone shares coordinates and a report: Fire Station for a fire; Police and, if the feed says injuries, Hospital.
3. Vehicles visibly leave their station and drive **on the roads** to the scene: fire engine, ambulance, police car, as needed.
4. On arrival a League-of-Legends-style progress ring/bar runs over the scene: "Extinguishing 64%", "Treating patients 40%", "Clearing the road 80%". Durations in `world.city.work_min`, 30–45 s real time.
5. At 100%, vehicles drive back. The drone keeps watching.
6. The drone pings kazum: "Looks clear. Leave, or stay a little longer?" Two big buttons; `approve`/`stay` in `#ops` also works.
   - **Return to patrol:** drone resumes roaming. If the incident was flagged at risk, a seeded small flare-up may reopen it and the crew returns.
   - **Stay longer:** drone holds 30 s, then asks again with a clearer reading (risk gone).
7. A full incident report posts to `#ops`: timeline, who went, response time, recording window.
8. No answer: the drone keeps watching and the call stays open until the lead replies (L60).

## Automatic background
A drone at a scene low on battery is relieved by another, on station before it leaves. Docks, slots, reserve and no-fly rules all stay enforced by the sim. Recording only at the scene and logged, shown as one small line.

## Screen
Keep the 3D map and look. Remove from the default view: speed buttons, play/pause/skip/reset, Orbit, Fleet, Baseline, KPI strip, log panel, scorecard, step bar. Keep: small AIRGRID title with "fictional, simulated", a calm day/time label, three counters (incidents handled, average response, drones up), two trigger buttons, the decision card when needed, and one plain-English line for what's happening. Footer stays. Ops details stay reachable at `?ops=1`.

## Sim (`sim/city.py`)
`POST /city/start`, `GET /city`, `POST /city/trigger {kind}`, `POST /city/decide {incident, choice}`, all writes with `X-Game-Key`. Runs continuously at a fixed rate, with no end. Roaming, station vehicles, road routing on the junction grid, work timers and the risk flag are sim code; deterministic with a seed. Agents never gate anything. Everything goes through the existing logs. No LLM.

## Discord
One room, `#ops`. Agents: `dispatch` (shown as Drone Ops), `fire-station`, `hospital`, `police`. Roughly 6–8 messages per incident: drone report, each service acknowledges with ETA, on-scene, done, the drone's leave-or-stay ping to kazum, final report. The game still runs if agents are offline. Numbers only from the CLI.

## Later
Taking control of any vehicle from the master view.

## Update (L57): the room decides, the dashboard watches
The dashboard is the drone monitor team's view: watch only, plus two clearly labelled simulate buttons. Every human decision happens in `#ops`: Drone Ops @mentions the `incident_lead`, who replies in plain words. Agents hand off to each other by real @mention, so the work happens in the open. Step 6's on-screen buttons are removed.

## Roadmap (L59), one at a time, at most 2 human calls per incident
0. Clean rooms: `#ops` gets 3 messages per incident (start, YOUR CALL, report); `#incident-1` and `#incident-2` hold the chatter. Ambulance assessment. Docked boxes.
1. Second engine: agent recommends with facts, owner decides, map and timing change, report shows the cost. Adds roles (Fire Chief, Duty Doctor, Police Sergeant, Incident Lead) and the after-action report.
2. Close the road: Police drafts a public advisory; the Sergeant edits or approves.
3. Which drone: pull a nearby drone off another scene, or launch a farther one. Only when two incidents overlap.
4. Evacuate: new map visuals.
Later: hit-and-run face-ID refusal, ask-anything questions, vehicle takeover.

## Ambulance escort (L61, built before Step 1)
For an accident with injuries, after the ambulance arrives:
1. Assessment (sim, seeded; `hospital` agent voices it): `treat` on scene or `transport`, with the facts.
2. The Duty Doctor confirms in the incident room: `transport` or `treat`. Role `duty_doctor`, kazum solo. No answer: the call stays open and the crew keeps treating.
3. Transport: the ambulance drives to the Hospital along the roads. A drone escorts it, flying just ahead on its route.
4. Seeded traffic can block a junction on the route. The drone sees it first: the ambulance stops, traffic shows on the map, and Drone Ops asks `@police` to clear it.
5. A police car drives to the junction, a short clearing progress ring runs, and the ambulance continues. Without a free police car, it waits.
6. Arrival: the ambulance reaches the Hospital and the patient is handed over. The drone then asks the usual leave-or-stay call for the scene, if not already answered.
7. The report states transport time, delay, and minutes saved compared with waiting for traffic to clear by itself.

Everything is sim code; agents narrate. At most 7 lines in the incident room for the escort. #ops still gets start, calls and report only.

## Random fire: drones detect it (L62)
Third button, "Random fire". The sim places a hidden fire at a seeded random road-side spot clear of zones, without telling anyone. Roaming drones carry a simulated thermal sensor: within `detect_km` a drone flags "possible heat source", diverts toward it, and confidence rises by distance (fixed thresholds) until "fire confirmed". Then the normal fire response runs, with the incident marked `found_by: <drone> on patrol`. Detection range and a guaranteed fallback ensure it is found within about 20 real seconds. No LLM or vision model: the sim stands in for the sensor, labelled "simulated sensor" on screen and in Discord. Before detection, the bot, agents and keyless /city reveal nothing about the hidden fire (rule 8). L63: the operator's screen (with `X-Game-Key`) draws it burning, without an alert beacon, so viewers see the fire before the drone finds it.

## #fleet (L64)
Optional room for the whole fleet, World Bot only. Every 2 real minutes a board: how many drones are in the air (on patrol, at incidents, heading to a dock), charging and ready, how many went to charge and took off since the last board, the lowest battery in the air, and any patrol route without a drone. A ⚠️ line at once when a patrol drone goes home with no spare to relieve it, because the sim keeps 3 drones ready for emergencies. No @mentions, no replies, no agents.
L66: plus a live log, every drone change as one timed row, gathered into one post about every 10 seconds. The first board posts at the start.

## Orders from the room (L67)
`watch <place> [N min]`, `stop watching <place>`, `dock D-n`, `release D-n`, typed in a city room by the Watch Commander. `POST /city/order` with the operator key. Watch takes a spare patrol drone (never the 3 kept ready for emergencies), flies loops over the place without recording, hands over when the battery runs low, and ends on time. Dock grounds a drone (not one at an incident or escort) until released. The reply is in the room, the record in `#fleet`, the ring on the map.
