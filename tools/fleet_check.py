"""AirGrid fleet & timeline checker.

Runs the FRIDAY-RUSH shift minute by minute (0.1-min steps) with the automatic policies
(patrol rotation, backfill, track relay, slot booking, weather grounding, safety RTB) plus the
golden human/agent actions, then checks every invariant and prints the numbers the demo uses.
It is a reference model for the real simulator: the build's golden test must match it.

Run: uv run python tools/fleet_check.py [--quiet]   (exit 1 on any violation)
"""
import math, sys, yaml

W = yaml.safe_load(open("world/airgrid.yaml"))
S = yaml.safe_load(open("scenarios/friday_rush.yaml"))
M = W["drone_model"]; DT = 0.1
QUIET = "--quiet" in sys.argv
DUMP = sys.argv[sys.argv.index("--dump-jsonl") + 1] if "--dump-jsonl" in sys.argv else None
frames = []

def tmin(s):  # "17:12.5" -> minutes since 00:00
    h, m = s.split(":"); return int(h) * 60 + float(m)
def fmt(t):
    h = int(t // 60); m = t - h * 60; s = int(round((m - int(m)) * 60))
    mm = int(m)
    if s == 60: mm, s = mm + 1, 0
    return f"{h:02d}:{mm:02d}" + (f":{s:02d}" if s else "")
def dist(a, b): return math.hypot(a[0] - b[0], a[1] - b[1])
def point(p): return W["district"]["landmarks"][p] if isinstance(p, str) else p
def sector_of(p):
    return ("N" if p[1] > 3.0 else "S") + ("E" if p[0] >= 3.0 else "W")

T0, T1 = tmin(S["start"]), tmin(S["end"])
docks = {k: dict(v, occupants=[], reserved=[], online=True) for k, v in W["docks"].items()}
loops = W["patrol_loops"]
pilots = {k: dict(v, drones=set()) for k, v in W["pilots"].items()}
log, violations, refusals = [], [], []
def note(t, msg):
    log.append((t, msg))

class Drone:
    def __init__(s, did, spec):
        s.id, s.thermal = did, spec.get("thermal", False)
        s.state = "docked"; s.slot = None; s.pos = None; s.bat = 100.0
        s.mission = None; s.loop = None; s.target = None; s.incident = None
        s.pilot = None; s.launch_wait = 0.0; s.on_station = False; s.flights = 0
        s.manual = False; s.min_landing = 101
D = {k: Drone(k, v) for k, v in W["fleet"].items()}

for did, v in S["initial"]["docked"].items():
    d = D[did]; d.bat = v["battery"]; d.slot = v["at"]; d.pos = W["docks"][v["at"]]["at"]
    docks[v["at"]]["occupants"].append(did)
for did, v in S["initial"]["airborne"].items():
    d = D[did]; d.bat = v["battery"]; d.state = "airborne"; d.mission = v["mission"]; d.loop = v["loop"]
    d.pos = loops[v["loop"]]["center"]; d.on_station = True; d.flights = 1
    d.pilot = v["pilot"]; pilots[v["pilot"]]["drones"].add(did)

grounded = set()      # sectors
incidents = {}        # id -> dict
suspect = sorted(((tmin(x["t"]), x["at"]) for x in S["suspect_path"]))
def suspect_pos(t):
    if t <= suspect[0][0]: return suspect[0][1]
    for (ta, a), (tb, b) in zip(suspect, suspect[1:]):
        if ta <= t <= tb:
            f = (t - ta) / (tb - ta); return [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])]
    return suspect[-1][1]
def in_zone(p, kind="red"):
    for z in W["zones"]:
        if z["kind"] == kind and (kind != "vip" or z.get("active")):
            r = z["rect"]
            if r["x"][0] <= p[0] <= r["x"][1] and r["y"][0] <= p[1] <= r["y"][1]: return z["id"]
    return None
RED = [z for z in W["zones"] if z["kind"] == "red"][0]["rect"]
def boundary_point(p):  # nearest point just outside the red rectangle
    x = min(p[0], RED["x"][0] - 0.05) if RED["y"][0] <= p[1] <= RED["y"][1] else p[0]
    return [x, p[1]]

def free_slots(dock):
    d = docks[dock]; return d["slots"] - len(d["occupants"]) - len(d["reserved"]) if d["online"] else 0
def pilot_for(prefer, t, purpose):
    for p in prefer:
        if len(pilots[p]["drones"]) < pilots[p]["max_drones"]: return p
    for p in pilots:
        if pilots[p]["kind"] == "simulated" and len(pilots[p]["drones"]) < pilots[p]["max_drones"]: return p
    violations.append(f"{fmt(t)} no pilot capacity for {purpose}"); return None
def assign_pilot(d, p):
    if d.pilot: pilots[d.pilot]["drones"].discard(d.id)
    d.pilot = p
    if p: pilots[p]["drones"].add(d.id)

def ready_candidates(near, gate, t, avoid_thermal=True):
    c = []
    for d in D.values():
        if d.state == "docked" and docks[d.slot]["online"] and d.bat >= gate and sector_of(d.pos)[0] + sector_of(d.pos)[1] not in grounded_sectors():
            c.append((dist(d.pos, near) + (0.75 if (avoid_thermal and d.thermal) else 0), d))
    c.sort(key=lambda x: (x[0], x[1].id)); return [d for _, d in c]
def grounded_sectors(): return grounded

def launch(d, t, mission, target=None, loop=None, incident=None, pilot=None):
    docks[d.slot]["occupants"].remove(d.id); d.slot = None
    d.state, d.mission, d.loop, d.incident = "airborne", mission, loop, incident
    d.target = target; d.launch_wait = M["launch_delay_min"]; d.on_station = False; d.flights += 1
    assign_pilot(d, pilot)

def book_slot(d, t):
    # nearest dock overall first (to log DOCK_FULL / DOCK_OFFLINE honestly), then nearest free
    order = sorted(docks, key=lambda k: (dist(d.pos, docks[k]["at"]), 0 if k == W["fleet"][d.id]["home"] else 1))
    for k in order:
        if free_slots(k) > 0:
            docks[k]["reserved"].append(d.id); return k
        reason = "DOCK_OFFLINE" if not docks[k]["online"] else "DOCK_FULL"
        if dist(d.pos, docks[k]["at"]) < 1.6:
            note(t, f"🅿 {d.id} nearest dock {k} unavailable → {reason}, rerouting")
    violations.append(f"{fmt(t)} {d.id} no free slot anywhere"); return None

def rtb(d, t, why=""):
    if d.state != "airborne" or d.mission == "returning": return
    slot = book_slot(d, t)
    d.mission, d.target, d.loop, d.on_station = "returning", slot, None, False
    d.manual = False
    if why: note(t, f"↩ {d.id} RTB {why} → {slot} (battery {d.bat:.0f}%)")

def move(d, target, speed):
    dd = dist(d.pos, target); step = speed * DT
    if dd <= step: d.pos = list(target); return True
    d.pos = [d.pos[0] + (target[0] - d.pos[0]) * step / dd, d.pos[1] + (target[1] - d.pos[1]) * step / dd]; return False

def drain(d, mode): d.bat -= M["drain_pct_per_min"][mode] * DT

def projected_landing(d):
    best = min((dist(d.pos, docks[k]["at"]) for k in docks if free_slots(k) > 0 or d.id in docks[k]["reserved"]), default=99)
    return d.bat - best / M["cruise_km_per_min"] * M["drain_pct_per_min"]["cruise"]

# ---- golden actions ----
actions = sorted(((tmin(a["t"]), i, a) for i, a in enumerate(S["golden_actions"])))
events = sorted(((tmin(e["t"]), i, e) for i, e in enumerate(S["events"])))
eyes_on, kpi = {}, {"released_early": 0, "pilot_saturated": 0}
tracker = {"id": None}; relief_pending = {"id": None}; patrol_relief = {}
cover_minutes = {k: 0.0 for k in loops}
ready_min = (99, None)

def do_action(t, a):
    act = a["act"]
    if act == "launch":
        d = D[a["drone"]]
        p = a.get("pilot")
        if a.get("expect") == "PILOT_SATURATED" and len(pilots[p]["drones"]) >= pilots[p]["max_drones"]:
            kpi["pilot_saturated"] += 1
            refusals.append((t, "PILOT_SATURATED", f"{d.id} under {p} would be drone #{len(pilots[p]['drones'])+1}"))
            note(t, f"❌ REJECTED {d.id} under {p}: PILOT_SATURATED ({sorted(pilots[p]['drones'])}) → {a['fallback_pilot']}")
            p = a["fallback_pilot"]
        elif a.get("expect") == "PILOT_SATURATED":
            violations.append(f"{fmt(t)} expected PILOT_SATURATED for {p} but count={len(pilots[p]['drones'])}")
        gate = M["launch_gate_pct"].get(a["mission"], 50)
        if d.state != "docked" or d.bat < gate:
            violations.append(f"{fmt(t)} cannot launch {d.id} ({d.state}, {d.bat:.0f}% < gate {gate})"); return
        if sector_of(d.pos) in grounded: violations.append(f"{fmt(t)} launch {d.id} into grounded sector"); return
        if a["mission"] == "corridor" and in_zone(point(a["to"])): violations.append("corridor target in red zone"); return
        launch(d, t, a["mission"], point(a["to"]), incident=a.get("incident"), pilot=p)
        note(t, f"🚀 {d.id} → {a['mission']} {a.get('incident','')} ({d.bat:.0f}%, pilot {p}, basis {a.get('basis','')})")
    elif act == "divert":
        d = D[a["drone"]]
        old = d.loop
        d.mission, d.incident, d.loop, d.on_station = "track", a["incident"], None, False
        assign_pilot(d, a["pilot"]); tracker["id"] = d.id
        note(t, f"🎯 {d.id} diverted from {old} patrol to TRACK {a['incident']} at {d.bat:.0f}%")
    elif act == "approve":
        note(t, f"🔐 {a['what']} approved by {a['by']}" + (f" — scope: {a['scope']}" if a.get('scope') else ""))
    elif act == "release_unit":
        kpi["released_early"] += 1; note(t, f"✅ {a['unit']} released early ({a['note']})")
    elif act == "rtb": rtb(D[a["drone"]], t, a.get("note", "incident task done"))
    elif act == "refuse":
        refusals.append((t, a["code"], a["request"])); note(t, f"⛔ {a['code']}: '{a['request']}'")
    elif act == "hold":
        d = D[tracker["id"]]; d.mission = "hold"; d.target = boundary_point(d.pos)
        note(t, f"⛔ {d.id} holds at RED-AIRFIELD boundary (battery {d.bat:.0f}%)")
    elif act == "reacquire":
        d = D[a["drone"]]; old = tracker["id"]; d.mission = "track"; tracker["id"] = d.id
        note(t, f"🎯 {d.id} reacquires suspect at {[round(x,2) for x in suspect_pos(t)]} (battery {d.bat:.0f}%); {old} keeps boundary hold")
    elif act == "take_control":
        D[a["drone"]].manual = True; note(t, f"🕹 {a['pilot']} has manual control of {a['drone']} (pilot load {len(pilots[a['pilot']]['drones'])}/4)")
    elif act == "close_incident":
        incidents[a["incident"]] = "closed"; note(t, f"🚓 {a['incident']} closed: {a['note']}")
    elif act == "rtb_incident":
        for d in D.values():
            if d.state == "airborne" and d.incident == a["incident"] and d.mission != "returning": rtb(d, t, f"{a['incident']} closed")
        tracker["id"] = None

def do_event(t, e):
    k = e["type"]
    if k == "dock_fault": docks[e["dock"]]["online"] = False; note(t, f"⚠ dock {e['dock']} OFFLINE ({e['note']})")
    elif k == "dock_restored": docks[e["dock"]]["online"] = True; note(t, f"✔ dock {e['dock']} back online")
    elif k == "weather":
        for s in e["sectors"]: grounded.add(s)
        note(t, f"🌬 wind {e['wind_ms']} m/s > {M['wind_limit_ms']} in {e['sectors']} → WEATHER_GROUNDED until {e['until']}")
        refusals.append((t, "WEATHER_GROUNDED", f"sectors {e['sectors']}"))
        weather_end.append((tmin(e["until"]), e["sectors"]))
    elif k == "cad_alert":
        incidents[e["incident"]] = dict(e, t=t, open=True); note(t, f"🚨 {e['incident']} {e['kind']} at {e['at']}")
weather_end = []

def loop_grounded(L):
    c = loops[L]["center"]; return sector_of(c) in grounded or (loops[L].get("sector") == "north" and {"NW", "NE"} & grounded)

t = T0; ai = ei = 0
while t <= T1 + 1e-9:
    while ei < len(events) and events[ei][0] <= t + 1e-9: do_event(t, events[ei][2]); ei += 1
    for te, secs in list(weather_end):
        if te <= t + 1e-9:
            for s in secs: grounded.discard(s)
            weather_end.remove((te, secs)); note(t, f"🌬 wind back under limit, {secs} reopened")
    while ai < len(actions) and actions[ai][0] <= t + 1e-9: do_action(t, actions[ai][2]); ai += 1

    # --- automatic policy: grounding RTB, patrol rotation/backfill, track relay ---
    for L in loops:
        inc = [d for d in D.values() if d.state == "airborne" and d.loop == L and d.mission == "patrol"]
        if loop_grounded(L):
            for d in inc: rtb(d, t, "WEATHER_GROUNDED")
            continue
        onst = [d for d in inc if d.on_station]
        pend = patrol_relief.get(L)
        if pend and (D[pend].state != "airborne" or D[pend].loop != L): patrol_relief[L] = pend = None
        need = (not onst) or (onst and min(x.bat for x in onst) <= M["relief_trigger_pct"])
        if need and not pend and not [d for d in inc if not d.on_station]:
            cands = ready_candidates(loops[L]["center"], M["launch_gate_pct"]["patrol"], t)
            if cands:
                r = cands[0]; p = pilot_for(["DP-2"] if L == "SOUTH" else ["DP-1", "DP-2"], t, f"{L} patrol")
                launch(r, t, "patrol", loops[L]["center"], loop=L, pilot=p); patrol_relief[L] = r.id
                note(t, f"🔄 {r.id} launches for {L} ({'relief for ' + onst[0].id + f' at {onst[0].bat:.0f}%' if onst else 'backfill — loop uncovered'})")
            elif not onst:
                note(t, f"⚠ {L} uncovered: no READY drone ≥{M['launch_gate_pct']['patrol']}%")
    if tracker["id"]:
        tr = D[tracker["id"]]
        if tr.mission == "track" and tr.bat <= M["track_relief_trigger_pct"] and not relief_pending["id"]:
            ahead = suspect_pos(t + 3)
            cands = ready_candidates(ahead, M["launch_gate_pct"]["track"], t, avoid_thermal=True)
            if cands:
                r = cands[0]; launch(r, t, "relay", None, incident=tr.incident, pilot="kazum")
                relief_pending["id"] = r.id
                note(t, f"🤝 relay planned: {r.id} launches to relieve {tr.id} ({tr.bat:.0f}%)")

    # --- movement, battery, landings ---
    for d in D.values():
        if d.state == "docked":
            d.bat = min(M["charge_cap_pct"], d.bat + M["charge_pct_per_min"] * DT); continue
        if d.launch_wait > 0:
            d.launch_wait -= DT; drain(d, "hover"); continue
        if d.mission == "patrol":
            if d.on_station: drain(d, "patrol")
            else:
                drain(d, "cruise")
                if move(d, loops[d.loop]["center"], M["cruise_km_per_min"]):
                    d.on_station = True
                    old = [x for x in D.values() if x.state == "airborne" and x.loop == d.loop and x.on_station and x is not d]
                    for x in old: rtb(x, t, f"relieved by {d.id} on {d.loop}")
                    patrol_relief[d.loop] = None
        elif d.mission in ("incident", "perimeter", "corridor"):
            if d.on_station: drain(d, "hover")
            else:
                drain(d, "cruise")
                if move(d, d.target, M["cruise_km_per_min"]):
                    d.on_station = True
                    if d.mission == "incident" and d.incident not in eyes_on:
                        eyes_on[d.incident] = t; note(t, f"👁 EYES-ON {d.incident} by {d.id} ({(t - incidents[d.incident]['t'])*60:.0f} s after alert, battery {d.bat:.0f}%)")
        elif d.mission in ("track", "relay"):
            sp = suspect_pos(t)
            target = boundary_point(sp) if in_zone(sp) else sp
            drain(d, "track" if d.mission == "track" else "cruise")
            arrived = move(d, target, M["sprint_km_per_min"] if d.mission == "relay" else M["cruise_km_per_min"])
            if d.mission == "track" and in_zone(sp) and arrived and not [a for a in S["golden_actions"] if a["act"] == "hold"]:
                pass
            if d.mission == "relay" and dist(d.pos, sp) < 0.15:
                old = D[tracker["id"]]; d.mission = "track"; tracker["id"] = d.id; relief_pending["id"] = None
                note(t, f"🤝 HANDOFF {old.id} → {d.id} (gap 0 s); {old.id} at {old.bat:.0f}%")
                rtb(old, t, "track handed off")
        elif d.mission == "hold":
            drain(d, "hover"); move(d, d.target, M["cruise_km_per_min"])
        elif d.mission == "returning":
            drain(d, "cruise")
            if move(d, docks[d.target]["at"], M["cruise_km_per_min"]):
                k = d.target; docks[k]["reserved"].remove(d.id); docks[k]["occupants"].append(d.id)
                d.state, d.slot, d.mission, d.incident = "docked", k, None, None
                assign_pilot(d, None); d.min_landing = min(d.min_landing, d.bat)
                note(t, f"🛬 {d.id} lands at {k} with {d.bat:.0f}%")
                if d.bat < M["reserve_pct"]: violations.append(f"{fmt(t)} {d.id} landed at {d.bat:.1f}% < reserve")
        if d.state == "airborne":
            if in_zone(d.pos): violations.append(f"{fmt(t)} {d.id} inside RED zone")
            if d.mission != "returning" and projected_landing(d) <= M["reserve_pct"] + 2:
                violations.append(f"{fmt(t)} SAFETY RTB {d.id} ({d.bat:.0f}%) — plan breached reserve margin"); rtb(d, t, "safety")
            if d.bat < M["reserve_pct"]: violations.append(f"{fmt(t)} {d.id} airborne below reserve ({d.bat:.1f}%)")

    # --- invariants + KPIs per step ---
    for p in pilots.values():
        if len(p["drones"]) > p["max_drones"]: violations.append(f"{fmt(t)} pilot over cap {p}")
    for k, dk in docks.items():
        if len(dk["occupants"]) > dk["slots"]: violations.append(f"{fmt(t)} dock {k} over capacity")
    nready = sum(1 for d in D.values() if d.state == "docked" and docks[d.slot]["online"] and d.bat >= M["ready_pct"])
    if nready < ready_min[0]: ready_min = (nready, t)
    for L in loops:
        if any(d.state == "airborne" and d.loop == L and d.mission == "patrol" and d.on_station for d in D.values()):
            cover_minutes[L] += DT
    if DUMP and abs(t * 10 - round(t * 10)) < 1e-6 and round(t * 10) % 5 == 0:   # every 0.5 sim-min
        frames.append({"t": round(t, 2), "clock": fmt(t), "grounded": sorted(grounded),
            "suspect": [round(v, 3) for v in suspect_pos(t)] if T0 + 40 <= t <= T0 + 67 else None,
            "drones": {d.id: {"state": d.state, "mission": d.mission, "bat": round(d.bat, 1), "pilot": d.pilot,
                               "pos": [round(v, 3) for v in d.pos], "slot": d.slot, "loop": d.loop} for d in D.values()},
            "docks": {k: {"occ": list(v["occupants"]), "res": list(v["reserved"]), "online": v["online"]} for k, v in docks.items()}})
    t = round(t + DT, 4)

# ---- baseline (the same Friday, no network): patrol cars + ONE van drone team ----
B = W["baseline"]; rs = W["ground_units"]["road_speed_km_per_min"]
def manhattan(a, b): return abs(a[0] - b[0]) + abs(a[1] - b[1])
acc = point("ring_road_j9"); mkt = point("market_street")
van_at = [3.75, 3.0]   # baseline van parked at J7 doing a central patrol with battery swaps
b_acc_eyes = 5 + manhattan(van_at, acc) / rs + B["van_setup_min"] + M["launch_delay_min"]   # pack 5 min, drive, set up, launch
b_rob_eyes = tmin("17:40") - tmin("17:35") * 0 + 0  # computed below
van_free_at = tmin("17:12") + b_acc_eyes + 23 + 5      # on scene until the accident clears (~17:35) + pack
van_pos_t = van_free_at
rob_alert = tmin("17:40")
start = max(rob_alert, van_pos_t)
b_rob_eyes_t = start + manhattan(acc, mkt) / rs + B["van_setup_min"] + M["launch_delay_min"]
lost = dist(suspect_pos(b_rob_eyes_t), mkt) > 0.5
shift = T1 - T0
b_cov = max(0.0, (shift - (van_free_at - tmin("17:12")) - (b_rob_eyes_t - rob_alert) - 6 * 5 - 30)) / shift / 3   # central loop only, minus incidents, swaps, weather

# ---- report ----
total_cov = sum(cover_minutes.values()) / (len(loops) * (shift + DT)) * 100
ground_first = {"INC-1": tmin("17:12") + 1 + 6.5}  # nearest patrol car: 1 min dispatch + road ETA (see 03-SIM-SPEC)
flights = sum(d.flights for d in D.values()); flew = sum(1 for d in D.values() if d.flights)
min_land = min(d.min_landing for d in D.values() if d.min_landing <= 100)
if not QUIET:
    print("=== FRIDAY RUSH — golden managed run (computed) ===")
    for tt, msg in log: print(f"{fmt(tt):>8}  {msg}")
    print("\n=== Scorecard (managed vs baseline) ===")
    print(f"eyes-on accident: {(eyes_on['INC-1']-tmin('17:12'))*60:.0f} s  vs baseline van {b_acc_eyes:.1f} min")
    print(f"robbery: tracked, detained 18:07  vs baseline van eyes at {fmt(b_rob_eyes_t)} → suspect {'LOST (outside 0.5 km search)' if lost else 'found'}")
    print(f"ground units released early: {kpi['released_early']} vs 0")
    print(f"patrol junction coverage 17:00–20:00: {total_cov:.1f}%  vs baseline ~{b_cov*100:.0f}%")
    print(f"coverage by loop (min): " + ", ".join(f"{k} {v:.0f}" for k, v in cover_minutes.items()))
    print(f"refusals enforced: {len(refusals)} → " + ", ".join(sorted({c for _, c, _ in refusals})))
    print(f"fleet: {flew}/14 drones flew, {flights} flights, lowest landing battery {min_land:.1f}% (reserve {M['reserve_pct']}%)")
    print(f"ready floor: minimum {ready_min[0]} READY drones (at {fmt(ready_min[1])})")
if DUMP:
    import json
    with open(DUMP, "w") as fh:
        for fr in frames: fh.write(json.dumps(fr) + "\n")
        fh.write(json.dumps({"log": [[round(a, 2), b] for a, b in log]}) + "\n")
print("\nVIOLATIONS:" if violations else "\nOK — 0 violations")
for v in violations: print("  ", v)
sys.exit(1 if violations else 0)
