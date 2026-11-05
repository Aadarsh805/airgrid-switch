"""Phase 04 HTTP helpers (docs/phase04-interface.md). Builds on tests/sim/p3.py."""

import math

from tests.sim.helpers import load_yaml, read_text
from tests.sim.p3 import CODES, minutes, rows

WORLD = load_yaml("world/airgrid.yaml")
FRIDAY = load_yaml("scenarios/friday_rush.yaml")
QUIET = load_yaml("scenarios/quiet_shift.yaml")
TRIGGERS = load_yaml("scenarios/triggers.yaml")
ROAD_SPEED = WORLD["ground_units"]["road_speed_km_per_min"]
GEOFENCE_KM = WORLD["policies"]["geofence_km"]
LANDMARKS = WORLD["district"]["landmarks"]
JUNCTIONS = WORLD["district"]["junctions"]
ZONES = {z["id"]: z for z in WORLD["zones"]}
THERMAL = {k for k, v in WORLD["fleet"].items() if v.get("thermal")}
UNIT_IDS = (
    [f"patrol_car_{i}" for i in range(1, 11)]
    + [f"ambulance_{i}" for i in range(1, 4)]
    + [f"fire_engine_{i}" for i in range(1, 3)]
)
UNIT_STATUSES = {"available", "en_route", "on_scene", "returning"}
TRIGGER_KINDS = list(TRIGGERS)  # the 7 menu kinds


def hms(m: float) -> str:
    """Minutes since 00:00 -> 'HH:MM:SS' (rounded to the second)."""
    s = int(round(m * 60))
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}"


def point(v):
    if isinstance(v, list | tuple) and len(v) == 2:
        return (float(v[0]), float(v[1]))
    if isinstance(v, str):
        if v in LANDMARKS:
            return tuple(LANDMARKS[v])
        if v in JUNCTIONS:
            return tuple(JUNCTIONS[v])
    raise AssertionError(f"cannot read a position from {v!r}")


def manhattan(a, b) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def euclid(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def get_ok(c, path, **params):
    r = c.get(path, params=params or None)
    assert r.status_code == 200, f"GET {path}: {r.status_code} {r.text[:300]}"
    return r.json()


def state(c) -> dict:
    return get_ok(c, "/state")


def now(c) -> float:
    return float(state(c)["t"])


def units(c) -> dict[str, dict]:
    body = get_ok(c, "/units")
    return {u["id"]: u for u in rows(body, "units")}


def incidents(c) -> dict[str, dict]:
    body = get_ok(c, "/incidents")
    return {i["id"]: i for i in rows(body, "incidents")}


def incident(c, iid: str) -> dict:
    b = get_ok(c, f"/incidents/{iid}")
    return b.get("incident", b)


def inc_pos(inc: dict):
    if isinstance(inc.get("pos"), list):
        return point(inc["pos"])
    return point(inc["at"])


def events(c, since=None) -> list[dict]:
    body = get_ok(c, "/events", **({"since": since} if since else {}))
    return rows(body, "events")


def fact_payload(f: dict) -> dict:
    """A served fact entry is a scenario feed entry; its content sits under `fact`."""
    return f["fact"] if isinstance(f.get("fact"), dict) else f


def facts(inc: dict) -> list[dict]:
    fs = inc.get("facts")
    assert isinstance(fs, list), f"{inc.get('id')}: facts missing or not a list: {fs!r}"
    return fs


def trigger(c, kind: str, at=None):
    body = {"kind": kind}
    if at is not None:
        body["at"] = at
    return c.post("/trigger", json=body)


def trigger_ok(c, kind: str, at=None) -> dict:
    r = trigger(c, kind, at)
    assert r.status_code == 200, f"trigger {kind}: {r.status_code} {r.text[:400]}"
    b = r.json()
    assert b.get("ok") is True, b
    return b.get("result") or {}


def refused(r) -> dict:
    """A rejection with a §4.2 code (status 4xx, ok false, reason + hint)."""
    assert 400 <= r.status_code < 500, f"want a rejection, got {r.status_code}: {r.text[:400]}"
    b = r.json()
    assert b.get("ok") is False, b
    assert b.get("code") in CODES, f"code {b.get('code')!r} is not a 04 §4.2 code: {b}"
    assert isinstance(b.get("reason"), str) and b["reason"].strip(), b
    return b


def golden_scorecard_block() -> list[str]:
    """Checker scorecard lines from docs/golden-timeline.txt (header excluded, up to blank)."""
    lines = read_text("docs/golden-timeline.txt").splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("=== Scorecard"))
    out = []
    for ln in lines[i + 1 :]:
        if not ln.strip():
            break
        out.append(ln.rstrip())
    assert len(out) == 8, out  # sanity: the block parsed
    return out


__all__ = ["minutes", "rows"]
