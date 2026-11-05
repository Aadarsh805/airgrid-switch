"""Rule 8 · leak grep over a golden shift: every endpoint and every read-only CLI output, sampled
at several times, plus the SSE stream. Also: no token value in any output or log.

test_scanner_* prove the scanner fires on planted leaks, so a clean run means something.
"""

import json
import threading

import httpx
import pytest

from tests.integration.cli_table import COMMANDS
from tests.integration.harness import (
    FRIDAY,
    dead_url,
    future_markers,
    leak_findings,
    run_cli,
    run_many,
    token_findings,
    token_values,
)
from tests.sim.helpers import tmin
from tests.sim.p3 import act, auth, load, skip
from tests.sim.p4 import UNIT_IDS

SAMPLES = [
    "17:05",
    "17:13",
    "17:30",
    "17:41",
    "17:45",
    "17:50",
    "17:55",
    "17:58",
    "18:05",
    "18:10",
    "18:25",
    "19:05",
]
ENDPOINTS = [
    "/world",
    "/state",
    "/status",
    "/fleet",
    "/fleet/D-13",
    "/docks",
    "/pilots",
    "/incidents",
    "/airspace",
    "/units",
    "/events",
    "/events?since=17:00",
    "/approvals",
    "/approvals?status=pending",
    "/privacy-log",
    "/kpi",
    "/kpi/history",
    "/decisions",
]
NEEDS_INCIDENT = {
    "INC-1": ["/incidents/INC-1"],
    "INC-2": ["/incidents/INC-2"],
}
REQUIRED_CLI = [
    "status",
    "fleet",
    "fleet --id",
    "docks",
    "pilots",
    "incidents",
    "airspace",
    "units",
    "events",
    "approvals",
    "privacy-log",
    "kpi",
    "decisions",
    "plan rotation",
    "route check",
]
INCIDENT_CLI = ["incidents --id", "feed", "track status", "relay plan"]
BY_KEY = {c.key: c for c in COMMANDS}
ROLE = "intel"


def cli_reads(now: float) -> list[tuple[str, list[str], str]]:
    """(label, argv, role) for every read-only §4.1 command that makes sense at `now`."""
    out = []
    for key in REQUIRED_CLI:
        cmd = BY_KEY[key]
        out.append((key, list(cmd.argv), cmd.who))
        out.append((f"{key} --text", [*cmd.argv, "--text"], cmd.who))
    for iid, opened in (("INC-1", tmin("17:12")), ("INC-2", tmin("17:40"))):
        if now < opened:
            continue
        for key, argv in (
            ("incidents --id", ["incidents", "--id", iid]),
            ("feed", ["feed", "--incident", iid]),
            ("track status", ["track", "status", "--incident", iid]),
            ("relay plan", ["relay", "plan", "--incident", iid]),
        ):
            out.append((f"{key} {iid}", argv, BY_KEY[key].who))
            out.append((f"{key} {iid} --text", [*argv, "--text"], BY_KEY[key].who))
    return out


@pytest.fixture(scope="module")
def shift(sim, c):
    """Golden shift with the baseline computed up front (the hard case for /kpi)."""
    load(c)
    assert c.post("/baseline/run").status_code == 200
    api, cli, baseline = {}, {}, {}
    for when in SAMPLES:
        skip(c, when)
        now = tmin(when)
        paths = list(ENDPOINTS)
        for iid, opened in (("INC-1", "17:12"), ("INC-2", "17:40")):
            if now >= tmin(opened):
                paths += NEEDS_INCIDENT[iid]
        api[when] = {p: c.get(p) for p in paths}
        baseline[when] = c.get("/kpi").json().get("baseline")
        reads = cli_reads(now)
        results = run_many(sim.url, [(argv, role) for _, argv, role in reads])
        cli[when] = {label: r for (label, _, _), r in zip(reads, results, strict=True)}
    return {"api": api, "cli": cli, "baseline": baseline}


def decoded(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---------- scanner positive controls ----------


def test_scanner_catches_a_future_golden_line():
    assert leak_findings(tmin("17:05"), "t", {"text": "🚨 INC-2 robbery at market_street"})


def test_scanner_catches_a_future_feed_fact():
    assert leak_findings(tmin("17:45"), "t", {"fact": "east toward old airfield fence"})


def test_scanner_catches_a_future_waypoint_in_json():
    assert leak_findings(tmin("17:45"), "t", {"pos": [5.25, 2.4]})


def test_scanner_catches_a_future_waypoint_in_text():
    assert leak_findings(tmin("17:45"), "t", "suspect next at [5.25, 2.4]")


def test_scanner_catches_scenario_keys():
    assert leak_findings(tmin("17:45"), "t", {"golden_actions": []})
    assert leak_findings(tmin("17:45"), "t", {"expected": {}})


def test_scanner_catches_baseline_internals():
    assert leak_findings(tmin("17:45"), "t", {"baseline": {"eyes_t": {"INC-1": 1}}})


def test_scanner_catches_a_future_event_time():
    assert leak_findings(tmin("17:45"), "/events", {"until": "18:50"})


def test_scanner_is_quiet_about_the_past():
    assert not leak_findings(tmin("19:05"), "t", {"text": "🚨 INC-2 robbery at market_street"})


def test_scanner_markers_shrink_over_the_shift():
    assert len(future_markers(tmin("17:05"))) > len(future_markers(tmin("19:05")))


def test_token_scanner_catches_a_token():
    assert token_findings("t", f"Authorization: Bearer {token_values()[0]}")


# ---------- API ----------


@pytest.mark.parametrize("when", SAMPLES)
def test_every_endpoint_answers_and_is_clean(shift, when):
    now, bad = tmin(when), []
    for path, r in shift["api"][when].items():
        assert r.status_code == 200, f"{when} {path}: {r.status_code}"
        bad += leak_findings(now, f"{when} {path}", r.json())
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("when", SAMPLES)
def test_no_event_is_ahead_of_the_clock(shift, when):
    now = tmin(when)
    for path in ("/events", "/events?since=17:00"):
        for e in shift["api"][when][path].json()["events"]:
            assert e["t"] <= now + 1e-6, f"{when} {path}: {e}"


@pytest.mark.parametrize("when", SAMPLES)
def test_suspect_is_only_shown_where_he_is_now(shift, when):
    """/state.suspect is null or the current position, never a point further along the path."""
    seen = shift["api"][when]["/state"].json().get("suspect")
    if seen is None:
        return
    assert tmin("17:40") <= tmin(when) <= tmin("18:07"), seen
    pts = [(tmin(w["t"]), w["at"]) for w in FRIDAY["suspect_path"]]
    now = tmin(when)
    here = next(
        (
            [
                a[0] + (now - ta) / (tb - ta) * (b[0] - a[0]),
                a[1] + (now - ta) / (tb - ta) * (b[1] - a[1]),
            ]
            for (ta, a), (tb, b) in zip(pts, pts[1:], strict=False)
            if ta <= now <= tb
        ),
        pts[-1][1],
    )
    assert abs(seen[0] - here[0]) < 0.01 and abs(seen[1] - here[1]) < 0.01, (seen, here)


@pytest.mark.parametrize("when", SAMPLES)
def test_baseline_is_final_numbers_only(shift, when):
    base = shift["baseline"][when]
    assert base, f"{when}: baseline was run, /kpi.baseline empty"
    assert base.get("suspect_lost") == {}, f"{when}: outcome depends on the future: {base}"
    now = tmin(when)
    if now < tmin("18:07"):
        assert "INC-2" not in json.dumps(base), f"{when}: open or future incident: {base}"
    for value in base.values():
        assert not (isinstance(value, list) and value and isinstance(value[0], dict)), (
            f"{when}: a baseline timeline: {base}"
        )


def test_every_contract_get_is_sampled():
    """04 §4.5 GET list, minus the SSE stream (its own test) and /ui (static files)."""
    want = {
        "/world",
        "/state",
        "/status",
        "/fleet",
        "/docks",
        "/pilots",
        "/incidents",
        "/airspace",
        "/units",
        "/events",
        "/approvals",
        "/privacy-log",
        "/kpi",
        "/kpi/history",
        "/decisions",
    }
    assert want <= set(ENDPOINTS)


# ---------- CLI ----------


def test_every_read_only_command_is_sampled():
    reads = {c.key for c in COMMANDS if not c.writes} - {"simulate"}
    assert reads == set(REQUIRED_CLI) | set(INCIDENT_CLI), reads ^ (
        set(REQUIRED_CLI) | set(INCIDENT_CLI)
    )


@pytest.mark.parametrize("when", SAMPLES)
def test_every_cli_read_is_clean(shift, when):
    now, bad = tmin(when), []
    for label, r in shift["cli"][when].items():
        assert r.code == 0, f"{when} {label}: {r}"
        bad += leak_findings(now, f"{when} airgrid {label}", decoded(r.out))
        bad += leak_findings(now, f"{when} airgrid {label} stderr", r.err)
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("when", ["17:45", "17:50", "17:58"])
def test_projections_expose_no_future_suspect_detail(shift, when):
    """track status and relay plan serve the present: no future heading or corridor exit."""
    future = [m for m in future_markers(tmin(when)) if m]
    for key in ("track status INC-2", "relay plan INC-2"):
        for suffix in ("", " --text"):
            out = shift["cli"][when][f"{key}{suffix}"].out
            for m in future:
                assert m not in out, f"{when} {key}: {m!r}"
            assert "5.45,4.45" not in out and "5.45, 4.45" not in out, out


def test_rejection_text_leaks_nothing(sim, c):
    """Rejections carry reason and hint text too. Sampled before the 17:40 robbery."""
    load(c)
    skip(c, "17:20")
    now = tmin("17:20")
    for argv, who in [
        (
            [
                "launch",
                "--drone",
                "D-01",
                "--mission",
                "incident",
                "--incident",
                "INC-1",
                "--to",
                "5.8,3.4",
                "--why",
                "x",
            ],
            "dispatch",
        ),
        (["rtb", "--drone", "D-12", "--dock", "P10"], "fleet"),
        (["incidents", "--id", "INC-99"], "intel"),
        (["track", "status", "--incident", "INC-99"], "relay"),
        (["relay", "plan", "--incident", "INC-99"], "relay"),
    ]:
        r = run_cli(sim.url, argv, who)
        bad = leak_findings(now, " ".join(argv), decoded(r.out))
        assert not bad, bad


def test_track_status_before_the_robbery_does_not_confirm_it(sim, c):
    """INC-2 has not happened at 17:30: same answer as any unknown incident."""
    load(c)
    skip(c, "17:30")
    future = run_cli(sim.url, ["track", "status", "--incident", "INC-2"], "relay")
    unknown = run_cli(sim.url, ["track", "status", "--incident", "INC-99"], "relay")
    assert future.code == unknown.code == 2, (future, unknown)
    assert future.json["code"] == unknown.json["code"] == "NOT_FOUND"


# ---------- SSE ----------


def test_event_stream_is_clean(sim, c):
    load(c)
    frames: list[dict] = []
    ready = threading.Event()
    stop = threading.Event()

    def reader():
        with httpx.stream("GET", f"{sim.url}/events/stream", timeout=None) as r:
            ready.set()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    frames.append(json.loads(line[5:]))
                if stop.is_set():
                    return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    assert ready.wait(10)
    for when in ("17:13", "17:45", "18:05", "18:25", "19:05"):
        skip(c, when)
    # One more write so the reader wakes up and sees `stop`.
    stop.set()
    skip(c, "19:06")
    t.join(10)
    assert len(frames) > 5, frames[:2]
    bad = []
    for f in frames:
        when = f.get("t", f.get("created_min"))  # approval frames carry created_min
        assert isinstance(when, int | float), f
        bad += leak_findings(float(when), f"SSE {f.get('type', 'tick')}", f)
    assert not bad, "\n".join(bad[:20])
    assert any(f.get("type") == "cad_alert" or "INC-1" in str(f) for f in frames)


# ---------- tokens ----------


def test_no_token_in_any_output(shift):
    bad = []
    for when, by_path in shift["api"].items():
        for path, r in by_path.items():
            bad += token_findings(f"{when} {path}", r.text)
    for when, by_label in shift["cli"].items():
        for label, r in by_label.items():
            bad += token_findings(f"{when} {label}", r.out + r.err)
    assert not bad, "\n".join(bad)


def test_no_token_on_cli_failure_paths(sim, c):
    """A failing call is where a client tends to dump its request headers."""
    load(c)
    skip(c, "17:45")
    runs = [
        run_cli(dead_url(), ["kpi"], "dispatch"),
        run_cli(dead_url(), ["rtb", "--drone", "D-14"], "fleet"),
        run_cli(sim.url, ["approve", "A-0001"], "dispatch"),
        run_cli(sim.url, ["rtb", "--drone", "D-14", "--dock", "P9"], "fleet"),
        run_cli(sim.url, ["log-refusal", "--about", "x", "--code", "NOPE", "--by", "k"], "fleet"),
    ]
    bad = []
    for r in runs:
        bad += token_findings(" ".join(r.args), r.out + r.err)
    assert not bad, "\n".join(bad)


def test_no_token_in_the_sim_log(sim, c, shift):
    """After writes with real tokens have gone through uvicorn at info level."""
    load(c)
    skip(c, "17:45")
    run_cli(sim.url, ["rtb", "--drone", "D-14"], "fleet")
    assert act(c, "rtb", {"drone": "D-02"}).status_code == 200
    text = sim.log_text()
    assert "POST /actions" in text, "log capture is not working"
    bad = token_findings("uvicorn log", text)
    assert not bad, "\n".join(bad)


def test_no_token_in_decisions_or_events(c):
    r = c.post("/actions", json={"action": "rtb", "args": {"drone": "D-99"}}, headers=auth("fleet"))
    assert r.status_code == 404
    for path in ("/decisions", "/events", "/state"):
        assert not token_findings(path, c.get(path).text)


# ---------- unit ids stay fixed (guards the table the leak walk relies on) ----------


def test_units_endpoint_shape(c):
    load(c)
    assert {u["id"] for u in c.get("/units").json()["units"]} == set(UNIT_IDS)
