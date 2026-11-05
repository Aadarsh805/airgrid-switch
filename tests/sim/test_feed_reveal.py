"""Phase 04 · feed facts: revealed only at/after t and after eyes-on; never early; no identities."""

import json

import pytest

from tests.sim.p3 import at, load, skip
from tests.sim.p4 import (
    FRIDAY,
    events,
    fact_payload,
    facts,
    get_ok,
    hms,
    incident,
    minutes,
    now,
)

FEED = [(minutes(f["t"]), f) for f in FRIDAY["feed"]]
# Distinctive text per feed fact (phrases that exist nowhere else before the fact's time).
MARKERS = {
    ("INC-1", "17:14"): ["eastbound lane 2", "2 minor, walking"],
    ("INC-1", "17:18"): ["Ring Road west feeder"],
    ("INC-2", "17:42"): ["grey hoodie", "red scooter, rider"],
    ("INC-2", "17:53"): ["east toward old airfield fence"],
    ("INC-2", "17:56"): ["inside airfield (out of view)"],
    ("INC-2", "18:01"): ["exiting airfield north gate", "west toward J4"],
}
IDENTITY_KEYS = {
    "name",
    "identity",
    "identities",
    "face",
    "face_id",
    "person",
    "person_id",
    "plate",
    "licence_plate",
    "license_plate",
    "dob",
    "address",
    "phone",
}


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


def payloads(inc) -> list[dict]:
    return [fact_payload(f) for f in facts(inc)]


def feed_fact(iid, t):
    return next(f["fact"] for ft, f in FEED if f["incident"] == iid and f["t"] == t)


# ---------- INC-1 ----------


def test_inc1_facts_empty_before_eyes_on(c):
    at(c, "17:13:42")  # D-09 eyes-on 17:13:54
    assert facts(incident(c, "INC-1")) == []


def test_inc1_1714_fact_after_eyes_on(c):
    at(c, "17:14")
    ps = payloads(incident(c, "INC-1"))
    assert feed_fact("INC-1", "17:14") in ps, ps
    assert feed_fact("INC-1", "17:18") not in ps, ps


def test_inc1_facts_need_a_drone_on_station(c):
    """Live mode, no standing policy approved: no INC-1 drone, so nothing is revealed."""
    load(c, mode="live")
    skip(c, "17:20")
    inc = incident(c, "INC-1")
    assert not inc.get("drones"), inc
    assert facts(inc) == [], "facts revealed without a drone on station"


# ---------- INC-2 partial fact ----------


def test_inc2_partial_fact_only_from_1756(c):
    at(c, "17:55:30")
    before = payloads(incident(c, "INC-2"))
    assert feed_fact("INC-2", "17:56") not in before, before
    skip(c, "17:56")
    after = incident(c, "INC-2")
    assert feed_fact("INC-2", "17:56") in payloads(after), after["facts"]
    entry = next(f for f in after["facts"] if fact_payload(f) == feed_fact("INC-2", "17:56"))
    if "confidence" in entry:
        assert entry["confidence"] == "partial", entry


# ---------- leak sweep ----------


def _walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _check_snapshot(t, path, body):
    blob = json.dumps(body, ensure_ascii=False)
    for (iid, ft), marks in MARKERS.items():
        if minutes(ft) > t + 1e-6:
            for m in marks:
                assert m not in blob, f"{hms(t)} {path}: future fact {iid} {ft} leaked ({m!r})"
    for o in _walk(body):
        if isinstance(o, dict) and "fact" in o and "t" in o:
            assert minutes(o["t"]) <= t + 1e-6, f"{hms(t)} {path}: fact t={o['t']} > now: {o}"


def _check_identity(t, body):
    for inc in body.get("incidents", []):
        for f in inc.get("facts", []):
            for o in _walk(f):
                if isinstance(o, dict):
                    bad = IDENTITY_KEYS & {str(k).lower() for k in o}
                    assert not bad, f"{hms(t)} {inc['id']}: identity fields {bad} in {f}"


@pytest.fixture(scope="module")
def sweep(c):
    load(c)
    out = []
    t = minutes("17:00")
    extra = [minutes(x) for x in ("17:13:54", "17:55:30", "17:56", "18:00:54", "18:01")]
    times = sorted({round(t + 0.5 * i, 4) for i in range(361)} | set(extra))
    for target in times:
        if target > minutes("17:00"):
            skip(c, hms(target))
        tn = now(c)
        snap = {
            "/incidents": get_ok(c, "/incidents"),
            "/state": get_ok(c, "/state"),
            "/events": get_ok(c, "/events"),
        }
        for iid in [i["id"] for i in snap["/incidents"].get("incidents", [])]:
            snap[f"/incidents/{iid}"] = get_ok(c, f"/incidents/{iid}")
        out.append((tn, snap))
    return out


def test_sweep_ran_the_whole_shift(sweep):
    assert sweep[-1][0] >= minutes("20:00") - 1e-6
    final = sweep[-1][1]["/incidents"]
    served = sum(len(i.get("facts", [])) for i in final.get("incidents", []))
    assert served >= 6, f"only {served} facts served by 20:00"


def test_no_future_fact_in_any_response(sweep):
    for t, snap in sweep:
        for path, body in snap.items():
            _check_snapshot(t, path, body)


def test_events_never_ahead_of_clock(sweep):
    for t, snap in sweep:
        for e in snap["/events"].get("events", []):
            assert e["t"] <= t + 1e-6, f"{hms(t)}: future event {e}"


def test_no_identity_fields_in_facts(sweep):
    for t, snap in sweep:
        _check_identity(t, snap["/incidents"])
        _check_identity(t, {"incidents": list(snap["/state"].get("incidents", {}).values())})


def test_events_endpoint_has_no_unrevealed_fact_text_at_1745(c):
    at(c, "17:45")
    blob = json.dumps(events(c), ensure_ascii=False)
    for m in MARKERS[("INC-2", "17:53")] + MARKERS[("INC-2", "17:56")]:
        assert m not in blob
