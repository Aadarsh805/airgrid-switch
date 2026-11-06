# 09 · Demo video script, 2:50

The submission video must be **under 3 minutes**, public or unlisted on YouTube or Loom, and must show **the agents working in a real chat with your teammates**. Target 2:50, hard stop 2:58.

The story: a reel about Shenzhen's drone network ends with a question, *"Where do we draw the line between public safety and personal privacy?"* AirGrid is the answer for our own cities: the drones are the easy part; the hard part is Fire, Police, the Hospital and a fleet team working as one, with people making the calls and privacy built into the rules.

## The reel
- File: `Downloads/instasave.website_AQOp…mp4`, 39 s, vertical 720×1280. Transcript: `Downloads/Drone_Video_Transcript_and_Screenshots.pdf`.
- It's someone else's work: narration by **Builder's Notes**, footage credited on screen to **XuZhenqing, Shenzhen**. Use only the two short pieces below (about 24 s in all), keep the credit on screen the whole time, and name the source in the video description. Better still, message the creator and ask; a yes removes any doubt.
- Piece 1, **0:00.0–0:12.1**: "China turned traffic lights into drone airports… monitor traffic, spot violations, and respond to incidents."
- Piece 2, **0:23.9–0:30.5**: "Now imagine dozens of these stations across a city. The real innovation isn't the drone, it's the network."
- Piece 3, **0:30.9–0:37.3**, used near the end: "But that raises a bigger question. Where do we draw the line between public safety and personal privacy?"

## Timeline
| Time | On screen | Sound |
|---|---|---|
| 0:00–0:19 | Reel pieces 1 and 2, centred on a blurred copy of itself, credit bottom-left | Reel audio |
| 0:19–0:36 | Title card **AirGrid**, then the calm 3D city | Voice-over 1 |
| 0:36–0:46 | Discord: channel list and member list (4 agents, World Bot, you) | Voice-over 2 |
| 0:46–1:46 | Flow A: the accident, end to end | Voice-over 3, sped-up gaps |
| 1:46–2:10 | Flow B: random fire found on patrol | Voice-over 4 |
| 2:10–2:30 | Flow C: `#fleet`, `watch market`, `dock D-4` | Voice-over 5 |
| 2:30–2:38 | Reel piece 3 | Reel audio |
| 2:38–2:52 | Privacy rules card, then the city | Voice-over 6 |
| 2:52–2:55 | End card: name, repo link, "Fictional city · simulated" | Silence |

## Voice-over (read at an easy pace; each block fits its slot)
**VO 1 (0:19–0:36, about 45 words)**
> "That's Shenzhen. Drones that launch from the street, land themselves, and recharge. I wanted this for our cities. But a network of drones only helps if the people on the ground work as one: fire, police, the hospital, and a team watching the fleet. So I built AirGrid."

**VO 2 (0:36–0:46, about 28 words)**
> "AirGrid runs a city's drone response from one team chat. Drone Ops, Fire, Hospital and Police are AI agents in Switch. The people on the team make the calls."

**VO 3 (0:46–1:46, about 120 words, spoken over the accident)**
> "An accident on Ring Road. The nearest drone gets there first and tags Police. Police find three injured and call the Hospital. The Hospital recommends taking them in, and asks the duty doctor. That's a person. I type *transport*."
> (⏩) "A drone escorts the ambulance, flying ahead of it. It spots a traffic jam before the ambulance reaches it, and asks Police to clear it. Watch the junction clear."
> (⏩) "Patient delivered. The scene looks clear, so the incident lead types *leave*, and the full report lands in ops: who went, when, and what was recorded. The agents did the legwork. People made every decision."

**VO 4 (1:46–2:10, about 50 words)**
> "This fire, nobody reported. It's burning, and no one knows yet. A patrolling drone picks up the heat with its thermal sensor, simulated here, turns to check, and confirms it. The fire engine is already on its way. Found before anyone called."

**VO 5 (2:10–2:30, about 50 words)**
> "The fleet team sees every drone, live: taking off, charging, coming back. And they can steer it from chat. *Watch market* puts a drone over Market Street. *Dock D-4* grounds one for a fault. The simulator checks every order, and it never touches the three drones kept for emergencies."

**VO 6 (2:38–2:52, about 40 words)**
> "My answer: drones record only at an active incident, and that's logged. A watch never records. Face identification doesn't exist in the system. And no AI approves its own work: people decide, in the open, through Switch."

## Shots to record (OBS, 1920×1080, 30 fps)
Record each flow as its own take; the editor cuts the waiting. Dashboard on the left (about 60%), Discord on the right.

| Take | What to do | Length |
|---|---|---|
| T1 calm | The city with drones patrolling, no incidents. Slowly hover over the stations | 20 s |
| T2 team | In Discord: scroll the channel list, open the member list | 10 s |
| T3 accident | Press **Simulate accident**, switch to its incident room, type `transport` when asked, then `leave` when asked, then show the report in `#ops` | 3–4 min raw |
| T4 random fire | Press **Random fire**; keep the map in view until the engine arrives | 1 min raw |
| T5 fleet | Show `#fleet` scrolling; in `#ops` type `watch market`, wait for the ring; type `dock D-4`, wait for "Grounded" | 1 min raw |
| T6 rules | Optional: `dock D-3` while D-3 escorts, to show a `❌ REJECTED` | 20 s |

**Teammate:** if a friend can hold `duty_doctor` on their own Discord account and type `transport`, that's what "with your teammates" looks like on screen. Solo works too.

**Voice:** record the six VO blocks separately afterwards, one file each (`vo-1.wav` … `vo-6.wav`), in a quiet room with the mic close. Phone voice memos are fine.

## Before recording
- Laptop plugged in and awake. Restart Switch Console and wait for the green dot.
- Paste the current room instructions from `agents/switch/rooms/` into each room.
- `scripts/run_game.sh --discord`, then open the printed URL. Discord notifications off.
- `#test-room`: `@dispatch reply with one word: ready`. Wait for the answer.
- Empty `#ops`, `#incident-1`, `#incident-2` and `#fleet`, or start a new day.

## If something breaks
| Problem | Do this |
|---|---|
| Agents silent, "My machine is offline" | Restart Switch Console; check the green dot; re-run |
| `transport` or `leave` gets no reply | Type it in the incident room, from the mapped account |
| Running long | Cut Flow C to `watch market` only, then shorten Flow B |
