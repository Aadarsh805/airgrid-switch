"""`--profile city`: the living city's Discord (docs/city-mode.md, L56, L58, L59).

#ops is info only: two messages an incident, a one-line start and the report. Every call to a
person (leave or stay, transport or treat) goes to the incident's own room (L63).
The work happens in #incident-1, or #incident-2 for an overlapping incident: at most five lines,
each starting with the place. People never need a drone or incident id: everything is named by
place, and those ids appear only inside the final report.

Handoffs are real @mentions, so Switch delivers them, and agents answer in the room they were asked.
Nothing here holds the sim. If nobody answers, the city runs on.
"""

from __future__ import annotations

from sim.auth import humans_path
from sim.geo import dist
from sim.world import load_world
from worldbot.game_profile import OPS, Moment

AGENTS = ("dispatch", "fire-station", "hospital", "police")
INCIDENT_ROOMS = ("#incident-1", "#incident-2")
ROOMS = (OPS, *INCIDENT_ROOMS)
FLEET = "#fleet"  # optional (L64): the whole fleet, bot only, no mentions. Skipped if absent.
FLEET_EVENTS = {"fleet_board": "🛰", "fleet_alert": "⚠️", "fleet_log": "📋", "city_order": "📌"}
SERVICES = {"fire": "fire-station", "accident": "police"}
ICONS = {"fire": "🔥", "accident": "💥"}
LEAD = "incident_lead"  # the human role that decides leave or stay
DOCTOR = "duty_doctor"  # the human role that decides transport or treat
LEAVE_STAY, TREAT_TRANSPORT = "leave_stay", "treat_transport"
CALLS = {
    "leave": (LEAVE_STAY, LEAD),
    "stay": (LEAVE_STAY, LEAD),
    "transport": (TREAT_TRANSPORT, DOCTOR),
    "treat": (TREAT_TRANSPORT, DOCTOR),
}
WORDS = {LEAVE_STAY: ("leave", "stay"), TREAT_TRANSPORT: ("transport", "treat")}
EVENTS = (
    "heat_possible",
    "fire_confirmed",
    "city_alert",
    "city_eyes_on",
    "ambulance_requested",
    "ambulance_not_needed",
    "city_decision",
    "city_stay",
    "care_assessed",
    "transport_started",
    "police_requested",
    "traffic_cleared",
    "patient_delivered",
    "city_report",
    *FLEET_EVENTS,
)


def load_lead(path=None, role: str = LEAD) -> str:
    """How a call names the person holding `role`: `discord_mention` in humans.yaml if set, else
    @<discord>."""
    import yaml

    with open(path or humans_path(), encoding="utf-8") as fh:
        humans = (yaml.safe_load(fh) or {}).get("humans") or []
    for h in humans:
        if role in (h.get("roles") or []):
            return str(h.get("discord_mention") or f"@{h['discord']}")
    return ""


def _nice(name: str) -> str:
    return str(name).replace("_", " ")


def _care_lines(r: dict) -> list[str]:
    rows = [f"Care: {r['care']}"] if r.get("care") else []
    t = r.get("transport")
    if t:
        rows.append(
            f"Transport: {t['transport_min']} min, delay {t['delay_min']} min, "
            f"saved {t['saved_min']} min"
        )
    return rows


def report_text(r: dict) -> str:
    """The final report: one tidy message, and the only place drone and incident ids appear."""
    rec = r.get("recording") or {}
    window = f"{rec['from']} to {rec['to']}, at the scene only" if rec.get("from") else "none"
    who = ", ".join(_nice(v) for v in r.get("who") or []) or "no ground units"
    rows = [f"  {t['clock'][:5]}  {t['text']}" for t in r.get("timeline") or []]
    name = r.get("name") or f"{r['scene']} {r['kind']}"
    return "\n".join(
        [
            f"✅ {name}: closed. Report {r['incident']} (simulated)",
            f"Response: first eyes on in {r['response_s']} s",
            f"Who went: {who}",
            f"Recording: {window}",
            *_care_lines(r),
            "Timeline:",
            *rows,
        ]
    )


class CityProfile:
    rooms = ROOMS
    optional_rooms = (FLEET,)
    home_room = OPS
    decides = True  # typed leave/stay reach /city/decide
    over = False

    def __init__(self, lead: str = "@kazum", doctor: str = "@kazum"):
        self.lead, self.doctor = lead, doctor
        scenes = load_world().raw["city"]["scenes"].values()
        self.places = [(s["name"], s["at"]) for rows in scenes for s in rows]
        self.reset()

    def reset(self) -> None:
        self.open: dict[str, dict] = {}
        self.heat: dict[str, str] = {}  # drone checking a heat reading -> the room it will use
        self.found: str | None = None  # room of a fire just confirmed, for its alert (L62)

    def _free_room(self) -> str:
        """The free incident room; a third overlap shares the quieter one, and the place prefix
        keeps it readable."""
        used = [s["room"] for s in self.open.values()] + list(self.heat.values())
        return min(INCIDENT_ROOMS, key=used.count)

    def _start(self, p: dict, text: str) -> tuple[dict, str]:
        scene = text.split(" reported at ")[-1].rstrip(".")
        found = p.get("kind") == "fire" and self.found is not None
        room, self.found = (self.found, None) if found else (self._free_room(), self.found)
        s = {
            "kind": p.get("kind"),
            "scene": scene,
            "name": f"{scene} {p.get('kind')}",
            "room": room,
            "asking": False,
            "call": None,
            "pinged": set(),
            "asked_in_room": set(),
            "human_stayed": False,
            "recommend": None,
        }
        self.open[p.get("incident")] = s
        how = ", found by a drone on patrol" if found else ""
        icon = ICONS.get(s["kind"], "🚨")
        return s, f"{icon} {s['name']}{how}. Crews are working in {s['room']}."

    def near(self, at) -> str:
        """A junction in words: the nearest named place. Never coordinates."""
        return "near " + min(self.places, key=lambda p: (dist(p[1], at), p[0]))[0]

    def key(self, iid: str) -> str:
        """The short word to type for an incident: the place's first word, whole name if shared."""
        word = self.open[iid]["scene"].split()[0].lower()
        others = [i for i in self.asking(self.open[iid]["call"]) if i != iid]
        clash = any(word in self.open[i]["name"].lower() for i in others)
        return self.open[iid]["name"].lower() if clash else word

    def _call(self, iid: str, s: dict, kind: str, at_risk) -> str:
        s["asking"], s["call"] = True, kind
        one, two = WORDS[kind]
        if len(self.asking(kind)) > 1:
            k = self.key(iid)
            one, two = f"{one} {k}", f"{two} {k}"
        how = f"`{one}` or `{two}`"
        if kind == TREAT_TRANSPORT:
            said = f"Hospital recommends {s['recommend']}" if s["recommend"] else "needs a doctor"
            return f"🙋 {self.doctor} {s['name']}: {said}. Reply: {how}.".strip()
        state = "looks mostly clear" if at_risk else "looks clear"
        return f"🙋 {self.lead} {s['name']} {state}. Reply: {how}.".strip()

    def moment(self, event: dict) -> Moment | None:
        kind, p = event.get("type", ""), event.get("payload") or {}
        if kind not in EVENTS:
            return None
        iid = p.get("incident")
        text = str(event.get("text", ""))
        if kind in FLEET_EVENTS:  # plain lines, no @mention, so no agent wakes (L64)
            line = f"{FLEET_EVENTS[kind]} Fleet: {text}"
            if kind == "fleet_log":  # the live log (L66): one row a change
                line = text  # rows already start with the time
            return Moment((kind, event.get("seq")), FLEET, text=line)
        if kind == "heat_possible":  # before any incident: one line, in the room it will use
            drone = p.get("drone")
            if drone in self.heat:
                return None
            room = self.heat[drone] = self._free_room()
            line = f"🌡 {drone} on patrol: possible heat source, checking (simulated sensor)"
            return Moment((kind, drone), room, text=line)
        if kind == "fire_confirmed":
            self.found = self.heat.pop(p.get("drone"), None) or self._free_room()
            return None
        if kind == "city_alert":
            _s, line = self._start(p, text)
            return Moment((kind, iid), OPS, text=line)
        s = self.open.get(iid)
        if s is None:  # an incident from before this bot joined
            return None
        name, room = s["name"], s["room"]
        if kind == "city_eyes_on":
            line = f"{ICONS.get(s['kind'], '🚨')} {name}: Drone Ops has eyes on. "
            return Moment(
                (kind, iid), room, text=line + f"@{SERVICES.get(s['kind'])} you're needed."
            )
        if kind == "ambulance_requested":
            n = p.get("injuries")
            line = f"🚑 {name}: {n} injured, @hospital ambulance needed."
            return Moment((kind, iid), room, text=line)
        if kind == "ambulance_not_needed":
            return Moment((kind, iid), room, text=f"🚑 {name}: no injuries, no ambulance needed.")
        if kind == "city_decision":
            call = p.get("kind", LEAVE_STAY)
            ping = call not in s["pinged"] or s["human_stayed"]  # silence isn't asked over and over
            s["pinged"].add(call)
            s["human_stayed"] = False
            line = self._call(iid, s, call, p.get("at_risk"))
            if call == TREAT_TRANSPORT and call in s["asked_in_room"]:
                return None  # the Hospital's assessment line already asked the doctor (L63)
            # every call goes to the incident's own room: #ops keeps only info (L63)
            return Moment((kind, iid), room, text=line) if ping else None
        if kind == "care_assessed":
            s["recommend"] = p.get("recommend")
            s["asked_in_room"].add(TREAT_TRANSPORT)
            line = f"🚑 {name}: Hospital: {text} {self.doctor} your call: `transport` or `treat`."
            return Moment((kind, iid), room, text=line)
        if kind == "transport_started":
            who = f" ({p['drone']})" if p.get("drone") else ""  # drone ids are fine (L65)
            line = f"🛰 {name}: Drone Ops{who} escorting the ambulance, route clear."
            return Moment((kind, iid), room, text=line)
        if kind == "police_requested":
            s["junction"] = self.near(p.get("at"))
            who = f" ({p['drone']})" if p.get("drone") else ""
            line = (
                f"🛰 {name}: Drone Ops{who} → @police ambulance stuck {s['junction']}, "
                "please clear it."
            )
            return Moment((kind, iid), room, text=line)
        if kind == "traffic_cleared":
            line = f"🚓 {name}: Police cleared the junction {self.near(p.get('at'))}."
            return Moment((kind, iid), room, text=line)
        if kind == "patient_delivered":
            return Moment(
                (kind, iid), room, text=f"🚑 {name}: Hospital has the patient. Delivered."
            )
        if kind == "city_stay":
            s["asking"] = False
            return None
        del self.open[iid]  # city_report
        return Moment((kind, iid), OPS, text=report_text(p["report"]), ends=True)

    def asking(self, kind: str | None = None) -> list[str]:
        """Incidents with a call open now, the only ones a typed word can mean."""
        rows = self.open.items()
        return [i for i, s in rows if s["asking"] and kind in (None, s["call"])]

    def resolve(self, room: str, place: str, verb: str = "leave") -> tuple[str | None, bool]:
        """What a typed word means here: (incident, needs_asking_which). `transport` only ever
        means a transport call, `leave` only a leave call."""
        asking = self.asking(CALLS[verb][0])
        if place:
            word = place.lower()
            hits = [i for i in asking if word == i.lower() or word in self.open[i]["name"].lower()]
        else:
            here = [i for i in asking if self.open[i]["room"] == room]
            hits = here if room != OPS else asking
        return (hits[0], False) if len(hits) == 1 else (None, len(hits) > 1)

    def which(self, verb: str = "leave") -> str:
        kind = CALLS[verb][0]
        one, two = WORDS[kind]
        keys = map(self.key, self.asking(kind))
        ways = ", ".join(f"`{one} {k}` or `{two} {k}`" for k in keys)
        return f"🙋 Two calls are open. Reply: {ways}."

    def confirm(self, iid: str, verb: str, minutes: str) -> tuple[str, str]:
        s = self.open[iid]
        if verb == "leave":
            return s["room"], f"✅ {s['name']}: heading back to patrol."
        if verb in ("transport", "treat"):
            s["asking"] = False
            what = "transport to the Hospital" if verb == "transport" else "treating on scene"
            return s["room"], f"✅ {s['name']}: Duty Doctor confirms {what}."
        s["human_stayed"] = True
        tail = f" The sim sets how long, not {minutes} min." if minutes else ""
        return s["room"], f"✅ {s['name']}: staying a little longer.{tail}"

    def line(self, event: dict, moment: Moment) -> str:
        return moment.text
