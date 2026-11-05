"""Phase 01: the sim answers /health, /ui is mounted, the CLI status stub keeps exit codes."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cli.airgrid import app as cli_app
from sim.app import app


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_ui_mounted():
    assert any(getattr(route, "path", None) == "/ui" for route in app.routes)


def test_cli_status_unreachable_exits_1(monkeypatch):
    monkeypatch.setenv("AIRGRID_API_URL", "http://127.0.0.1:9")
    r = CliRunner().invoke(cli_app, ["status"])
    assert r.exit_code == 1
    assert json.loads(r.stdout)["ok"] is False
