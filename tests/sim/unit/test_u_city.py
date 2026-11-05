"""City mode (docs/city-mode.md, L56): lifecycle, roads, decisions, relief, zones, API."""

import json

import pytest
from fastapi.testclient import TestClient

from sim import city as ct
from sim import rules
from sim.app import S, app
from sim.engine import Engine
from sim.geo import DT, in_rect
from sim.world import load_world

W = load_world()
C = W.raw["city"]
RED = next(z for z in W.raw["zones"] if z["kind"] == "red")["rect"]
KEY = "test-city-key"
H = {"X-Game-Key": KEY}


def new_city(seed="T-1"):
    scen = ct.scenario(W, seed)
    eng = Engine(W, scen, seed=scen["seed"], mode="city")
    c = ct.City(eng)
    c.start()
    return c


def run(c, minutes):
    for _ in range(round(minutes / DT)):
        c.eng.step()
        check(c)


def check(c):
    assert c.eng.violations == []
    for v in c.vehicles.values():
        assert c.roads.on_road(v.pos), (v.id, v.pos)
    for d in c.eng.drones.values():
        assert not in_rect(d.pos, RED), (d.id, d.pos)
        if d.state == "airborne":
            assert d.bat >= W.model["reserve_pct"], d.id


def until(c, cond, limit=120):
    for _ in range(round(limit / DT)):
        if cond():
            return
        c.eng.step()
        check(c)
    pytest.fail("condition not reached")


def stage(c, iid):
    i = c.active.get(iid)
    return i["stage"] if i else "closed"


def to_stage(c, iid, name):
    until(c, lambda: stage(c, iid) == name)


def treat_through(c, iid):
    """Past the doctor's call, if this incident gets one, to the leave-or-stay call."""
    until(c, lambda: stage(c, iid) in ("care_call", "watching"))
    if stage(c, iid) == "care_call":
        c.decide(iid, "treat")
    to_stage(c, iid, "watching")


def fire(c, injuries=0, **force):
    c._roll_injuries = lambda _inc: injuries
    r = c.trigger("fire")
    c.active[r["id"]].update(force)
    return r["id"]


def accident(c, injuries=2, **force):
    c._roll_injuries = lambda _inc: injuries
    r = c.trigger("accident")
    c.active[r["id"]].update(force)
    return r["id"]


# ---- world data
def test_world_data_is_valid():
    roads = ct.Roads(W.raw["district"]["junctions"])
    assert {s["name"] for s in C["stations"].values()} == {
        "Fire Station",
        "Hospital",
        "Police Station",
    }
    for s in C["stations"].values():
        assert roads.on_road(s["road"])
        for z in W.raw["zones"]:
            assert not in_rect(s["at"], z["rect"]) and not in_rect(s["road"], z["rect"])
    for scenes in C["scenes"].values():
        for s in scenes:
            assert roads.on_road(s["at"])
            assert not any(in_rect(s["at"], z["rect"]) for z in W.raw["zones"])
    for spec in C["work_min"].values():
        assert 30 <= spec["min"] / C["sim_min_per_s"] <= 45


def test_roam_routes_clear_of_red_and_cover_all_sectors():
    j = W.raw["district"]["junctions"]
    seen = set()
    for route in C["roam"]:
        pts = [j[x] for x in route]
        seen |= set(route)
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            assert rules.segment_hits_rect(a, b, RED) is False
    assert seen == set(j)


def test_bad_scene_refused():
    import copy

    w = copy.deepcopy(W)
    w.raw["city"]["scenes"]["fire"][0]["at"] = [5.8, 3.0]
    eng = Engine(w, ct.scenario(w), mode="city")
    with pytest.raises(ValueError):
        ct.City(eng)


# ---- calm
def test_roamers_always_move_and_vehicles_stay_parked():
    c = new_city()
    run(c, 2)
    for _ in range(600):
        was = {
            d.id: list(d.pos)
            for d in c.eng.drones.values()
            if d.mission == "roam" and d.launch_wait <= 0  # lid + climb happens first
        }
        assert len(was) >= 1
        c.eng.step()
        check(c)
        for did, p in was.items():
            d = c.eng.drones[did]
            if d.mission == "roam":
                assert d.pos != p, did
    assert all(v.state == "parked" for v in c.vehicles.values())
    assert not any(d.recording for d in c.eng.drones.values())


def test_long_calm_run_relief_keeps_every_route_flown():
    c = new_city()
    run(c, 300)
    routes = {r["route"] for r in c.roam.values()}
    assert routes == set(range(len(C["roam"])))
    assert sum(d.flights for d in c.eng.drones.values()) > len(C["roam"])
    assert c.eng.kpi.reserve_breaches == 0


def test_busy_day_keeps_every_rule():
    """Five hours of back-to-back incidents, always answered at once."""
    c = new_city("BUSY")
    rng = __import__("random").Random(7)
    for _ in range(3000):
        c.eng.step()
        check(c)
        if not rng.randrange(120) and len(c.active) < 2:
            c.trigger(rng.choice(["fire", "accident"]))
        for i in list(c.active.values()):
            if i["decision"]:
                care = i["decision"]["kind"] == "treat_transport"
                c.decide(i["id"], rng.choice(["transport", "treat"]) if care else "leave")
    assert c.handled >= 5
    assert c.eng.kpi.reserve_breaches == 0 and c.eng.kpi.red_zone_entries == 0
    assert c.eng.kpi.safety_rtbs == 0


def test_low_roamer_with_no_relief_heads_home_early():
    c = new_city()
    run(c, 3)
    d = next(c.eng.drones[i] for i in c.roam)
    for x in c.eng.drones.values():
        if x.state == "docked":
            x.bat = 10.0
    d.bat = C["home_pct"]
    c.eng.step()
    assert d.mission == "returning" and c.eng.kpi.safety_rtbs == 0


def test_no_clock_ending():
    c = new_city()
    assert not c.eng.done and c.eng.T1 > c.eng.T0 + 365 * 24 * 60
    assert c.view()["clock_label"].startswith(C["day"])


# ---- lifecycle
def test_fire_lifecycle():
    c = new_city()
    run(c, 3)
    iid = fire(c, at_risk=False)
    inc = c.active[iid]
    assert inc["stage"] == "alert" and inc["drone"]
    assert c.eng.drones[inc["drone"]].mission == "incident"
    to_stage(c, iid, "responding")
    assert inc["response_s"] > 0
    until(c, lambda: inc["vehicles"])
    v = c.vehicles[inc["vehicles"][0]]
    assert v.kind == "fire_engine" and v.state == "en_route" and v.station == "fire"
    to_stage(c, iid, "working")
    assert v.state == "working"
    run(c, 2)
    view = c.incident_view(inc)
    assert 0 < view["progress_pct"] < 100 and view["progress_label"].startswith("Extinguishing")
    to_stage(c, iid, "watching")
    assert v.state == "returning"
    assert c.incident_view(inc)["decision"]["options"][0]["id"] == "leave"
    drone = c.eng.drones[inc["drone"]]
    assert drone.recording
    out = c.decide(iid, "leave")
    assert out["stage"] == "closed" and c.eng.incidents[iid]["status"] == "closed"
    assert drone.mission == "roam" and c.handled == 1
    run(c, 1)
    assert not drone.recording
    r = out["report"]
    assert r["recording"]["from"] and r["who"] == [v.id] and len(r["timeline"]) >= 5
    until(c, lambda: v.state == "parked")
    assert v.pos == v.home


@pytest.mark.parametrize("injuries,kinds", [(2, {"police_car", "ambulance"}), (0, {"police_car"})])
def test_accident_lifecycle(injuries, kinds):
    c = new_city()
    run(c, 3)
    iid = accident(c, injuries=injuries, at_risk=False)
    inc = c.active[iid]
    to_stage(c, iid, "working")
    until(c, lambda: len(inc["jobs"]) == len(kinds))
    assert {c.vehicles[v].kind for v in inc["vehicles"]} == kinds
    treat_through(c, iid)
    assert all(j["done"] for j in inc["jobs"].values())
    assert c.decide(iid, "leave")["stage"] == "closed"


def test_two_at_once():
    c = new_city()
    run(c, 3)
    a, b = fire(c, at_risk=False), accident(c, at_risk=False)
    assert c.active[a]["drone"] != c.active[b]["drone"]
    assert c.active[a]["scene"] != c.active[b]["scene"]
    to_stage(c, a, "watching")
    treat_through(c, b)
    c.decide(a, "leave")
    assert stage(c, b) == "watching"
    c.decide(b, "leave")
    assert c.handled == 2 and c.view()["counters"]["avg_response_s"] > 0


def test_three_fires_queue_for_two_engines():
    c = new_city()
    run(c, 3)
    ids = [fire(c, at_risk=False) for _ in range(3)]
    for i in ids:
        to_stage(c, i, "watching")
        c.decide(i, "leave")
    assert c.handled == 3


def test_vehicle_path_only_turns_at_junctions():
    c = new_city()
    path = c.roads.route(C["stations"]["fire"]["road"], [5.25, 2.0])
    corners = {tuple(p) for p in W.raw["district"]["junctions"].values()}
    for a, b in zip(path, path[1:], strict=False):
        assert a[0] == b[0] or a[1] == b[1]
        assert tuple(a) in corners or a == path[0]


def types(c):
    return [e["type"] for e in c.eng.events]


def test_police_first_then_ambulance_only_after_assessment():
    c = new_city()
    run(c, 3)
    iid = accident(c, injuries=2, at_risk=False)
    inc = c.active[iid]
    to_stage(c, iid, "responding")
    until(c, lambda: inc["vehicles"])
    assert [c.vehicles[v].kind for v in inc["vehicles"]] == ["police_car"]
    amb = [v for v in c.vehicles.values() if v.kind == "ambulance"]
    assert all(v.state == "parked" for v in amb) and inc["injuries"] is None
    assert "ambulance_requested" not in types(c)
    until(c, lambda: inc["jobs"])
    c.eng.step()
    assert inc["injuries"] == 2 and "ambulance_requested" in types(c)
    until(c, lambda: any(v.state == "en_route" for v in amb))
    e = next(x for x in c.eng.events if x["type"] == "ambulance_requested")
    assert e["payload"]["injuries"] == 2 and e["payload"]["incident"] == iid
    assert c.incident_view(inc)["progress_pct"] == 0  # the ambulance's job hasn't started
    treat_through(c, iid)
    assert c.decide(iid, "leave")["report"]["injuries"] == 2


def test_no_injuries_no_ambulance_and_it_is_stated():
    c = new_city()
    run(c, 3)
    iid = accident(c, injuries=0, at_risk=False)
    to_stage(c, iid, "watching")
    assert "ambulance_not_needed" in types(c) and "ambulance_requested" not in types(c)
    kinds = {c.vehicles[v].kind for v in c.active[iid]["vehicles"]}
    assert kinds == {"police_car"}


@pytest.mark.parametrize("n,want", [(1, "ambulance_requested"), (0, "ambulance_not_needed")])
def test_fire_assessed_after_the_engine_arrives(n, want):
    c = new_city()
    run(c, 3)
    iid = fire(c, injuries=n, at_risk=False)
    inc = c.active[iid]
    to_stage(c, iid, "responding")
    until(c, lambda: inc["vehicles"])
    assert want not in types(c)
    to_stage(c, iid, "watching")
    assert want in types(c)
    kinds = {c.vehicles[v].kind for v in inc["vehicles"]}
    assert kinds == ({"fire_engine", "ambulance"} if n else {"fire_engine"})


def test_assessment_waits_for_the_drone_on_station():
    c = new_city()
    run(c, 3)
    iid = accident(c, injuries=1, at_risk=False)
    inc = c.active[iid]
    to_stage(c, iid, "responding")
    until(c, lambda: inc["jobs"])
    inc["injuries"] = None
    c.eng.drones[inc["drone"]].on_station = False
    c._assess(inc)
    assert inc["injuries"] is None


@pytest.mark.parametrize("first", [[], ["fire"], ["fire", "fire"]])
def test_default_seed_first_accident_has_injuries(first):
    c = new_city(C["seed"])
    run(c, 2)
    for k in first:
        c.trigger(k)
    iid = c.trigger("accident")["id"]
    inc = c.active[iid]
    until(c, lambda: inc["injuries"] is not None)
    assert inc["injuries"] > 0


def test_assessment_is_seeded():
    def roll(seed, n=0):
        c = new_city(seed)
        return c._roll_injuries({"kind": "accident", "serial": n})

    assert roll("A") == roll("A")
    rolls = {roll(f"S{i}") for i in range(30)}
    assert 0 in rolls and any(rolls - {0}) and max(rolls) <= C["injuries_max"]


def test_incident_name_and_place():
    c = new_city()
    run(c, 3)
    a, b = fire(c, at_risk=False), accident(c, injuries=0, at_risk=False)
    ia, ib = c.active[a], c.active[b]
    assert ia["name"] == f"{ia['scene']} fire" and c.incident_view(ib)["name"].endswith("accident")
    to_stage(c, a, "watching")
    to_stage(c, b, "watching")
    frag = ib["scene"].split()[0].upper()
    assert c.decide(None, "leave", place=frag)["incident"] == b
    assert c.decide(None, "stay", place="fire")["incident"] == a
    with pytest.raises(ct.CityError) as e:
        c.decide(None, "leave", place="nowhere")
    assert e.value.rejection.code == "NOT_FOUND"


def test_ambiguous_place_is_not_guessed():
    c = new_city()
    run(c, 3)
    x, y = fire(c, at_risk=False), fire(c, at_risk=False)
    to_stage(c, x, "watching")
    to_stage(c, y, "watching")
    with pytest.raises(ct.CityError):
        c.decide(None, "leave", place="fire")


# ---- ambulance escort (L61)
def _care_call(seed="T-1", transport=True, traffic=True, at_risk=False):
    c = new_city(seed)
    c.cfg = {
        **c.cfg,
        "transport_chance": 1.0 if transport else 0.0,
        "traffic_chance": 1.0 if traffic else 0.0,
    }
    run(c, 3)
    iid = accident(c, injuries=2, at_risk=at_risk)
    to_stage(c, iid, "care_call")
    return c, iid


def _transporting(**kw):
    c, iid = _care_call(**kw)
    c.decide(iid, "transport")
    to_stage(c, iid, "transporting")
    return c, iid


def test_doctor_call_shape_and_recommendation():
    c, iid = _care_call(transport=False)
    assert "care_assessed" in types(c)
    d = c.incident_view(c.active[iid])["decision"]
    assert d["kind"] == "treat_transport" and d["role"] == "duty_doctor"
    assert {o["id"] for o in d["options"]} == {"transport", "treat"}
    assert "treating on scene" in d["prompt"]
    with pytest.raises(ValueError):
        c.decide(iid, "leave")


def test_leave_stay_call_shape():
    c, iid = _watching()
    d = c.incident_view(c.active[iid])["decision"]
    assert d["kind"] == "leave_stay" and d["role"] == "incident_lead"
    with pytest.raises(ValueError):
        c.decide(iid, "transport")


def test_doctor_call_stays_open_and_crew_keeps_treating():
    c, iid = _care_call()
    inc = c.active[iid]
    amb = c.vehicles[c._ambulance(inc)]
    run(c, 60)
    assert inc["stage"] == "care_call" and inc["decision"] and amb.state == "working"
    assert not inc["jobs"][amb.id]["done"]


def test_treat_on_scene_no_escort():
    c, iid = _care_call(transport=True)  # the doctor overrules the recommendation
    inc = c.active[iid]
    c.decide(iid, "treat")
    to_stage(c, iid, "watching")
    assert inc["escort"] is None and "transport_started" not in types(c)
    r = c.decide(iid, "leave")["report"]
    assert r["care"] == "treat" and r["transport"] is None


def test_transport_with_escort_no_traffic():
    c, iid = _transporting(traffic=False)
    inc = c.active[iid]
    amb = c.vehicles[inc["escort"]["ambulance"]]
    assert amb.state == "transporting" and "transport_started" in types(c)
    until(c, lambda: inc["escort"]["drone"])
    d = c.eng.drones[inc["escort"]["drone"]]
    assert d.mission == "escort" and d.id != inc["drone"] and not d.recording
    run(c, 2)
    assert c.view()["traffic"] == []
    aim = c._ahead(amb)
    assert ct.dist(d.pos, aim) < 0.2 and c.roads.on_road(aim)
    assert c.eng.drones[inc["drone"]].mission == "incident"  # the scene stays watched
    to_stage(c, iid, "watching")
    assert amb.state == "parked" and amb.pos == amb.home
    assert "patient_delivered" in types(c) and d.mission == "roam"
    r = c.decide(iid, "leave")["report"]
    assert r["care"] == "transport" and r["transport"]["transport_min"] > 0
    assert r["transport"]["delay_min"] == 0 and r["transport"]["saved_min"] == 0


def test_block_is_hidden_until_the_drone_sees_it():
    c, iid = _transporting()
    b = c.active[iid]["escort"]["block"]
    assert b and b["status"] == "pending"
    assert c.view()["traffic"] == []
    assert c.incident_view(c.active[iid])["escort"]["blocked_at"] is None
    assert "traffic_blocked" not in types(c)


def test_block_is_a_junction_on_the_route_and_police_clear_it():
    c, iid = _transporting()
    inc = c.active[iid]
    e = inc["escort"]
    amb = c.vehicles[e["ambulance"]]
    corners = [list(p) for p in W.raw["district"]["junctions"].values()]
    assert e["block"]["at"] in corners
    until(c, lambda: e["block"]["status"] != "pending")
    d = c.eng.drones[e["drone"]]
    assert ct.dist(d.pos, e["block"]["at"]) <= C["traffic_sight_km"]  # the drone saw it first
    assert amb.state == "transporting" and "traffic_blocked" in types(c)
    assert "police_requested" in types(c)
    until(c, lambda: amb.state == "blocked")
    assert inc["stage"] == "blocked" and e["status"] == "blocked"
    if e["block"]["status"] == "blocked":
        until(c, lambda: e["block"]["status"] == "clearing")
    car = c.vehicles[e["block"]["car"]]
    assert car.kind == "police_car" and car.pos == e["block"]["at"] and car.state == "working"
    run(c, 2)
    (t,) = c.view()["traffic"]
    assert t["status"] == "clearing" and 0 < t["progress_pct"] < 100
    view = c.incident_view(inc)["escort"]
    assert view["blocked_at"] == e["block"]["at"] and view["delay_s"] > 0
    until(c, lambda: e["block"]["status"] == "cleared")
    assert amb.state == "transporting" and "traffic_cleared" in types(c)
    to_stage(c, iid, "watching")
    r = c.decide(iid, "leave")["report"]
    tr = r["transport"]
    assert tr["delay_min"] > 0
    assert tr["saved_min"] == pytest.approx(
        C["traffic_natural_clear_min"] - tr["delay_min"], abs=0.1
    )
    assert car.id in r["who"] and c.view()["traffic"] == []
    until(c, lambda: car.state == "parked")


def test_no_free_police_ambulance_waits():
    c, iid = _transporting()
    inc = c.active[iid]
    e = inc["escort"]
    amb = c.vehicles[e["ambulance"]]
    cars = [v for v in c.vehicles.values() if v.kind == "police_car"]
    for v in cars:
        v.state = "working"  # both tied up, the one at the scene included
        if v.id in inc["jobs"]:
            inc["jobs"][v.id]["min"] = 1e6
    until(c, lambda: amb.state == "blocked")
    run(c, 30)
    assert amb.state == "blocked" and e["block"]["status"] == "blocked" and not e["block"]["car"]
    assert types(c).count("police_requested") == 1
    cars[0].state = "parked"
    until(c, lambda: e["block"]["status"] == "cleared")
    assert amb.state == "transporting"
    assert e["delay_min"] > 30


def test_escort_relief_and_safety():
    c, iid = _transporting(traffic=False)
    e = c.active[iid]["escort"]
    until(c, lambda: e["drone"])
    old = c.eng.drones[e["drone"]]
    until(c, lambda: old.launch_wait <= 0 and old.mission == "escort")
    old.bat = W.model["relief_trigger_pct"]
    c.eng.step()
    new = e["relief"]
    assert new and new != e["drone"]
    while e["relief"]:
        assert old.mission == "escort"
        c.eng.step()
        check(c)
    assert e["drone"] == new and old.mission == "returning"
    c.eng.rtb(c.eng.drones[new], c.eng.now, "safety")
    c.eng.step()
    assert e["drone"] and e["drone"] != new


def test_default_seed_first_accident_shows_the_whole_escort():
    c = new_city(C["seed"])
    run(c, 2)
    iid = c.trigger("accident")["id"]
    inc = c.active[iid]
    to_stage(c, iid, "care_call")
    assert inc["care"]["recommend"] == "transport"
    c.decide(iid, "transport")
    to_stage(c, iid, "transporting")
    assert inc["escort"]["block"] is not None
    until(c, lambda: inc["escort"]["status"] == "delivered")
    for want in ("traffic_blocked", "police_requested", "traffic_cleared", "patient_delivered"):
        assert want in types(c)


def test_escort_is_deterministic():
    def go():
        c, iid = _transporting(seed="E-1")
        to_stage(c, iid, "watching")
        c.decide(iid, "leave")
        return c

    a, b = go(), go()
    assert a.eng.log.hash() == b.eng.log.hash() and a.view() == b.view()


def test_escort_api_shape(api):
    api.post("/city/start", json={"paused": True}, headers=H)
    b = api.get("/city").json()
    assert b["traffic"] == []
    S.city.cfg = {**S.city.cfg, "transport_chance": 1.0, "traffic_chance": 1.0}
    api.post("/control/skip", json={"to": "09:03"})
    i = accident(S.city, injuries=1, at_risk=False)
    to_stage(S.city, i, "care_call")
    body = {"incident": i, "choice": "transport"}
    assert api.post("/city/decide", json=body, headers=H).json()["stage"] == "working"
    to_stage(S.city, i, "transporting")
    e = api.get("/city").json()["incidents"][0]["escort"]
    assert set(e) == {"ambulance", "drone", "status", "blocked_at", "delay_s"}
    until(S.city, lambda: api.get("/city").json()["traffic"])
    t = api.get("/city").json()["traffic"][0]
    assert set(t) == {"at", "status", "progress_pct"}


# ---- decisions
def _watching(risk=False, seed="T-1"):
    c = new_city(seed)
    run(c, 3)
    iid = fire(c, at_risk=risk)
    to_stage(c, iid, "watching")
    return c, iid


def test_stay_holds_then_asks_again_with_risk_gone():
    c, iid = _watching(risk=True)
    inc = c.active[iid]
    c.decide(iid, "stay")
    assert inc["stage"] == "holding" and inc["decision"] is None
    assert "decision" not in c.incident_view(inc)
    run(c, C["stay_min"] - 1)
    assert inc["stage"] == "holding"
    to_stage(c, iid, "watching")
    assert inc["at_risk"] is False and "Clear reading" in c.incident_view(inc)["decision"]["prompt"]
    assert c.decide(iid, "approve")["stage"] == "closed"  # `approve` in #ops = leave
    run(c, C["flare_delay_min"] + 1)
    assert c.active == {}


def test_unanswered_call_stays_open_until_the_lead_replies():
    """L60: people in chat are slow, so a late reply still counts."""
    c, iid = _watching()
    inc = c.active[iid]
    run(c, C["decision_timeout_min"] * 4)
    assert inc["stage"] == "watching" and inc["decision"]
    assert not any(r["rationale"] == "timed out" for r in c.eng.decisions)
    c.decide(iid, "leave", "kazum")
    assert c.eng.incidents[iid]["status"] != "open" or iid not in c.active


class Always:
    def __init__(self, rng, value):
        self.rng, self.value = rng, value

    def random(self):
        return self.value

    def choice(self, x):
        return self.rng.choice(x)


def test_flare_up_after_leaving_at_risk_crew_returns():
    c, iid = _watching(risk=True)
    scene = c.active[iid]["scene"]
    c.eng.rng = Always(c.eng.rng, 0.0)
    c.decide(iid, "leave")
    assert c.active == {}
    run(c, C["flare_delay_min"] + DT)
    (inc,) = c.active.values()
    assert inc["flare_of"] == iid and inc["scene"] == scene
    to_stage(c, inc["id"], "working")
    assert inc["vehicles"]


def test_no_flare_when_the_roll_misses():
    c, iid = _watching(risk=True)
    c.eng.rng = Always(c.eng.rng, 0.999)
    c.decide(iid, "leave")
    run(c, C["flare_delay_min"] + 1)
    assert c.active == {}


def test_decide_errors():
    c = new_city()
    run(c, 1)
    iid = fire(c, at_risk=False)
    with pytest.raises(ct.CityError) as e:
        c.decide(iid, "leave")
    assert e.value.rejection.code == "MISSION_EXPIRED"
    with pytest.raises(ct.CityError) as e:
        c.decide("INC-T99", "leave")
    assert e.value.rejection.code == "NOT_FOUND"
    to_stage(c, iid, "watching")
    with pytest.raises(ValueError):
        c.decide(iid, "maybe")


# ---- relief, rules, logs
def test_relief_at_a_scene_on_station_before_leaving():
    c, iid = _watching()
    c.decide(iid, "stay")
    inc = c.active[iid]
    old = c.eng.drones[inc["drone"]]
    old.bat = W.model["relief_trigger_pct"]
    c.eng.step()
    new = inc["relief"]
    assert new and new != old.id
    while inc["relief"]:
        assert old.mission == "incident" and old.on_station
        c.eng.step()
    assert inc["drone"] == new and c.eng.drones[new].on_station
    assert old.mission == "returning"
    until(c, lambda: old.state == "docked")


def test_scene_drone_taken_by_safety_rtb_is_replaced():
    c, iid = _watching()
    inc = c.active[iid]
    old = inc["drone"]
    c.eng.rtb(c.eng.drones[old], c.eng.now, "safety")
    c.eng.step()
    assert inc["drone"] and inc["drone"] != old


def test_uses_existing_logs():
    c, iid = _watching()
    c.decide(iid, "leave")
    acts = [(r["actor"], r["action"]) for r in c.eng.decisions]
    assert ("kazum (dashboard)", "city:decide:leave") in acts
    assert ("operator", "city:trigger:fire") in acts
    assert ("dispatch", "divert") in acts and ("dispatch", "close_incident") in acts
    kinds = [r["kind"] for r in c.eng.privacy.rows() if r["incident"] == iid]
    assert "recording_on" in kinds


def test_recording_only_at_the_scene():
    c = new_city()
    run(c, 3)
    fire(c)
    for _ in range(300):
        c.eng.step()
        for d in c.eng.drones.values():
            if d.recording:
                assert d.mission == "incident"


# ---- determinism
def script(seed):
    c = new_city(seed)
    run(c, 2)
    a = c.trigger("fire")["id"]
    run(c, 1)
    b = c.trigger("accident")["id"]
    to_stage(c, a, "watching")
    c.decide(a, "stay")
    treat_through(c, b)
    c.decide(b, "leave")
    to_stage(c, a, "watching")
    c.decide(a, "leave")
    run(c, 10)
    return c


def test_deterministic():
    a, b = script("D-1"), script("D-1")
    assert a.eng.log.hash() == b.eng.log.hash()
    assert a.view() == b.view()


def test_seed_changes_the_run():
    hashes = {script(f"D-{i}").eng.log.hash() for i in range(4)}
    assert len(hashes) > 1


# ---- API
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "c.db"))
    monkeypatch.setenv("AIRGRID_GAME_KEY", KEY)
    with TestClient(app) as client:
        S.city = None
        yield client
        S.city = None


WRITES = [
    ("/city/start", {}),
    ("/city/trigger", {"kind": "fire"}),
    ("/city/decide", {"incident": "INC-T1", "choice": "leave"}),
]


@pytest.mark.parametrize("path,body", WRITES)
def test_no_key_403(api, path, body):
    r = api.post(path, json=body)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"


@pytest.mark.parametrize("path,body", WRITES)
def test_server_without_key_refuses(api, monkeypatch, path, body):
    monkeypatch.delenv("AIRGRID_GAME_KEY")
    assert api.post(path, json=body, headers=H).status_code == 403


def test_agent_token_cannot_write(api):
    r = api.post("/city/start", json={}, headers={"Authorization": "Bearer dispatch-token"})
    assert r.status_code == 403


def test_api_whole_incident(api):
    b = api.get("/city").json()
    assert b["started"] is False and len(b["stations"]) == 3
    assert api.post("/city/trigger", json={"kind": "fire"}, headers=H).status_code == 404
    r = api.post("/city/start", json={"paused": True}, headers=H)
    assert r.status_code == 200
    assert api.post("/city/trigger", json={"kind": "flood"}, headers=H).status_code == 404
    api.post("/control/skip", json={"to": "09:03"})
    b = api.get("/city").json()
    for k in ("clock_label", "counters", "drones", "vehicles", "stations", "incidents", "feed"):
        assert k in b
    assert b["counters"]["drones_up"] == len(C["roam"]) and len(b["vehicles"]) == 6
    assert set(b["drones"][0]) == {"id", "pos", "battery", "state", "label", "recording"}
    assert set(b["vehicles"][0]) == {"id", "kind", "pos", "state", "incident"}
    inc = api.post("/city/trigger", json={"kind": "accident"}, headers=H).json()["incident"]
    S.city.active[inc["id"]]["at_risk"] = False
    S.city._roll_injuries = lambda _inc: 0  # the doctor's call has its own test
    assert (
        api.post(
            "/city/decide", json={"incident": inc["id"], "choice": "leave"}, headers=H
        ).status_code
        == 409
    )
    clock = 9 * 60 + 3
    for _ in range(30):
        clock += 5
        api.post("/control/skip", json={"to": f"{clock // 60:02d}:{clock % 60:02d}"})
        b = api.get("/city").json()
        if b["incidents"][0].get("decision"):
            break
    i = b["incidents"][0]
    assert i["decision"]["remaining_s"] > 0 and i["stage"] == "watching"
    assert set(i) >= {
        "id",
        "kind",
        "at",
        "stage",
        "progress_pct",
        "progress_label",
        "drone",
        "vehicles",
    }
    bad = {"incident": inc["id"], "choice": "nope"}
    assert api.post("/city/decide", json=bad, headers=H).status_code == 422
    assert api.post("/city/decide", json={"choice": "leave"}, headers=H).status_code == 422
    good = {"place": inc["scene"].lower(), "choice": "leave"}
    r = api.post("/city/decide", json=good, headers=H).json()
    assert r["report"]["incident"] == inc["id"]
    b = api.get("/city").json()
    assert b["incidents"] == [] and b["counters"]["handled"] == 1 and b["feed"]
    last = b["events"][-1]
    assert last["type"] == "city_report"
    assert api.get(f"/city?since={last['seq']}").json()["events"] == []


def test_agent_activity_never_holds_the_city(api):
    api.post("/city/start", json={"paused": True}, headers=H)
    api.post("/control/agent-activity", json={"agent": "dispatch", "state": "busy"})
    assert not S.clock.active_holds()


def test_existing_modes_untouched():
    eng = Engine.load("FRIDAY-RUSH")
    assert eng.city is None


# ---- random fire, found by patrol (L62)
DET = C["detect"]


def hidden_city(seed="R-1"):
    c = new_city(seed)
    run(c, 3)
    out = c.random_fire()
    assert out["status"] and "at" not in out
    (h,) = c.hidden
    return c, h


def found(c, limit=40):
    until(c, lambda: any(i.get("found_by") for i in c.active.values()), limit)
    return next(i for i in c.active.values() if i.get("found_by"))


def test_hidden_spot_on_road_clear_of_zones_stations_and_scenes():
    c, h = hidden_city()
    assert c.roads.on_road(h["at"]) and c.active == {}
    for z in W.raw["zones"]:
        assert not in_rect(h["at"], z["rect"])
    places = [s["road"] for s in C["stations"].values()]
    places += [s["at"] for v in C["scenes"].values() for s in v]
    assert all(ct.dist(h["at"], p) >= DET["clear_km"] for p in places)
    spots = {tuple(hidden_city(f"S{i}")[1]["at"]) for i in range(8)}
    assert len(spots) > 1  # seeded, not one fixed spot


def test_confidence_is_a_fixed_function_of_distance():
    c = new_city()
    assert c.confidence(0) == 100 and c.confidence(DET["sense_km"]) == 0
    assert c.confidence(DET["sense_km"] * 2) == 0
    assert c.confidence(0.4) > c.confidence(0.6)


def test_patrol_senses_diverts_confirms_and_the_fire_response_runs():
    c, h = hidden_city()
    until(c, lambda: h["status"] == "possible", 40)
    d = c.eng.drones[h["drone"]]
    assert d.mission == "investigate" and c._label(d) == "Checking a heat reading"
    (e,) = [x for x in c.eng.events if x["type"] == "heat_possible"]
    assert e["payload"]["confidence_pct"] >= DET["possible_pct"]
    assert "at" not in e["payload"] and str(h["at"][0]) not in e["text"]
    (det,) = c.view()["detections"]
    assert set(det) == {"drone", "confidence_pct", "status"} and det["status"] == "possible"
    inc = found(c)
    assert "fire_confirmed" in types(c) and c.hidden == [] and c.view()["detections"] == []
    assert {k: inc["found_by"][k] for k in ("drone", "how")} == {
        "drone": d.id,
        "how": "patrol sensor (simulated)",
    }
    assert inc["found_by"]["found_s"] > 0
    assert inc["drone"] == d.id and d.mission == "incident"
    assert inc["at"] == h["at"] and inc["name"] == f"{c.street(h['at'])} fire"
    assert c.incident_view(inc)["found_by"]["drone"] == d.id
    to_stage(c, inc["id"], "working")
    assert c.vehicles[inc["vehicles"][0]].kind == "fire_engine"


def test_found_within_about_20_real_seconds_for_many_seeds():
    for i in range(12):
        c, h = hidden_city(f"F-{i}")
        t0 = c.eng.now
        found(c, limit=15)
        real_s = (c.eng.now - t0) / C["sim_min_per_s"]
        assert real_s <= 25, (i, real_s)


def test_fallback_routes_the_nearest_roamer_past_it():
    c, h = hidden_city()
    c.cfg = {**c.cfg, "detect": {**DET, "sense_km": 0.01, "possible_pct": 99, "confirm_pct": 99}}
    run(c, DET["fallback_min"] - 0.5)
    assert h["drone"] is None and not h["sent"]
    run(c, 1)
    assert h["sent"] and h["drone"] and c.eng.drones[h["drone"]].mission == "investigate"
    found(c)


def test_investigating_drone_low_battery_goes_home_and_another_takes_over():
    c, h = hidden_city()
    until(c, lambda: h["drone"], 40)
    d = c.eng.drones[h["drone"]]
    d.bat = C["home_pct"]  # same rule as roamers and scene drones
    c.eng.step()
    assert d.mission == "returning"
    c.eng.step()
    assert h["drone"] != d.id
    found(c)
    assert c.eng.violations == []


def test_nothing_leaks_before_detection(api):
    api.post("/city/start", json={"paused": True}, headers=H)
    api.post("/control/skip", json={"to": "09:03"})
    before_events = len(api.get("/events").json()["events"])
    r = api.post("/city/trigger", json={"kind": "random_fire"}, headers=H).json()
    (h,) = S.city.hidden
    x, y = h["at"]
    marks = [f"{x:g}, {y:g}", f"[{x}, {y}]", f"{x:g},{y:g}", c_street(h)]
    assert r["incident"] is None
    S.city.cfg = {**S.city.cfg, "detect": {**DET, "sense_km": 0.01, "fallback_min": 999}}
    api.post("/control/skip", json={"to": "09:06"})
    bodies = [
        api.get(p).text
        for p in (
            "/city",
            "/state",
            "/events",
            "/decisions",
            "/fleet",
            "/incidents",
            "/kpi",
            "/privacy-log",
        )
    ]
    bodies.append(r and str(r))
    for text in bodies:
        for m in marks:
            assert m not in text, m
    evs = api.get("/events").json()["events"][before_events:]
    assert not [e for e in evs if "heat" in e["type"] or "fire" in e["text"].lower()]
    assert api.get("/city").json()["detections"] == []
    from sim.app import state_diff

    snap = json.dumps(S.engine.state_view())
    assert all(m not in snap for m in marks)
    assert state_diff  # SSE frames are built from state_view only


def c_street(h):
    return S.city.street(h["at"])


def test_random_fire_is_deterministic():
    def go():
        c, h = hidden_city("D-9")
        inc = found(c)
        to_stage(c, inc["id"], "watching")
        c.decide(inc["id"], "leave")
        return c

    a, b = go(), go()
    assert a.eng.log.hash() == b.eng.log.hash() and a.view() == b.view()


def test_found_fire_is_not_a_zero_second_response():
    """A drone that found a fire is already there: it reports time to find, not a 0 s response."""
    c = new_city()
    c.random_fire()
    for _ in range(round(30 / DT)):
        c.eng.step()
        if c.active:
            break
    inc = next(iter(c.active.values()))
    assert inc["found_by"]["found_s"] > 0
    run(c, 2)
    assert c.responses == []


def test_unfound_fire_only_on_the_operator_screen(api):
    """L63: the keyed dashboard draws the fire burning; keyless callers still learn nothing."""
    api.post("/city/start", json={"paused": True}, headers=H)
    api.post("/city/trigger", json={"kind": "random_fire"}, headers=H)
    (h,) = S.city.hidden
    keyed = api.get("/city", headers=H).json()
    assert [u["at"] for u in keyed["unfound"]] == [list(h["at"])]
    assert "unfound" not in api.get("/city").json()
    assert "unfound" not in api.get("/city", headers={"X-Game-Key": "wrong"}).json()
    S.city._confirm(h, next(iter(S.engine.drones.values())))
    assert api.get("/city", headers=H).json()["unfound"] == []
