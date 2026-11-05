"""Phase 05 test harness: sim subprocess, CLI subprocess, leak scanner.

The CLI always runs as a real subprocess with piped stdout, as a runtime agent runs it, and with
no UTF-8 override: on Windows a pipe defaults to cp1252, so an unencodable character fails here
the same way it would fail for an agent.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx

from tests.sim.helpers import ROOT, TOKENS_DOC, golden_expected, load_yaml, tmin
from tests.sim.p3 import CODES, token

FRIDAY = load_yaml("scenarios/friday_rush.yaml")
WORLD = load_yaml("world/airgrid.yaml")
CLI_BOOT = "from cli.airgrid import app; app()"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def dead_url() -> str:
    """A URL nothing listens on."""
    return f"http://127.0.0.1:{free_port()}"


def base_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AIRGRID_")}
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    return env


class SimProcess:
    """uvicorn sim.app:app in a subprocess; stdout and stderr go to `log_path`."""

    def __init__(self, work: Path, tokens: Path, humans: Path):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.log_path = work / "uvicorn.log"
        self.env = {
            **base_env(),
            "AIRGRID_DB": str(work / "airgrid.db"),
            "AIRGRID_TOKENS": str(tokens),
            "AIRGRID_HUMANS": str(humans),
        }
        self._log = None
        self.proc = None

    def start(self) -> None:
        self._log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "sim.app:app",
                "--port",
                str(self.port),
                "--log-level",
                "info",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"sim exited early: {self.log_text()[-2000:]}")
            try:
                if httpx.get(f"{self.url}/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("sim did not answer /health within 60 s")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=20)
        if self._log is not None:
            self._log.close()

    def log_text(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")


@dataclass
class CliResult:
    args: tuple
    code: int
    out: str
    err: str

    @property
    def json(self):
        try:
            return json.loads(self.out)
        except json.JSONDecodeError as e:
            raise AssertionError(f"stdout is not JSON ({e}): {self}") from None

    def __str__(self) -> str:
        return (
            f"airgrid {' '.join(self.args)} -> exit {self.code}\n"
            f"stdout: {self.out[:600]}\nstderr: {self.err[-600:]}"
        )


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def run_cli(url: str, args, who=None, tok=None, timeout: float = 90) -> CliResult:
    """Run the CLI. `who`: agent role, or (human, role). `tok` overrides the token."""
    args = tuple(str(a) for a in args)
    env = {**base_env(), "AIRGRID_API_URL": url}
    if who is not None:
        name, role = who if isinstance(who, tuple) else (who, who)
        env["AIRGRID_ROLE"] = role
        env["AIRGRID_TOKEN"] = token(name)
    if tok is not None:
        env["AIRGRID_TOKEN"] = tok
    p = subprocess.run(
        [sys.executable, "-c", CLI_BOOT, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=timeout,
    )
    return CliResult(args, p.returncode, _decode(p.stdout), _decode(p.stderr))


def run_many(url: str, jobs, workers: int = 8) -> list[CliResult]:
    """jobs: list of (args, who). Only for read-only or rejected calls (order is not kept)."""
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda j: run_cli(url, j[0], j[1]), jobs))


def assert_compact_json(r: CliResult):
    """stdout is one line of JSON with no separator whitespace."""
    assert r.err.strip() == "", f"stderr not empty: {r}"
    body = r.out.rstrip("\r\n")
    assert "\n" not in body, f"not one line: {r}"
    obj = r.json
    forms = {
        json.dumps(obj, separators=(",", ":"), ensure_ascii=True),
        json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
    }
    assert body in forms, f"not compact JSON: {r}"
    return obj


def assert_ok(r: CliResult):
    assert r.code == 0, f"want exit 0: {r}"
    obj = assert_compact_json(r)
    assert obj.get("ok") is True, f"want ok true: {r}"
    return obj


def assert_rejected(r: CliResult, code: str | None = None) -> dict:
    assert r.code == 2, f"want exit 2 ({code}): {r}"
    obj = assert_compact_json(r)
    assert obj.get("ok") is False, f"want ok false: {r}"
    assert obj.get("code") in CODES, f"code outside 04 §4.2: {r}"
    if code:
        assert obj["code"] == code, f"want {code}: {r}"
    assert isinstance(obj.get("reason"), str) and obj["reason"].strip(), f"no reason: {r}"
    assert isinstance(obj.get("hint"), str), f"no hint: {r}"
    return obj


def walk(obj, key=None):
    """Yield (key, value) for every node of a JSON value."""
    yield key, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)


def find_key(obj, name: str):
    """First value under a key equal to `name` (case-insensitive), anywhere in the JSON."""
    for k, v in walk(obj):
        if isinstance(k, str) and k.lower() == name:
            return v
    return None


def has_key(obj, *names: str) -> bool:
    """True if any key contains one of `names` (case-insensitive)."""
    return any(isinstance(k, str) and any(n in k.lower() for n in names) for k, _ in walk(obj))


# ---------------------------------------------------------------- leak scanner
FORBIDDEN_KEYS = {
    "suspect_path",
    "golden_actions",
    "units_golden",
    "expected",
    "initial",
    "waypoints",
    "future_path",
    "predicted_path",
}
FORBIDDEN_WORDS = ["suspect_path", "golden_actions", "units_golden"]
# Baseline fields that exist only inside sim/baseline.py (the API serves summary() numbers).
BASELINE_INTERNALS = {
    "alerts",
    "eyes_t",
    "first_unit_t",
    "van_free",
    "van_at",
    "depart",
    "coverage_frac",
    "search_km",
}
TIME_FREE_PROJECTIONS = ("plan rotation", "track status", "relay plan", "route check")

_GOLDEN = golden_expected()
_EVENTS = [(tmin(e["t"]), e) for e in FRIDAY["events"]]
_WAYPOINTS = [(tmin(w["t"]), tuple(w["at"])) for w in FRIDAY["suspect_path"]]


def _past_targets(now: float) -> set[tuple[float, float]]:
    """Coordinates that are public by `now`: landmarks and targets ordered up to now."""
    out = {tuple(p) for p in WORLD["district"]["landmarks"].values()}
    out |= {tuple(p) for p in WORLD["district"]["junctions"].values()}
    for a in FRIDAY["golden_actions"]:
        if isinstance(a.get("to"), list) and tmin(a["t"]) <= now + 1e-6:
            out.add(tuple(a["to"]))
    return out


def future_markers(now: float) -> list[str]:
    """Text that may not exist yet at `now`, from the scenario (not from the code under test)."""
    out = []
    past_msgs = {m for t, m in _GOLDEN if t <= now + 0.05}
    out += [m for t, m in _GOLDEN if t > now + 0.05 and m not in past_msgs]
    for t, e in _EVENTS:
        if t <= now + 1e-6:
            continue
        if e.get("note"):
            out.append(e["note"])
        if e["type"] == "cad_alert":
            out.append(e["incident"])
            if e["kind"] == "robbery":
                out.append("robbery")
        if e["type"] == "dock_fault":
            out.append(f"{e['dock']} OFFLINE")
    for f in FRIDAY["feed"]:
        if tmin(f["t"]) > now + 1e-6:
            out += [v for v in f["fact"].values() if isinstance(v, str) and len(v) >= 6]
    return out


def future_times(now: float) -> list[str]:
    """HH:MM of scenario events ahead of `now`, plus weather end. dock_restored is excluded: the
    17:25 fault note announces 19:00 publicly. 17:00 and 20:00 are the public shift bounds."""
    out = []
    for t, e in _EVENTS:
        if t > now + 1e-6 and e["type"] in ("cad_alert", "weather", "dock_fault"):
            out.append(e["t"])
            if e.get("until"):
                out.append(e["until"])
    return out


def leak_findings(now: float, source: str, payload) -> list[str]:
    """Everything wrong with one output at sim time `now`. `payload`: decoded JSON or a string."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    found = []
    for m in future_markers(now):
        if m in text:
            found.append(f"{source}: future text {m!r}")
    for w in FORBIDDEN_WORDS:
        if w in text:
            found.append(f"{source}: scenario word {w!r}")
    if not any(p in source for p in TIME_FREE_PROJECTIONS):
        for hhmm in future_times(now):
            if re.search(rf"(?<![\d:]){hhmm}(?![\d:])", text):
                found.append(f"{source}: future event time {hhmm}")
    public = _past_targets(now)
    for t, (x, y) in _WAYPOINTS:
        if t <= now + 1e-6 or (x, y) in public:
            continue
        if re.search(rf"\[\s*{x:g}0*,\s*{y:g}0*\s*\]", text):
            found.append(f"{source}: future suspect waypoint {t} [{x}, {y}]")
    if isinstance(payload, str):
        return found
    for k, v in walk(payload):
        if k in FORBIDDEN_KEYS:
            found.append(f"{source}: forbidden key {k!r}")
        if k in BASELINE_INTERNALS:
            found.append(f"{source}: baseline internal {k!r}")
        if v == FRIDAY["feed"] or v == FRIDAY["suspect_path"]:
            found.append(f"{source}: whole scenario list served")
        if isinstance(v, list) and len(v) == 2 and all(isinstance(n, int | float) for n in v):
            for t, wp in _WAYPOINTS:
                if t > now + 1e-6 and tuple(v) == wp and wp not in public:
                    found.append(f"{source}: future suspect waypoint {t} at key {k!r}")
    return found


def token_values() -> list[str]:
    return [*TOKENS_DOC["agents"].values(), *TOKENS_DOC["humans"].values()]


def token_findings(source: str, text: str) -> list[str]:
    return [f"{source}: token value {t!r}" for t in token_values() if t in text]
