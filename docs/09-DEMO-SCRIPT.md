# 09 · Demo video script, 2:50

The submission video must be **under 3 minutes**, public or unlisted on YouTube or Loom, and must show **the agents working in a real chat with your teammates**. Target 2:50, hard stop 2:58.

The vibe: a casual walkthrough, like showing a friend what you built. Not an ad. Talk normally, it's fine to pause or say "so" and "basically". Don't read it word for word: glance at the lines, then say them your way.

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
| 0:36–0:46 | Discord: the member list, your teammates Drone Ops, Fire Station, Hospital and Police (AI agents), plus you | Voice-over 2 |
| 0:46–1:46 | Flow A: the accident, end to end | Voice-over 3, sped-up gaps |
| 1:46–2:10 | Flow B: random fire found on patrol | Voice-over 4 |
| 2:10–2:30 | Flow C: `#fleet`, `watch market`, `dock D-4` | Voice-over 5 |
| 2:30–2:38 | Reel piece 3 | Reel audio |
| 2:38–2:52 | Privacy rules card, then the city | Voice-over 6 |
| 2:52–2:55 | End card: name, repo link, "Fictional city · simulated" | Silence |

## Voice-over (rough lines, say them your way)
**VO 1 (0:19–0:36), over the title and the city**
> "So I came across this reel a while back. In Shenzhen the drones just sit on poles above the street, fly out, come back and charge on their own. And I was like, okay, what would it take to have that here? Honestly the drones aren't the hard part. It's getting fire, police, the hospital and whoever's watching the drones to actually work together. So that's what I tried to build. It's called AirGrid."

**VO 2 (0:36–0:46), over the Discord channels**
> "Everything runs in Discord through Switch. This is the team: drone ops, fire, hospital and police. They're AI agents, but they work like teammates. Each one has its own job, they tag each other when they need help, and they check with us before anything big happens."

**VO 3 (0:46–1:46), over the accident**
> "Let me trigger an accident. Okay, drone ops is already on it, and it tags police in the incident channel. Police check it out, three people are hurt, so they call in the hospital. Hospital thinks they should go in, but that's the duty doctor's call, and that's my role on the team today. So... *transport*."
> (⏩) "Now drone ops sends a drone ahead of the ambulance. There's a jam up ahead. The drone spots it first, so drone ops asks police to clear it, and police are on it. You can see the cars moving out of the way."
> (⏩) "Patient's at the hospital. Drone ops asks if the drone can leave the scene. Yeah, *leave*. And the report shows up in ops with everything the team did."

**VO 4 (1:46–2:10), over the random fire**
> "This one's a fire nobody called in. It's just burning there. A drone on patrol picks up the heat, the sensor's simulated obviously, goes over to check, and yep, it's a fire. Drone ops tells the fire station, and the fire truck's already heading out."

**VO 5 (2:10–2:30), over #fleet and the orders**
> "This channel is just the drones. Who's flying, who's charging, who's coming back. And I can tell them stuff. *Watch market*, and one drone goes and circles Market Street. *Dock D-4*, and it goes home and stays there. It always keeps three drones back for emergencies, so it won't let me use those."

**VO 6 (2:38–2:52), over the privacy card and the city**
> "That question at the end of the reel is a fair one. So here, drones only record when there's an actual incident, and it's logged. The watch thing doesn't record at all. There's no face recognition. And the AI teammates never make the big calls on their own, a person does. That's AirGrid. Thanks for watching."

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

**Voice:** record the voice-over afterwards, one file per block, in a quiet room with the mic close (phone voice memos are fine): `vo-1`, `vo-2`, `vo-3a`, `vo-3b`, `vo-3c` (VO 3 split at each ⏩), `vo-4`, `vo-5`, `vo-6`. Any of wav, m4a or mp3 works. Put them in `video/raw/` next to the takes, named `T1-calm.mp4` … `T5-fleet.mp4`.

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
