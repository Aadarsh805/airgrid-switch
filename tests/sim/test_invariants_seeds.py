"""Layer 3 invariants over 20 seeds: the seed may change wording, never physics or safety."""

import random
import re

import pytest

from tests.sim.helpers import dist, in_rect, load_yaml
from tests.sim.p3 import approval_id, approve, load, ok, propose, rows
from tests.sim.p4 import TRIGGER_KINDS, inc_pos, minutes, trigger_ok

WORLD = load_yaml("world/airgrid.yaml")
RED = next(z for z in WORLD["zones"] if z["id"] == "RED-AIRFIELD")["rect"]
SLOTS = {k: v["slots"] for k, v in WORLD["docks"].items()}
CAPS = {k: v["max_drones"] for k, v in WORLD["pilots"].items()}
RESERVE = WORLD["drone_model"]["reserve_pct"]
SEEDS = [f"SEED-{i:02d}" for i in range(20)]


@pytest.fixture(scope="module")
def golden():
    from sim import golden as g

    return g


@pytest.fixture(scope="module")
def baseline_lines(golden):
    return list(golden.run(name="FRIDAY-RUSH").lines)


def check_frames(frames):
    for f in frames:
        load = {}
        for did, d in f["drones"].items():
            assert not in_rect(d["pos"], RED), f"{f['clock']} {did} in RED-AIRFIELD"
            if d["state"] == "airborne":
                assert d["bat"] >= RESERVE, f"{f['clock']} {did} {d['bat']}%"
                load[d["pilot"]] = load.get(d["pilot"], 0) + 1
        for p, n in load.items():
            assert p in CAPS and n <= CAPS[p], f"{f['clock']} pilot {p}: {n}"
        for k, dk in f["docks"].items():
            assert len(dk["occ"]) + len(dk["res"]) <= SLOTS[k], f"{f['clock']} {k}"
        ready = sum(
            1
            for d in f["drones"].values()
            if d["state"] == "docked" and f["docks"][d["slot"]]["online"] and d["bat"] >= 80
        )
        assert ready >= 3, f"{f['clock']} ready floor {ready}"


@pytest.mark.parametrize("seed", SEEDS)
def test_seed_invariants(golden, baseline_lines, seed):
    r = golden.run(name="FRIDAY-RUSH", seed=seed)
    assert list(r.violations) == []
    assert list(r.lines) == baseline_lines, "seed changed physics lines"
    check_frames(r.frames)


# ---------- Phase 04: the same invariants with random live triggers ----------
#
# Live mode (no scripted golden actions): with random triggers the scripted golden decisions
# would collide with trigger launches by design, so every engine violation here is a real
# invariant failure. Safety RTBs are not violations (L36); they are recorded via /kpi.
# The 17:00 standing auto-launch policy is proposed and approved over HTTP, as at the brief.
# Sampled every 0.5 sim-min and right after each trigger.

TRIGGER_WINDOW = ("17:05", "19:30")
NR = next(z for z in WORLD["zones"] if z["id"] == "NR-HOSPITAL")["rect"]
READY_PCT = WORLD["drone_model"]["ready_pct"]


def _hms(m):
    s = int(round(m * 60))
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def _plan(seed):
    rng = random.Random(seed)
    lo, hi = (minutes(x) for x in TRIGGER_WINDOW)
    n = rng.randint(2, 4)
    return sorted((round(rng.uniform(lo, hi), 1), rng.choice(TRIGGER_KINDS)) for _ in range(n))


def _check_state(st, grounded_windows):
    t, clock = st["t"], st["clock"]
    drones, docks, incs = st["drones"], st["docks"], st["incidents"]
    load = {}
    for did, d in drones.items():
        assert not in_rect(d["pos"], RED), f"{clock} {did} in RED-AIRFIELD"
        if d["state"] == "airborne":
            assert d["battery"] >= RESERVE, f"{clock} {did} airborne at {d['battery']}%"
            assert d["pilot"], f"{clock} {did} airborne without a pilot"
            load[d["pilot"]] = load.get(d["pilot"], 0) + 1
        if d["recording"]:
            inc = incs.get(d["incident"]) if d["incident"] else None
            assert inc is not None, f"{clock} {did} recording with no incident"
            assert not in_rect(d["pos"], NR), f"{clock} {did} recording inside NR-HOSPITAL"
            tracked = any(
                a.get("incident") == d["incident"] and a.get("kind") == "track"
                for a in st.get("authorisations", [])
            )
            r = dist(d["pos"], inc_pos(inc))
            assert tracked or r <= inc["geofence_km"] + 1e-6, (
                f"{clock} {did} recording {r:.2f} km from {d['incident']} (geofence "
                f"{inc['geofence_km']})"
            )
    for p, n in load.items():
        assert p in CAPS and n <= CAPS[p], f"{clock} pilot {p}: {n} drones"
    seen = {}
    for k, dk in docks.items():
        assert len(dk["occupants"]) + len(dk["reserved"]) <= SLOTS[k], f"{clock} {k} over slots"
        for did in dk["occupants"] + dk["reserved"]:
            assert did not in seen, f"{clock} {did} holds {seen[did]} and {k}"
            seen[did] = k
    for did, d in drones.items():
        if d["state"] == "docked":
            assert did in docks[d["slot"]]["occupants"], f"{clock} {did} not in {d['slot']}"
    ready = sum(
        1
        for d in drones.values()
        if d["state"] == "docked"
        and docks[d["slot"]]["online"]
        and d["battery"] >= READY_PCT
        and not d.get("out_of_service")
    )
    exempt = any(a - 1e-6 <= t <= b + 1e-6 for a, b in grounded_windows)
    if not exempt:
        assert ready >= 3, f"{clock} ready floor {ready}"


def _grounded_windows(evs, end):
    """Declared grounding windows from the run's own weather events."""
    out, open_at = [], None
    for e in evs:
        if e["type"] == "weather_grounded" and open_at is None:
            open_at = e["t"]
        elif e["type"] == "weather_reopened" and open_at is not None:
            out.append((open_at, e["t"]))
            open_at = None
    if open_at is not None:
        out.append((open_at, end))
    return out


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as c:
        yield c


def _live_run(c, seed):
    load(c, mode="live", seed=seed)
    aid = approval_id(propose(c, "standing_policy", {}, "standing auto-launch policy"))
    ok(approve(c, aid, note="shift brief"))
    plan = _plan(seed)
    samples, fired = [], []
    t, end = minutes("17:00"), minutes("20:00")
    todo = list(plan)
    while t < end - 1e-9:
        nxt = round(t + 0.5, 4)
        if todo and todo[0][0] <= nxt + 1e-9:
            nxt = todo[0][0]
        r = c.post("/control/skip", json={"to": _hms(nxt)})
        assert r.status_code == 200, r.text
        samples.append(c.get("/state").json())
        while todo and todo[0][0] <= nxt + 1e-9:
            tt, kind = todo.pop(0)
            fired.append((tt, kind, trigger_ok(c, kind)))
            samples.append(c.get("/state").json())
        t = nxt
    evs = rows(c.get("/events").json(), "events")
    return plan, fired, samples, evs


@pytest.mark.parametrize("seed", SEEDS)
def test_seed_invariants_with_random_triggers(client, seed):
    plan, fired, samples, evs = _live_run(client, seed)
    assert 2 <= len(plan) <= 4 and len(fired) == len(plan), (plan, fired)
    final = samples[-1]
    assert final["clock"] == "20:00", final["clock"]
    assert final["violations"] == 0, f"{seed} {plan}: {final['violations']} violations"
    assert final["kpi"]["reserve_breaches"] == 0
    assert final["kpi"]["red_zone_entries"] == 0
    # L36: safety RTBs are the safety net working in an agent-less live run; recorded, not failed
    kpi = client.get("/kpi")
    assert kpi.status_code == 200, kpi.text
    srtb = kpi.json()["managed"]["safety_rtbs"]
    assert isinstance(srtb, int) and not isinstance(srtb, bool) and srtb >= 0, srtb
    print(f"{seed} {plan}: safety_rtbs={srtb}")
    windows = _grounded_windows(evs, final["t"])
    for st in samples:
        _check_state(st, windows)
    for e in evs:
        if e["type"] == "landing":
            m = re.search(r"with (\d+(?:\.\d+)?)%", e["text"])
            assert m, e
            assert float(m.group(1)) >= RESERVE, f"{seed} {plan}: {e['clock']} {e['text']}"
