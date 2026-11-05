"""Phase 03 HTTP helpers (docs/phase03-interface.md). Identity choices live in ACTOR only."""

import json
import re

from tests.sim.helpers import TOKENS_DOC, tmin

CODES = {
    "BATTERY_RESERVE",
    "DRONE_NOT_READY",
    "NO_FLY_ZONE",
    "WEATHER_GROUNDED",
    "DOCK_FULL",
    "DOCK_OFFLINE",
    "PILOT_SATURATED",
    "SEPARATION",
    "OUT_OF_RANGE",
    "AUTH_REQUIRED",
    "PRIVACY_POLICY",
    "MISSION_EXPIRED",
    "FORBIDDEN_ROLE",
    "NOT_FOUND",
}
KAZUM_WC = ("kazum", "watch_commander")
KAZUM_RP = ("kazum", "remote_pilot")

# Who performs each action (config/roles.yaml agent rights; actions no agent holds go to the
# human role that owns them in 04 §4.3).
ACTOR = {
    "launch": "dispatch",
    "open_incident": "dispatch",
    "close_incident": "dispatch",
    "rtb": "fleet",
    "hold": "airspace",
    "corridor_watch": "airspace",
    "activate_zone": "airspace",
    "request_footage_release": "privacy",
    "log_refusal": "dispatch",
    "divert": KAZUM_WC,
    "assign_track": KAZUM_WC,
    "assign_perimeter": KAZUM_WC,
    "broadcast": KAZUM_WC,
    "release_footage": KAZUM_WC,
    "set_recording": KAZUM_WC,
    "request_face_id": KAZUM_WC,
    "dock_at": KAZUM_RP,
    "take_control": KAZUM_RP,
    "release_control": KAZUM_RP,
}
PROPOSER = {
    "track": "dispatch",
    "broadcast": "dispatch",
    "zone": "airspace",
    "footage_release": "privacy",
    "out_of_service": "fleet",
    "standing_policy": "dispatch",
}


def token(who: str) -> str:
    if who in TOKENS_DOC["agents"]:
        return TOKENS_DOC["agents"][who]
    return TOKENS_DOC["humans"][who]


def auth(who) -> dict:
    name = who[0] if isinstance(who, tuple) else who
    return {"Authorization": f"Bearer {token(name)}"}


def load(c, mode="headless", name="FRIDAY-RUSH", seed="DRONE-01"):
    r = c.post("/scenario/load", json={"name": name, "seed": seed, "mode": mode})
    assert r.status_code == 200, r.text
    return r


def skip(c, to: str):
    r = c.post("/control/skip", json={"to": to})
    assert r.status_code == 200, r.text
    return r


def at(c, when: str, mode="headless"):
    load(c, mode=mode)
    if when != "17:00":
        skip(c, when)


def act(c, action: str, args: dict, who=None):
    who = who if who is not None else ACTOR[action]
    role = who[1] if isinstance(who, tuple) else who
    return c.post(
        "/actions", json={"role": role, "action": action, "args": args}, headers=auth(who)
    )


def propose(c, type_: str, args: dict, summary="test proposal", who=None, **extra):
    who = who or PROPOSER[type_]
    body = {"type": type_, "args": args, "summary": summary, **extra}
    return c.post("/approvals", json=body, headers=auth(who))


def approve(c, aid: str, who="kazum", note=None):
    return c.post(
        f"/approvals/{aid}/approve", json={"note": note} if note else {}, headers=auth(who)
    )


def reject(c, aid: str, reason: str, who="kazum"):
    return c.post(f"/approvals/{aid}/reject", json={"reason": reason}, headers=auth(who))


def ok(r) -> dict:
    assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
    b = r.json()
    assert b.get("ok") is True, b
    return b.get("result") or {}


def rejected(r, code: str, status: int = 409) -> dict:
    assert code in CODES
    assert r.status_code == status, f"want {status} {code}, got {r.status_code}: {r.text[:400]}"
    b = r.json()
    assert b.get("ok") is False, b
    assert b.get("code") == code, b
    assert isinstance(b.get("reason"), str) and b["reason"].strip(), f"no reason: {b}"
    assert isinstance(b.get("hint"), str), f"no hint: {b}"
    return b


AID_RE = re.compile(r"\bA-\d{4}\b")


def approval_id(r) -> str:
    res = ok(r)
    aid = res.get("id") or res.get("approval_id")
    if not aid:
        found = AID_RE.findall(json.dumps(res))
        assert found, f"no approval id in {res}"
        aid = found[0]
    assert AID_RE.fullmatch(aid), aid
    return aid


def rows(body, key):
    """Accept a list or a dict holding the list under `key` (or its only list value)."""
    if isinstance(body, list):
        return body
    if key in body and isinstance(body[key], list):
        return body[key]
    lists = [v for v in body.values() if isinstance(v, list)]
    assert len(lists) == 1, f"cannot find {key} rows in {list(body)}"
    return lists[0]


def decisions(c) -> list[dict]:
    r = c.get("/decisions")
    assert r.status_code == 200, r.text
    return rows(r.json(), "decisions")


def approvals(c, status=None) -> list[dict]:
    r = c.get("/approvals", params={"status": status} if status else None)
    assert r.status_code == 200, r.text
    return rows(r.json(), "approvals")


def privacy_log(c, since=None) -> list[dict]:
    r = c.get("/privacy-log", params={"since": since} if since else None)
    assert r.status_code == 200, r.text
    return rows(r.json(), "privacy_log")


def minutes(v) -> float:
    return tmin(v) if isinstance(v, str) else float(v)


def drone(c, did: str) -> dict:
    r = c.get(f"/fleet/{did}")
    assert r.status_code == 200, r.text
    b = r.json()
    return b.get("drone", b) if isinstance(b, dict) else b


def fleet(c) -> dict[str, dict]:
    """GET /fleet as {drone_id: record}; accepts list, {"drones": list|dict} or {id: rec}."""
    r = c.get("/fleet")
    assert r.status_code == 200, r.text
    b = r.json()
    if isinstance(b, dict) and "drones" in b:
        b = b["drones"]
    if isinstance(b, list):
        return {d["id"]: d for d in b}
    return {k: v for k, v in b.items() if k.startswith("D-")}
