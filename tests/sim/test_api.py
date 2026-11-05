"""Layer 4 (sim API part) · GET endpoints at 17:45, 404 shape, and the leak test (rule 8)."""

import json
import re

import pytest

from tests.sim.helpers import dist, load_yaml, tmin

WORLD = load_yaml("world/airgrid.yaml")
SCEN = load_yaml("scenarios/friday_rush.yaml")
DRONES = sorted(WORLD["fleet"])
DOCKS = sorted(WORLD["docks"])
PILOTS = sorted(WORLD["pilots"])

REQUIRED = ["/world", "/state", "/status", "/fleet", "/docks", "/pilots", "/events?since=17:00"]
# Rest of 04 §4.5: scanned for leaks whenever they answer 200 (may not exist yet in Phase 02).
OPTIONAL = [
    "/incidents",
    "/airspace",
    "/units",
    "/approvals",
    "/privacy-log",
    "/kpi",
    "/kpi/history",
    "/decisions",
    "/incidents/INC-1",
    "/incidents/INC-2",
]
# `feed` itself is allowed (Phase 04 serves revealed facts); unrevealed facts are banned below.
FORBIDDEN_KEYS = {"suspect_path", "golden_actions", "expected", "units_golden"}
FORBIDDEN_ANYWHERE = ["suspect_path", "golden_actions", "units_golden"]

# Future-only text, by snapshot time (events/refusal wording that must not exist yet).
FUTURE_TEXT = {
    "17:45": [
        "18:20",
        "13 m/s",
        "dock_restored",
        "back online",
        "WEATHER_GROUNDED",
        "detained",
        "face recognition",
        "straight through the airfield",
        "just in case",
        "reacquire",
        "manual control",
    ],
    "17:58": [
        "18:20",
        "13 m/s",
        "dock_restored",
        "back online",
        "WEATHER_GROUNDED",
        "detained",
        "just in case",
        "reacquire",
        "manual control",
    ],
}


# ---------- generic JSON walkers ----------


def walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def keys(obj):
    for o in walk(obj):
        if isinstance(o, dict):
            yield from o.items()


def _num(v):
    return isinstance(v, int | float) and not isinstance(v, bool)


PAIR_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")


def coord_pairs(obj):
    for o in walk(obj):
        if isinstance(o, list) and len(o) == 2 and all(_num(v) for v in o):
            yield (float(o[0]), float(o[1]))
        elif isinstance(o, dict) and _num(o.get("x")) and _num(o.get("y")):
            yield (float(o["x"]), float(o["y"]))
        elif isinstance(o, str):
            for a, b in PAIR_RE.findall(o):
                yield (float(a), float(b))


def as_pair(v):
    if isinstance(v, list) and len(v) == 2:
        return (float(v[0]), float(v[1]))
    if isinstance(v, dict):
        for k in ("pos", "at", "position"):
            if k in v:
                return as_pair(v[k])
        if "x" in v and "y" in v:
            return (float(v["x"]), float(v["y"]))
    raise AssertionError(f"cannot read a position from {v!r}")


def records(body, key, id_prefix):
    """Accept list, {key: list}, {key: {id: rec}} or {id: rec}; return {id: rec}."""
    if isinstance(body, dict) and key in body:
        body = body[key]
    if isinstance(body, list):
        return {r["id"]: r for r in body}
    assert isinstance(body, dict), body
    return {k: v for k, v in body.items() if k.startswith(id_prefix)}


STATIC_POINTS = (
    [tuple(p) for p in WORLD["district"]["junctions"].values()]
    + [tuple(p) for p in WORLD["district"]["landmarks"].values()]
    + [tuple(d["at"]) for d in WORLD["docks"].values()]
    + [tuple(L["center"]) for L in WORLD["patrol_loops"].values()]
)


def _suspect_at(t):
    path = sorted((tmin(w["t"]), w["at"]) for w in SCEN["suspect_path"])
    for (ta, a), (tb, b) in zip(path, path[1:], strict=False):
        if ta <= t <= tb:
            f = (t - ta) / (tb - ta)
            return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
    return tuple(path[-1][1])


def future_points(now):
    """Suspect waypoints and golden-action targets after `now`, minus static geometry."""
    pts = [tuple(w["at"]) for w in SCEN["suspect_path"] if tmin(w["t"]) > now]
    pts += [
        tuple(a["to"])
        for a in SCEN["golden_actions"]
        if tmin(a["t"]) > now and isinstance(a.get("to"), list)
    ]
    return [p for p in pts if all(dist(p, s) > 0.02 for s in STATIC_POINTS)]


# ---------- fixture: one headless run, two snapshots ----------


def _snapshot(c):
    snap = {}
    for path in REQUIRED + [f"/fleet/{d}" for d in DRONES]:
        r = c.get(path)
        snap[path] = (r.status_code, r.text)
    for path in OPTIONAL:
        r = c.get(path)
        if r.status_code == 200:
            snap[path] = (r.status_code, r.text)
    return snap


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as c:
        r = c.post(
            "/scenario/load", json={"name": "FRIDAY-RUSH", "seed": "DRONE-01", "mode": "headless"}
        )
        assert r.status_code == 200, r.text
        out = {}
        for at in ("17:45", "17:58"):
            r = c.post("/control/skip", json={"to": at})
            assert r.status_code == 200, r.text
            out[at] = _snapshot(c)
        out["missing"] = c.get("/fleet/D-99")
        yield out


def body(api, at, path):
    code, text = api[at][path]
    assert code == 200, f"GET {path} at {at} → {code}: {text[:300]}"
    return json.loads(text)


# ---------- endpoints at 17:45 ----------


@pytest.mark.parametrize("path", REQUIRED)
def test_get_endpoints_200_json(api, path):
    b = body(api, "17:45", path)
    assert isinstance(b, dict | list)


def test_status_clock(api):
    assert "17:45" in json.dumps(body(api, "17:45", "/status"), ensure_ascii=False)


def test_world_static_geometry(api):
    text = json.dumps(body(api, "17:45", "/world"), ensure_ascii=False)
    for thing in ["RED-AIRFIELD", "NR-HOSPITAL", "HUB-A", "HUB-B", "P12", "J9"]:
        assert thing in text, thing


def test_fleet_shape(api):
    fleet = records(body(api, "17:45", "/fleet"), "drones", "D-")
    assert sorted(fleet) == DRONES
    for did, rec in fleet.items():
        assert "state" in rec, did
        assert "battery" in rec or "bat" in rec, did
        bat = rec.get("battery", rec.get("bat"))
        assert 0 <= bat <= 100, did


def test_fleet_by_id(api):
    for did in DRONES:
        text = json.dumps(body(api, "17:45", f"/fleet/{did}"), ensure_ascii=False)
        assert did in text
    # 17:45 facts from the golden timeline
    assert "HUB-B" in json.dumps(body(api, "17:45", "/fleet/D-09"))  # landed at HUB-B 17:37
    assert "track" in json.dumps(body(api, "17:45", "/fleet/D-13"))


def test_docks_shape(api):
    docks = records(body(api, "17:45", "/docks"), "docks", ("P", "HUB"))
    assert sorted(docks) == DOCKS


def test_pilots_shape(api):
    text = json.dumps(body(api, "17:45", "/pilots"), ensure_ascii=False)
    for p in PILOTS:
        assert p in text
    assert "simulated" in text


def test_events_since(api):
    text = json.dumps(body(api, "17:45", "/events?since=17:00"), ensure_ascii=False)
    assert "INC-1" in text and "INC-2" in text


def test_events_stream_route_registered():
    from sim.app import app

    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/events/stream" in paths


def test_unknown_drone_404(api):
    r = api["missing"]
    assert r.status_code == 404
    b = r.json()
    assert b.get("ok") is False and b.get("code") == "NOT_FOUND", b


def test_state_suspect_visible_while_tracked(api):
    """17:45: D-13 is on the suspect (outside RED) → current position only (L29c)."""
    s = body(api, "17:45", "/state").get("suspect")
    assert s is not None
    assert dist(as_pair(s), _suspect_at(tmin("17:45"))) <= 0.05


def test_state_suspect_null_inside_airfield(api):
    """17:58: suspect inside RED-AIRFIELD, tracker holding at the boundary → null."""
    assert body(api, "17:58", "/state").get("suspect") is None


# ---------- leak test (rule 8) ----------

AT = ["17:45", "17:58"]


def _bodies(api, at):
    return {p: (json.loads(t) if t else None, t) for p, (code, t) in api[at].items() if code == 200}


@pytest.mark.parametrize("at", AT)
def test_no_forbidden_keys(api, at):
    for path, (obj, text) in _bodies(api, at).items():
        for k, v in keys(obj):
            assert k not in FORBIDDEN_KEYS, f"{at} {path}: key {k!r}"
            if k == "baseline":
                assert not v, f"{at} {path}: baseline internals in a managed run"
        for word in FORBIDDEN_ANYWHERE:
            assert word not in text, f"{at} {path}: {word!r}"


def unrevealed_feed_text(now):
    """String values of scenario feed facts timed after `now` (never served before then)."""
    out = []
    for entry in SCEN["feed"]:
        if tmin(entry["t"]) > now:
            out += [v for v in entry["fact"].values() if isinstance(v, str) and len(v) >= 6]
    return out


@pytest.mark.parametrize("at", AT)
def test_no_unrevealed_feed(api, at):
    banned = unrevealed_feed_text(tmin(at))
    assert banned  # 17:45: 17:53/17:56/18:01 facts · 17:58: 18:01 facts
    for path, (obj, _text) in _bodies(api, at).items():
        decoded = json.dumps(obj, ensure_ascii=False)
        for fact in banned:
            assert fact not in decoded, f"{at} {path}: unrevealed feed fact {fact!r}"
        for o in walk(obj):
            assert o != SCEN["feed"], f"{at} {path}: the whole scenario feed"


@pytest.mark.parametrize("at", AT)
def test_no_future_event_text(api, at):
    for path, (_obj, text) in _bodies(api, at).items():
        decoded = json.dumps(json.loads(text), ensure_ascii=False)
        for word in FUTURE_TEXT[at]:
            assert word not in decoded, f"{at} {path}: future text {word!r}"
        if path.startswith("/events"):
            assert "wind" not in decoded.lower(), f"{at} {path}: weather event before 18:20"


@pytest.mark.parametrize("at", AT)
def test_no_future_wind_values(api, at):
    for path, (obj, _t) in _bodies(api, at).items():
        for k, v in keys(obj):
            if "wind" in str(k).lower() and _num(v) and "limit" not in str(k).lower():
                assert v <= 12, f"{at} {path}: {k}={v} before the 18:20 front"


@pytest.mark.parametrize("at", AT)
def test_no_future_suspect_coordinates(api, at):
    now = tmin(at)
    bad = future_points(now)
    if at == "17:58":
        bad.append(_suspect_at(now))  # inside the airfield: not served at all
    assert bad
    for path, (obj, _t) in _bodies(api, at).items():
        for p in coord_pairs(obj):
            for q in bad:
                assert dist(p, q) > 0.02, f"{at} {path}: coordinate {p} ≈ future/hidden {q}"


def test_replay_fixtures_are_not_served_by_the_live_sim():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as c:
        assert c.get("/ui/fixtures/golden.jsonl").status_code == 404
        assert c.get("/ui/fixtures/world.json").status_code == 404
