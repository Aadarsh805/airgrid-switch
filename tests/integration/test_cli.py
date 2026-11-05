"""Layer 4 · every §4.1 command: ok (0, compact JSON), rejected (2, code/reason/hint), error (1)."""

import ast
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.integration.cli_table import COMMANDS, KAZUM_RP, Cmd
from tests.integration.harness import (
    assert_ok,
    assert_rejected,
    dead_url,
    run_cli,
)
from tests.sim.helpers import ROOT, read_text
from tests.sim.p3 import CODES, approvals, at, load

# ---------- guard: the table cannot lose a §4.1 command ----------


def contract_commands() -> dict[str, set[str]]:
    """§4.1 parsed from the contract: {command path: {flags}}, comments stripped."""
    text = read_text("docs/04-CONTRACTS.md")
    section = text[text.index("## 4.1") : text.index("## 4.2")]
    out: dict[str, set[str]] = {}

    def add(raw: str):
        raw = re.sub(r'"[^"]*"', '"x"', raw)
        words = raw.split()
        assert words[0] == "airgrid", raw
        path = []
        for w in words[1:]:
            if w.startswith(("--", "[", "<", '"')) or not re.fullmatch(r"[a-z][a-z-]*", w):
                break
            path.append(w)
        out.setdefault(" ".join(path), set()).update(re.findall(r"--[a-z][a-z-]*", raw))

    for block in re.findall(r"```\n(.*?)```", section, re.S):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            for part in line.split(" | "):
                if part.strip().startswith("airgrid "):
                    add(part.strip())
    human = re.search(r"Human-only[^\n]*", section).group(0)
    for cmd in re.findall(r"`([a-z-]+) <[^`]*`", human):
        out.setdefault(cmd, set())
    return out


def table_path(cmd: Cmd) -> str:
    return cmd.key.split(" --")[0]


def test_guard_contract_parse_is_sane():
    cmds = contract_commands()
    assert len(cmds) >= 35, sorted(cmds)
    for known in ("status", "incident close", "propose footage-release", "approve", "simulate"):
        assert known in cmds, sorted(cmds)
    assert "--from-alert" in cmds["incident open"]
    assert "--at-boundary" in cmds["hold"]


def test_guard_every_contract_command_is_in_the_table():
    missing = set(contract_commands()) - {table_path(c) for c in COMMANDS}
    assert not missing, f"§4.1 commands missing from tests/integration/cli_table.py: {missing}"


def test_guard_table_has_no_invented_command():
    extra = {table_path(c) for c in COMMANDS} - set(contract_commands())
    assert not extra, f"table commands not in §4.1: {extra}"


def test_guard_every_contract_flag_is_exercised():
    used: dict[str, set[str]] = {}
    for c in COMMANDS:
        used.setdefault(table_path(c), set()).update(a for a in c.argv if a.startswith("--"))
    for path, flags in contract_commands().items():
        if path == "simulate":
            continue  # "<same args as a write>"
        assert flags <= used[path], f"{path}: flags never exercised: {flags - used[path]}"


def test_guard_every_command_has_a_rejection_or_a_reason():
    for c in COMMANDS:
        assert c.rejects or c.no_reject, f"{c.key}: rejection case or reason required"


def test_guard_reason_codes_all_reached():
    seen = {r.code for c in COMMANDS for r in c.rejects}
    assert seen <= CODES
    assert seen == CODES, CODES - seen


# ---------- ok ----------


def prepare(c, when, setup) -> dict:
    at(c, when)
    return (setup(c) if setup else None) or {}


def formatted(argv, values):
    return [a.format(**values) if "{" in a else a for a in argv]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda x: x.key)
def test_ok(sim, c, cmd):
    values = prepare(c, cmd.when, cmd.setup)
    r = run_cli(sim.url, formatted(cmd.argv, values), cmd.who)
    payload = assert_ok(r)
    if cmd.check:
        cmd.check(payload, c)


# ---------- rejected ----------

REJECT_CASES = [(cmd, rej) for cmd in COMMANDS for rej in cmd.rejects]


def _rej_id(case):
    cmd, rej = case
    return f"{cmd.key}-{rej.label or rej.code}"


@pytest.mark.parametrize("case", REJECT_CASES, ids=_rej_id)
def test_rejected(sim, c, case):
    cmd, rej = case
    values = prepare(c, rej.when or cmd.when, rej.setup or cmd.setup)
    r = run_cli(sim.url, formatted(rej.argv, values), rej.who or cmd.who, tok=rej.tok)
    assert_rejected(r, rej.code)


def test_rejection_body_matches_the_sim_exactly(sim, c):
    """The CLI forwards the §4.2 body; it writes no reason or hint of its own."""
    at(c, "17:15")
    body = {
        "role": "dispatch",
        "action": "launch",
        "args": {"drone": "D-01", "mission": "incident", "incident": "INC-1", "to": "5.8,3.4"},
    }
    from tests.sim.p3 import auth

    direct = c.post("/actions", json=body, headers=auth("dispatch")).json()
    r = run_cli(
        sim.url,
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
    )
    got = assert_rejected(r, "NO_FLY_ZONE")
    assert (got["code"], got["reason"], got["hint"]) == (
        direct["code"],
        direct["reason"],
        direct["hint"],
    )


def test_a_rejection_changes_nothing(sim, c):
    at(c, "17:45")
    before = c.get("/fleet").json()["drones"]
    assert_rejected(
        run_cli(sim.url, ["rtb", "--drone", "D-14", "--dock", "P9"], "fleet"), "DOCK_OFFLINE"
    )
    assert c.get("/fleet").json()["drones"] == before


def test_missing_token_is_forbidden_role(sim, c):
    at(c, "17:45")
    assert_rejected(run_cli(sim.url, ["rtb", "--drone", "D-14"]), "FORBIDDEN_ROLE")


# ---------- simulate: same args as a write, changes nothing ----------

SIMULATE = [
    (
        "dispatch",
        "17:15",
        [
            "launch",
            "--drone",
            "D-05",
            "--mission",
            "incident",
            "--incident",
            "INC-1",
            "--to",
            "0.75,2.2",
            "--why",
            "x",
        ],
    ),
    ("dispatch", "17:45", ["incident", "close", "--id", "INC-1", "--note", "x"]),
    ("fleet", "17:45", ["rtb", "--drone", "D-14"]),
    ("airspace", "17:45", ["hold", "--drone", "D-02", "--at-boundary", "RED-AIRFIELD"]),
    (
        "airspace",
        "17:45",
        ["corridor", "--drone", "D-04", "--incident", "INC-2", "--to", "5.45,4.45"],
    ),
    ("comms", "17:12:30", ["unit", "task", "--unit", "patrol_car_2", "--incident", "INC-1"]),
    ("comms", "17:45", ["advisory", "--area", "J9", "--text", "x", "--until", "18:00"]),
    ("intel", "17:45", ["evidence", "tag", "--incident", "INC-1", "--clip", "C-3", "--what", "x"]),
]


@pytest.mark.parametrize(
    "who,when,write", SIMULATE, ids=lambda v: v if isinstance(v, str) else " ".join(v[:2])
)
def test_simulate_changes_nothing(sim, c, who, when, write):
    at(c, when)
    before = c.get("/state").json()
    payload = assert_ok(run_cli(sim.url, ["simulate", *write], who))
    assert "would" in json.dumps(payload) and "kpi_delta" in json.dumps(payload), payload
    assert c.get("/state").json() == before


def test_simulate_of_a_refused_write_reports_the_code_without_doing_it(sim, c):
    at(c, "17:45")
    r = run_cli(sim.url, ["simulate", "rtb", "--drone", "D-14", "--dock", "P9"], "fleet")
    assert "DOCK_OFFLINE" in r.out, r
    assert c.get("/fleet/D-14").json()["drone"]["mission"] != "returning"


# ---------- route check facts ----------


def test_route_check_names_the_red_zone(sim, c):
    at(c, "17:45")
    r = run_cli(sim.url, ["route", "check", "--drone", "D-04", "--to", "5.8,3.4"], "airspace")
    assert r.code in (0, 2), r
    assert "RED-AIRFIELD" in r.out, r


def test_route_check_states_recording_off_over_the_hospital(sim, c):
    at(c, "17:45")
    r = run_cli(sim.url, ["route", "check", "--drone", "D-05", "--to", "2.1,4.0"], "airspace")
    payload = assert_ok(r)
    assert "NR-HOSPITAL" in r.out, r
    assert "recording" in r.out.lower(), payload


# ---------- --text ----------


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda x: x.key)
def test_text_is_one_line(sim, c, cmd):
    values = prepare(c, cmd.when, cmd.setup)
    r = run_cli(sim.url, [*formatted(cmd.argv, values), "--text"], cmd.who)
    assert r.code == 0, r
    lines = [ln for ln in r.out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"want one line: {r}"
    assert not lines[0].startswith("{"), f"--text printed JSON: {r}"


def test_a_second_text_flag_keeps_the_message(sim, c):
    """§4.1 uses --text for the message of these commands too: the first one is the message."""
    at(c, "17:45")
    argv = ["advisory", "--area", "J9", "--text", "Ring Road lane 2 closed", "--until", "18:00"]
    assert run_cli(sim.url, [*argv, "--text"], "comms").code == 0
    assert "Ring Road lane 2 closed" in str(c.get("/events").json())


def test_text_rejection_is_one_line_with_the_code(sim, c):
    at(c, "17:45")
    r = run_cli(sim.url, ["rtb", "--drone", "D-14", "--dock", "P9", "--text"], "fleet")
    assert r.code == 2, r
    lines = [ln for ln in r.out.splitlines() if ln.strip()]
    assert len(lines) == 1, r
    assert "DOCK_OFFLINE" in lines[0], r


# ---------- error: dead API URL, for every command ----------


@pytest.fixture(scope="module")
def dead_runs():
    """All commands against a closed port. In parallel: a refused connect takes 2 s on Windows."""
    url = dead_url()

    def one(cmd):
        argv = [a.replace("{aid}", "A-0002") for a in cmd.argv]
        return cmd.key, run_cli(url, argv, cmd.who)

    with ThreadPoolExecutor(8) as pool:
        return dict(pool.map(one, COMMANDS))


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda x: x.key)
def test_error_dead_api(dead_runs, cmd):
    r = dead_runs[cmd.key]
    assert r.code == 1, f"want exit 1: {r}"
    assert "Traceback" not in r.out + r.err, r
    assert (r.out + r.err).strip(), f"silent failure: {r}"


# ---------- HTTP mapping: 2xx → 0, §4.2 code → 2, anything else → 1 ----------


class StubHandler(BaseHTTPRequestHandler):
    status = 200
    body = b"{}"

    def _send(self):
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(type(self).status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    do_GET = _send
    do_POST = _send

    def log_message(self, *_):
        pass


@pytest.fixture(scope="module")
def stub():
    srv = HTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


REJ = {"ok": False, "code": "NOT_FOUND", "reason": "r", "hint": "h"}
MAPPING = [
    (500, b'{"detail":"boom"}', 1),
    (503, b"down", 1),
    (200, b"<html>not json</html>", 1),
    (404, b"plain not found", 1),
    (404, b'{"detail":"Not Found"}', 1),
    (422, b'{"ok":false,"error":"invalid request","reason":"x","hint":""}', 1),
    (409, b'{"ok":false,"code":"NOT_A_CODE","reason":"r","hint":"h"}', 1),
    (401, json.dumps({**REJ, "code": "FORBIDDEN_ROLE"}).encode(), 2),
    (403, json.dumps({**REJ, "code": "FORBIDDEN_ROLE"}).encode(), 2),
    (404, json.dumps(REJ).encode(), 2),
    (409, json.dumps({**REJ, "code": "NO_FLY_ZONE"}).encode(), 2),
]


@pytest.mark.parametrize(
    "argv,who",
    [
        (["kpi"], "intel"),
        (["hold", "--drone", "D-02", "--at-boundary", "RED-AIRFIELD"], "airspace"),
    ],
    ids=["read", "write"],
)
@pytest.mark.parametrize("status,body,want", MAPPING, ids=lambda v: str(v)[:22])
def test_http_mapping(stub, argv, who, status, body, want):
    StubHandler.status, StubHandler.body = status, body
    r = run_cli(stub, argv, who)
    assert r.code == want, f"HTTP {status} {body[:40]!r}: {r}"
    assert "Traceback" not in r.out + r.err, r
    if want == 2:
        assert_rejected(r)


@pytest.mark.parametrize(
    "argv,who",
    [
        (["events", "--since", "not-a-time"], "intel"),
        (["privacy-log", "--since", "not-a-time"], "intel"),
        (["approvals", "--status", "bogus"], "intel"),
        (["log-refusal", "--about", "x", "--code", "NOT_A_CODE", "--by", "k"], "privacy"),
    ],
    ids=lambda v: v[0] if isinstance(v, list) else None,
)
def test_malformed_request_is_error_not_rejection(sim, c, argv, who):
    at(c, "17:45")
    r = run_cli(sim.url, argv, who)
    assert r.code == 1, f"HTTP 422 has no §4.2 code, want exit 1: {r}"
    assert "Traceback" not in r.out + r.err, r


# ---------- environment ----------


def test_default_url_is_the_contract_port():
    src = read_text("cli/airgrid.py")
    assert "http://localhost:8740" in src


# ---------- thin client: no rule logic, no state ----------


def test_cli_imports_nothing_from_the_sim():
    tree = ast.parse(read_text("cli/airgrid.py"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"sim", "sqlite3", "yaml", "worldbot"}, imported


def test_cli_source_holds_no_reason_code_logic():
    """Codes come from the sim; the only table allowed is the set of known §4.2 names."""
    src = (ROOT / "cli" / "airgrid.py").read_text(encoding="utf-8")
    for literal in ("reserve_pct", "wind_limit", "max_drones", "scenarios/", "friday_rush"):
        assert literal not in src, literal


# ---------- human-only commands decide through the sim ----------


def test_cli_approve_by_kazum_takes_effect(sim, c):
    load(c)
    from tests.sim.p3 import approval_id, propose, skip

    skip(c, "17:45")
    aid = approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}))
    assert_ok(run_cli(sim.url, ["approve", aid], ("kazum", "watch_commander")))
    assert {a["id"]: a for a in approvals(c)}[aid]["decided_by"] == "kazum"


def test_cli_take_control_by_kazum_as_remote_pilot(sim, c):
    at(c, "17:45")
    assert_ok(run_cli(sim.url, ["take-control", "D-14"], KAZUM_RP))
    assert c.get("/fleet/D-14").json()["drone"]["pilot"] == "kazum"
