"""API smoke (sim-engineer): /world, /state, NOT_FOUND, control/skip, scenario load."""

import pytest
from fastapi.testclient import TestClient

from sim.app import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRGRID_DB", str(tmp_path / "t.db"))
    with TestClient(app) as c:
        # headless: golden actions apply (the 17:00 standing policy enables auto-launch)
        assert c.post("/scenario/load", json={"name": "FRIDAY-RUSH", "mode": "headless"}).json()[
            "ok"
        ]
        yield c


def test_world_is_static_geometry(client):
    w = client.get("/world").json()
    assert set(w) >= {"district", "sectors", "junctions", "landmarks", "zones", "docks", "loops"}
    assert "fleet" not in w and "suspect_path" not in str(w)


def test_not_found_codes(client):
    r = client.get("/fleet/D-99")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert client.get("/nope").json()["code"] == "NOT_FOUND"
    assert client.post("/scenario/load", json={"name": "NOPE"}).json()["code"] == "NOT_FOUND"


def test_skip_to_and_events_since(client):
    r = client.post("/control/skip", json={"to": "17:13"}).json()
    assert r["clock"] == "17:13"
    assert client.get("/fleet/D-09").json()["drone"]["mission"] == "incident"
    ev = client.get("/events", params={"since": "17:12"}).json()["events"]
    assert [e["type"] for e in ev if e["timeline"]] == ["cad_alert", "launch"]
    s = client.get("/status").json()
    assert s["clock"] == "17:13" and s["scenario"] == "FRIDAY-RUSH"


def test_skip_without_body_goes_to_next_event(client):
    client.post("/control/skip", json={"to": "17:10"})
    client.post("/control/skip")  # next scheduled thing: the 17:12 CAD alert
    assert client.get("/status").json()["clock"] == "17:12"


def test_speed_validation(client):
    assert client.post("/control/speed", json={"sim_min_per_s": 2}).json()["ok"]
    assert client.post("/control/speed", json={"sim_min_per_s": 7}).status_code == 422
