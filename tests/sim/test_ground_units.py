"""Phase 04 · ground units: /units, task_unit ETA, arrival, early release with evidence."""

import pytest

from tests.sim.p3 import act, at, load, ok, skip
from tests.sim.p4 import (
    ROAD_SPEED,
    UNIT_IDS,
    UNIT_STATUSES,
    hms,
    inc_pos,
    incident,
    manhattan,
    now,
    point,
    refused,
    units,
)

UNIT_ACTOR = "comms"  # units_golden run as comms actions (phase04-interface)
INC1_FACT_1714 = "2 minor, walking"  # feed INC-1 17:14 injuries
INC1_FACT_1718 = "Ring Road west feeder"  # feed INC-1 17:18 (future at 17:15)


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def task(c, unit, inc):
    return act(c, "task_unit", {"unit": unit, "incident": inc}, who=UNIT_ACTOR)


def release(c, unit, inc, evidence):
    args = {"unit": unit, "incident": inc, "evidence": evidence}
    return act(c, "release_unit", args, who=UNIT_ACTOR)


# ---------- /units ----------


def test_units_lists_10_3_2(c):
    load(c)
    us = units(c)
    assert sorted(us) == sorted(UNIT_IDS), sorted(us)
    kinds = {}
    for u in us.values():
        for k in ("id", "kind", "status", "pos", "incident", "eta_min"):
            assert k in u, f"{u.get('id')}: no {k}: {u}"
        assert u["status"] in UNIT_STATUSES, u
        assert u["status"] == "available" and u["incident"] is None, u
        point(u["pos"])
        kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
    assert sorted(kinds.values()) == [2, 3, 10], kinds


# ---------- task_unit -> en_route (ETA) -> on_scene ----------


def test_task_unit_eta_and_arrival(c):
    at(c, "17:12:30")  # INC-1 open since 17:12
    unit = "patrol_car_3"  # not used by units_golden
    u0 = units(c)[unit]
    assert u0["status"] == "available", u0
    inc = incident(c, "INC-1")
    eta = 1 + manhattan(point(u0["pos"]), inc_pos(inc)) / ROAD_SPEED
    t0 = now(c)
    ok(task(c, unit, "INC-1"))
    u1 = units(c)[unit]
    assert u1["status"] == "en_route", u1
    assert u1["incident"] == "INC-1", u1
    assert abs(float(u1["eta_min"]) - eta) <= 0.1 + 1e-6, f"eta {u1['eta_min']} vs {eta:.2f}"
    if eta > 0.4:
        skip(c, hms(t0 + eta - 0.3))
        assert units(c)[unit]["status"] == "en_route", units(c)[unit]
    skip(c, hms(t0 + eta + 0.2))
    u2 = units(c)[unit]
    assert u2["status"] == "on_scene", f"{hms(t0 + eta + 0.2)}: {u2}"
    assert u2["incident"] == "INC-1", u2


def test_golden_units_tasked(c):
    at(c, "17:13:30")
    us = units(c)
    for unit in ("patrol_car_2", "ambulance_1"):
        assert us[unit]["status"] in ("en_route", "on_scene"), us[unit]
        assert us[unit]["incident"] == "INC-1", us[unit]


# ---------- release_unit ----------


def test_release_with_future_fact_rejected(c):
    at(c, "17:15")  # 17:18 fact not revealed yet
    refused(release(c, "patrol_car_2", "INC-1", INC1_FACT_1718))
    assert units(c)["patrol_car_2"]["status"] in ("en_route", "on_scene")


def test_release_with_non_fact_rejected(c):
    at(c, "17:15")
    refused(release(c, "patrol_car_2", "INC-1", "the moon is bright tonight"))
    assert units(c)["patrol_car_2"]["status"] in ("en_route", "on_scene")


def test_release_quoting_revealed_fact_works(c):
    at(c, "17:15")
    assert any(INC1_FACT_1714 in str(f) for f in incident(c, "INC-1")["facts"]), (
        "17:14 fact not revealed at 17:15"
    )
    ok(release(c, "patrol_car_2", "INC-1", INC1_FACT_1714))
    u = units(c)["patrol_car_2"]
    assert u["status"] in ("returning", "available"), u


def test_golden_patrol_car_2_released_by_1721(c):
    at(c, "17:20:30")
    assert units(c)["patrol_car_2"]["status"] in ("en_route", "on_scene"), "released too early"
    skip(c, "17:21:30")
    u = units(c)["patrol_car_2"]
    assert u["status"] in ("returning", "available"), u
    assert u["status"] != "on_scene"
