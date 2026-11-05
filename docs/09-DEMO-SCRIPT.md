# 09 · Demo script: city mode, under 3:00

The submission video must be **under 3 minutes**, public or unlisted on YouTube or Loom, and must show **the agent working in a real chat with your teammates**. Target 2:45. The earlier 3-hour Friday Rush script is in git history and on the `cv-full-version` branch.

**Screen:** dashboard left (about 60%), Discord right. `#ops` open, switching to `#incident-1` when the script says so.
**Teammate:** if possible, a second Discord account (a friend, or your own second account) holds `duty_doctor` and types `transport`. That is what "with your teammates" looks like. Solo is fine if not.
**Seed:** `run_game.sh` default. Its first accident has injuries, a transport and a traffic block.
**Numbers:** say only what's on screen. Cut waiting time in the edit and mark each cut with a small "⏩".

## Before recording
- Laptop plugged in and awake. Restart Switch Console and wait for the green dot.
- `scripts/run_game.sh --discord`, then open the printed URL.
- `#test-room`: `@dispatch reply with one word: ready`. Wait for the answer.
- Empty `#ops`, `#incident-1` and `#incident-2`, or start a new day.

## 0:00–0:15 · The idea
Calm city, drones moving, then the Discord member list.
> "This is AirGrid. A city's drone response team in one chat. Drone Ops, Fire, Hospital and Police are AI agents in Switch. The people make the calls."

## 0:15–1:50 · One accident, end to end ★
Press **Simulate accident**.
1. `#ops`: one line saying it started and which room. Switch to `#incident-1`.
2. Drone Ops has eyes on and tags `@police`. On the map, a police car drives there.
3. "3 injured, @hospital ambulance needed." The ambulance drives there.
4. **The doctor's call:** Hospital recommends with the facts and tags the duty doctor, who types `transport`.
   > "The agent recommends. A person decides."
5. ⏩ The ambulance heads to the hospital with the drone escorting it.
6. **Traffic jam:** stopped cars on the map. Drone Ops asks `@police` to clear it. A police car arrives, the ring fills, the road clears.
   > "The drone spotted the jam and called the people who could fix it."
7. ⏩ Patient delivered. Still in `#incident-1`: the lead's call, leave or stay. Type `leave`. The report lands in `#ops`.

## 1:50–2:25 · Found before anyone called
Press **Random fire**. Flames appear on the map with no alert beacon.
> "Nobody reported this one."

A patrolling drone picks up a heat reading, turns, and confidence climbs to "fire confirmed". The engine goes. ⏩ Show the progress ring once.
> "The sensor is simulated. The teamwork is the point."

## 2:25–2:45 · Close
`#ops`: one start line and one report per incident. The decisions stayed in the incident rooms.
> "Agents do the legwork, people decide, and the record writes itself. All in the chat, through Switch. The simulator owns the truth, so agents can't approve their own work. Fictional city, simulated drones."

## If something breaks
| Problem | Do this |
|---|---|
| Agents silent, "My machine is offline" | Restart Switch Console; check the green dot; re-run |
| `transport` or `leave` gets no reply | Type it in the incident room, from the mapped account |
| Running long | Cut the random fire (1:50–2:25); it's the only optional part |
