# Submission draft (Flip the Switch form)

Copy each answer into the form. Edit the wording to sound like you. Fields marked ⚠ depend on things that aren't done yet.

## Part 1 · Project details

**Project title**
AirGrid: a city drone monitoring system with AI agents, run from one team chat

**Tracks**
Best Business Workflow. Optionally also Best Blog, if you publish the post.

**Project description**
Cities already send drones to emergencies, and the drones aren't the hard part. Coordination is. Fire, police and the hospital each hold part of the picture, decisions happen over the phone, and afterwards nobody can show who decided what.

AirGrid puts that coordination in one team chat. Drones patrol a fictional city. When a fire or accident starts, AI agents for Drone Ops, Fire Station, Hospital and Police work it together in a Switch room: they report, recommend and hand off to each other by @mention. People make the calls in the same chat. The duty doctor decides whether to take a patient to hospital, and the incident lead decides when the drone can leave the scene. A 3D map shows the drones, engines, ambulances and police cars moving as it happens, and a report with the full timeline lands in the chat at the end.

Some fires nobody reports: a patrolling drone finds them with its simulated thermal sensor, and the response starts on its own. A `#fleet` channel shows every drone live, taking off, charging and coming back, and the team can steer the fleet from chat: `watch market` puts a drone over an area, `dock D-4` grounds one. The simulator checks every decision and order before it happens, so no agent can approve its own work.

It's for city emergency teams, and for anyone who wants to see how AI agents and people could run time-critical work together without handing the decisions to the AI. Everything is simulated: no real drones, no real dispatch, no identification of people.

**GitHub repository** (public)
https://github.com/Aadarsh805/airgrid-switch

**Demo video** ⚠ record first. Under 3 minutes, YouTube or Loom, public or unlisted
(link)

**Live demo or deployment**
Leave blank. It runs locally with Switch Console and Discord.

## Part 2 · Track details (optional)

**Blog post** ⚠ if you write it
(link)

**#HowISwitch post** ⚠ if you post it
(link)

Pull requests and Wispr card: leave blank. They're for other tracks.

## Part 3 · How you used Switch

**How did you use Switch in your project?**
I connected four Claude Code agents through Switch Console: Drone Ops, Fire Station, Hospital and Police. Each runs in its own folder with only its instructions and a token, and can only read and act through a small CLI. A fifth member, the World Bot, is plain code registered as a custom Switch client. It carries events from the city simulator into the rooms and carries people's decisions back.

It runs on Discord through Switch's Discord bridge, with four channels. `#ops` is information only for incidents: one line when one starts and the final report; it's also where the Watch Commander types fleet orders. `#incident-1` and `#incident-2` hold the work and the decisions for each incident, so two emergencies at once stay readable. `#fleet` holds only the World Bot and shows the whole fleet live, with no pings, so no agent wakes for routine news.

The real work is running an emergency response. When there's an accident, Drone Ops gets eyes on and tags Police. Police check for injuries and call in the Hospital. Hospital recommends taking the patient in and tags the duty doctor, a person, who types `transport`. Drone Ops then escorts the ambulance, spots a traffic jam on the way and asks Police to clear it. When the scene looks clear, the incident lead types `leave` or `stay`. Between incidents, the Watch Commander can type `watch market` to put a drone over an area, or `dock D-4` to ground one for a fault; the simulator refuses an order that would break a rule, such as taking one of the three drones kept for emergencies. Teammates work with it by holding these roles: each decision is tied to a role in a config file and matched to the person's Switch identity, so with a team, the doctor, the lead and the watch commander are different people in the same rooms.

Switch is what makes it a team. Without it, these are four separate agents and a dashboard nobody can act from. With it, they hand work to each other in the open, and the people decide in the same place.

**What did you like about Switch, and what would you improve?**
What worked: putting agents in a normal Discord channel and having them hand work to each other just by @mention. The agent protocol was clear enough to write my own client for the World Bot, and the docs were good: I could read how the bridge, rooms and addressing work before writing code. Room instructions were a simple way to give every agent the same rules.

What got in the way: on a local server run by Switch Console there's no Gateway page, so there was nowhere to create the registration key a custom client needs. I had to work around it through the API. Each @mention opens a Discord thread for the agent's activity, while the actual messages land in the channel, which made busy channels confusing until I moved incident chatter into separate rooms. And once the link between Console and my machine dropped, every agent went quiet with a "machine offline" reply.

What I'd want next: a way to make registration keys in Console for local servers, a choice to keep an agent's replies in its thread or in the channel, and room documents you can read in Discord, so a team can see a shared log without opening the Gateway.

## ⚠ Before you submit (deadline Nov 7, 11:59 PM IST)
- Record the video under 3 minutes using `docs/09-DEMO-SCRIPT.md` and `docs/AirGrid-Demo-Guide.pdf`; upload it to YouTube or Loom, public or unlisted, and paste the link above.
- Optional: ask the organisers about the timing, since some Switch work was done before Nov 4. The README's "Build and commit timeline" already says what was built when; point them to it.
- The repo is public, with the full commit history.
