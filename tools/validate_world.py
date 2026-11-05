"""Validate world/airgrid.yaml + scenarios. Run: uv run python tools/validate_world.py  (exit 1 on errors)"""
import sys, yaml
W = yaml.safe_load(open("world/airgrid.yaml"))
errs, n = [], 0
def check(cond, msg):
    global n; n += 1
    if not cond: errs.append(msg)

size = W["district"]["size_km"]
def inside(p): return 0 <= p[0] <= size[0] and 0 <= p[1] <= size[1]
def in_rect(p, r): return r["x"][0] <= p[0] <= r["x"][1] and r["y"][0] <= p[1] <= r["y"][1]
red = [z for z in W["zones"] if z["kind"] == "red"]
check(len(red) == 1, "exactly one red zone")

for k, d in W["docks"].items():
    check(inside(d["at"]), f"dock {k} outside district")
    check(not any(in_rect(d["at"], z["rect"]) for z in W["zones"] if z["kind"] != "vip"), f"dock {k} inside red/no-record zone")  # P1 sits in VIP-TEMP on purpose (L22)
    check(d["slots"] in (1, 3), f"dock {k} slots 1 or 3")
slots = sum(d["slots"] for d in W["docks"].values())
check(slots == 18, f"18 slots expected, got {slots}")
check(len(W["fleet"]) == 14, "14 drones")
for k, f in W["fleet"].items():
    check(f["home"] in W["docks"], f"{k} home missing")
homes = {}
for k, f in W["fleet"].items(): homes[f["home"]] = homes.get(f["home"], 0) + 1
for h, c in homes.items(): check(c <= W["docks"][h]["slots"], f"too many homes at {h}")
for L, l in W["patrol_loops"].items():
    check(inside(l["center"]), f"loop {L} centre outside")
    check(not any(in_rect(l["center"], z["rect"]) for z in red), f"loop {L} in red zone")
M = W["drone_model"]
check(M["reserve_pct"] < min(M["relief_trigger_pct"], M["track_relief_trigger_pct"]), "reserve below relief triggers")
check(M["ready_pct"] >= M["launch_gate_pct"]["patrol"], "ready ≥ patrol gate")
check(abs((95 - 15) / M["charge_pct_per_min"] - 27) < 0.5, "charge 15→95 ≈ 27 min (Dock 3 class)")
check(M["max_flight_min"] == 54 and M["wind_limit_ms"] == 12, "Dock 3 class limits")
pil = W["pilots"]
check(all(p["max_drones"] == 4 for p in pil.values()), "max 4 drones per pilot")
check(not any(p.get("kind") == "auto_pool" for p in pil.values()), "no auto pool")
check(sum(1 for p in pil.values() if p["kind"] == "human") >= 1, "at least one human pilot")

for f in ["scenarios/friday_rush.yaml", "scenarios/quiet_shift.yaml"]:
    S = yaml.safe_load(open(f))
    ini = S["initial"]
    ids = list(ini["airborne"]) + list(ini["docked"])
    check(sorted(ids) == sorted(W["fleet"]), f"{f}: initial covers all 14 drones exactly once")
    occ = {}
    for d, v in ini["docked"].items(): occ[v["at"]] = occ.get(v["at"], 0) + 1
    for k, c in occ.items(): check(c <= W["docks"][k]["slots"], f"{f}: {k} over capacity at start")
    load = {}
    for d, v in ini["airborne"].items():
        load[v["pilot"]] = load.get(v["pilot"], 0) + 1
        check(v["loop"] in W["patrol_loops"], f"{f}: {d} loop")
    for p, c in load.items(): check(c <= pil[p]["max_drones"], f"{f}: pilot {p} over cap at start")
    for p in S.get("suspect_path", []): check(inside(p["at"]), f"{f}: suspect point outside district")

print(f"{n} checks, {len(errs)} errors")
for e in errs: print("  ERROR", e)
sys.exit(1 if errs else 0)
