"""Phase 03 · privacy: recording is derived, every transition logged, face ID refused + logged,
privacy_log append-only. 03-SIM-SPEC §5, phase03-interface "Privacy"."""

import json
import re

import pytest

from tests.sim.helpers import dist, in_rect, load_yaml, tmin
from tests.sim.p3 import (
    KAZUM_WC,
    act,
    at,
    decisions,
    fleet,
    load,
    minutes,
    ok,
    privacy_log,
    rejected,
    skip,
)

WORLD = load_yaml("world/airgrid.yaml")
HOSP = next(z for z in WORLD["zones"] if z["id"] == "NR-HOSPITAL")["rect"]
GEOFENCE = WORLD["policies"].get("geofence_km", 0.6)
LM = WORLD["district"]["landmarks"]
INCIDENT_AT = {"INC-1": LM["ring_road_j9"], "INC-2": LM["market_street"]}
TRACK_AUTH = (tmin("17:41"), tmin("18:07"))  # golden track authorisation until INC-2 closes
SWEEP_END = "18:30"
ON_RE = re.compile(r"(^|[^a-z])on($|[^a-z])")
OFF_RE = re.compile(r"(^|[^a-z])off($|[^a-z])")


def pos(rec):
    if "pos" in rec:
        return rec["pos"]
    return [rec["x"], rec["y"]]


def clock(t):
    h, m = divmod(round(t), 60)
    return f"{h:02d}:{m:02d}"


def row_dir(row) -> str | None:
    s = f"{row.get('kind', '')} {row.get('detail', '')}".lower()
    if OFF_RE.search(s):
        return "off"
    if ON_RE.search(s):
        return "on"
    return None


@pytest.fixture(scope="module")
def c():
    from fastapi.testclient import TestClient

    from sim.app import app

    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def sweep(c):
    """Headless golden run sampled every sim-minute 17:00 → 18:30, plus the privacy log."""
    load(c)
    samples = [(tmin("17:00"), fleet(c))]
    t = tmin("17:01")
    while t <= tmin(SWEEP_END) + 1e-9:
        skip(c, clock(t))
        samples.append((t, fleet(c)))
        t += 1
    return {"samples": samples, "log": privacy_log(c)}


def recording_rows(log):
    return [r for r in log if r.get("drone") and row_dir(r)]


# ---------- derived recording ----------


def test_recording_field_present(sweep):
    for t, fl in sweep["samples"]:
        for did, d in fl.items():
            assert isinstance(d.get("recording"), bool), f"{clock(t)} {did}: {d}"


def test_recording_only_where_allowed(sweep):
    for t, fl in sweep["samples"]:
        for did, d in fl.items():
            if not d["recording"]:
                continue
            where = f"{clock(t)} {did} recording at {pos(d)} ({d.get('mission')})"
            assert d["state"] == "airborne", where
            assert d.get("mission") not in (None, "patrol", "returning"), where
            assert not in_rect(pos(d), HOSP), f"{where}: inside NR-HOSPITAL"
            inc = d.get("incident")
            assert inc in INCIDENT_AT, where
            under_track = inc == "INC-2" and TRACK_AUTH[0] <= t <= TRACK_AUTH[1]
            if not under_track:
                assert dist(pos(d), INCIDENT_AT[inc]) <= GEOFENCE + 0.01, (
                    f"{where}: outside geofence"
                )


def test_patrols_never_record(sweep):
    seen = 0
    for t, fl in sweep["samples"]:
        for did, d in fl.items():
            if d.get("mission") == "patrol":
                seen += 1
                assert d["recording"] is False, f"{clock(t)} {did}"
    assert seen > 200


def test_d09_inc1_on_station_records_transit_does_not(sweep):
    by_t = dict(sweep["samples"])
    assert by_t[tmin("17:13")]["D-09"]["recording"] is False  # transit, 0.75 km out
    assert by_t[tmin("17:20")]["D-09"]["recording"] is True  # on station at INC-1
    assert by_t[tmin("17:36")]["D-09"]["recording"] is False  # RTB to HUB-B


# ---------- transitions logged ----------


def test_every_transition_in_privacy_log(sweep):
    rows = recording_rows(sweep["log"])
    samples = sweep["samples"]
    changes = 0
    for (t0, a), (t1, b) in zip(samples, samples[1:], strict=False):
        for did in b:
            if a[did]["recording"] != b[did]["recording"]:
                changes += 1
                want = "on" if b[did]["recording"] else "off"
                hits = [
                    r
                    for r in rows
                    if r["drone"] == did
                    and t0 < minutes(r["sim_min"]) <= t1 + 1e-6
                    and row_dir(r) == want
                ]
                assert hits, (
                    f"{did} recording {want} between {clock(t0)} and {clock(t1)} not logged"
                )
    assert changes >= 4  # D-09 on/off at INC-1, D-13 on/off on the INC-2 track


def test_privacy_log_alternates_per_drone(sweep):
    per = {}
    for r in sorted(
        recording_rows(sweep["log"]), key=lambda r: (minutes(r["sim_min"]), r.get("seq", 0))
    ):
        per.setdefault(r["drone"], []).append(row_dir(r))
    assert per, "no recording rows in the privacy log"
    for did, seq in per.items():
        assert seq[0] == "on", f"{did} starts with {seq[0]}"
        for x, y in zip(seq, seq[1:], strict=False):
            assert x != y, f"{did} two '{x}' in a row: {seq}"


def test_privacy_log_seq_monotonic(sweep):
    seqs = [r["seq"] for r in sweep["log"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_golden_face_id_logged(sweep):
    face = [r for r in sweep["log"] if "face" in json.dumps(r).lower()]
    assert face, "17:52 face-recognition request missing from the privacy log"
    assert abs(minutes(face[0]["sim_min"]) - tmin("17:52")) <= 0.1 + 1e-6


# ---------- face ID, live ----------


def test_face_id_refused_and_logged(c):
    at(c, "17:20")
    before_p, before_d = len(privacy_log(c)), len(decisions(c))
    req = {"request": "identify the driver's face from D-09 footage", "by": "kazum"}
    rejected(act(c, "request_face_id", req, who=KAZUM_WC), "PRIVACY_POLICY")
    new_p = privacy_log(c)[before_p:]
    new_d = decisions(c)[before_d:]
    assert any("face" in json.dumps(r).lower() for r in new_p), new_p
    assert any(
        r.get("action") == "request_face_id" and r.get("code") == "PRIVACY_POLICY" for r in new_d
    ), new_d


# ---------- append-only ----------


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "POST"])
@pytest.mark.parametrize("path", ["/privacy-log", "/privacy-log/1"])
def test_privacy_log_has_no_write_route(c, method, path):
    at(c, "17:20")
    before = privacy_log(c)
    r = c.request(method, path, json={"kind": "recording_off", "detail": "tamper"})
    assert r.status_code in (404, 405), f"{method} {path} → {r.status_code}"
    assert privacy_log(c) == before


# ---------- NR-HOSPITAL ----------
# Needs POST /trigger (04 §4.5); the fire trigger sits at (2.25, 3.4) per scenarios/triggers.yaml.


def test_no_recording_inside_nr_hospital(c):
    load(c)
    skip(c, "17:20")
    r = c.post("/trigger", json={"kind": "fire", "at": "17:20"})
    assert r.status_code == 200, r.text
    incs = sorted(
        # triggered incidents use their own id series INC-T<n> (L33)
        set(re.findall(r"INC-T\d+", r.text + c.get("/events?since=17:20").text))
    )
    assert incs, "trigger created no incident"
    inc = incs[0]
    # (2.25, 3.9): 0.5 km from the fire (inside its geofence) and inside NR-HOSPITAL
    ok(
        act(
            c,
            "launch",
            {"drone": "D-01", "mission": "perimeter", "incident": inc, "to": [2.25, 3.9]},
        )
    )
    skip(c, "17:30")
    fl = fleet(c)
    d01 = fl["D-01"]
    assert in_rect(pos(d01), HOSP), d01
    assert d01["recording"] is False
    rejected(act(c, "set_recording", {"drone": "D-01", "on": True}), "PRIVACY_POLICY")
    onsite = [
        k
        for k, d in fl.items()
        if d.get("incident") == inc and not in_rect(pos(d), HOSP) and d["recording"]
    ]
    assert onsite, "the drone on the fire (outside the hospital) should be recording"
