"""Gate · full FRIDAY-RUSH through FakeRoom: kazum approves in chat, the World Bot makes the sim
calls, and the run reproduces the golden timeline and scorecard. Plus the saved dry-run report.

Two independent runs: `chat_run` drives the conductor from scripts/demo_run.py against this suite's
sim so results can be read back from the sim itself; `dry_run` runs the script as a user does.
"""

import ast
import asyncio
import importlib.util
import io
import json
import re
import subprocess
import sys

import pytest

from sim import golden
from tests.integration.conftest import TOKENS_PATH
from tests.integration.harness import FRIDAY, base_env, leak_findings, token_findings
from tests.sim.helpers import ROOT, read_text
from tests.sim.p3 import approval_id, load, propose, skip
from worldbot.decisions_sink import FakeSink

EXPECTED_TEXT = read_text("docs/golden-timeline.txt")
EXPECTED = FRIDAY["expected"]["managed"]


def load_demo_run():
    spec = importlib.util.spec_from_file_location("demo_run", ROOT / "scripts" / "demo_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["demo_run"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def reference():
    return golden.run("FRIDAY-RUSH")


@pytest.fixture(scope="module")
def chat_run(sim, c, tmp_path_factory):
    demo_run = load_demo_run()
    sink = FakeSink(tmp_path_factory.mktemp("sink") / "decisions.jsonl")
    out = io.StringIO()
    scenario = demo_run.load_scenario("FRIDAY-RUSH")
    result = asyncio.run(
        asyncio.wait_for(demo_run.conduct(sim.url, TOKENS_PATH, sink, scenario, out), 240)
    )
    return {
        **result,
        "sink": sink,
        "printed": out.getvalue(),
        "live_status": c.get("/status").json(),
        "hash": demo_run.event_hash(result["events"]),
        "lines": demo_run.timeline(result["events"]),
    }


@pytest.fixture(scope="module")
def dry_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("runs")
    p = subprocess.run(
        [sys.executable, "scripts/demo_run.py", "--out", str(out), "--quiet"],
        cwd=ROOT,
        env=base_env(),
        capture_output=True,
        timeout=300,
    )
    return {
        "code": p.returncode,
        "stdout": p.stdout.decode("utf-8", "replace"),
        "stderr": p.stderr.decode("utf-8", "replace"),
        "reports": sorted(out.glob("demo-dryrun-*.md")),
        "dir": out,
    }


@pytest.fixture(scope="module")
def report(dry_run):
    assert len(dry_run["reports"]) == 1, (dry_run["stdout"][-800:], dry_run["stderr"][-800:])
    return dry_run["reports"][0].read_text(encoding="utf-8")


def section(text: str, title: str) -> str:
    m = re.search(rf"^## {title}.*?(?=^## |\Z)", text, re.M | re.S)
    assert m, f"report has no section {title!r}"
    return m.group(0)


# ---------- the golden outcome ----------


def test_timeline_has_zero_diffs(chat_run):
    diffs = golden.diff(chat_run["lines"], EXPECTED_TEXT)
    assert diffs == [], "\n".join(diffs[:15])


def test_event_hash_equals_the_golden_run(chat_run, reference):
    assert chat_run["hash"] == reference.event_hash


def test_managed_kpis_equal_the_golden_run(chat_run, reference):
    managed = chat_run["kpi"]["managed"]
    assert {k: managed.get(k) for k in reference.kpi} == reference.kpi


@pytest.mark.parametrize(
    "key,value",
    [
        ("coverage_pct", EXPECTED["coverage_pct"]),
        ("flights", EXPECTED["flights"]),
        ("lowest_landing_pct", EXPECTED["lowest_landing_pct"]),
        ("reserve_breaches", 0),
        ("red_zone_entries", 0),
    ],
)
def test_scorecard_numbers(chat_run, key, value):
    got = chat_run["kpi"]["managed"][key]
    # scenario `expected` rounds the landing battery to a whole percent, as the scorecard shows it
    assert (round(got) if key == "lowest_landing_pct" else got) == value


def test_scorecard_wins_every_counted_line(chat_run):
    wins = chat_run["kpi"]["wins"]
    assert wins, "no counted lines"
    assert all(v is True for v in wins.values()), wins


def test_scorecard_baseline_equals_the_golden_run(chat_run, reference):
    served = chat_run["kpi"]["baseline"]
    for k, v in reference.baseline.items():
        assert served[k] == v, k


def test_run_finished_at_shift_end(chat_run):
    assert chat_run["live_status"]["done"] is True


def test_no_violations(chat_run):
    assert not [e for e in chat_run["events"] if e["type"] == "violation"]


# ---------- approvals really went through chat ----------


def test_every_approval_was_decided_by_kazum(chat_run):
    assert len(chat_run["approvals"]) >= 3
    for a in chat_run["approvals"]:
        assert a["status"] == "approved", a
        assert a["decided_by"] == "kazum", a
        assert a["created_by"] in ("dispatch", "fleet", "airspace", "privacy"), a


def test_every_approval_passed_through_the_room(chat_run):
    """A card, then the result line after kazum's reply, then a sink entry."""
    posted = chat_run["room"]
    entries = chat_run["sink"].entries
    for a in chat_run["approvals"]:
        aid = a["id"]
        card = [i for i, (_, t) in enumerate(posted) if t.startswith(f"🟡 APPROVAL {aid} ")]
        done = [
            i for i, (_, t) in enumerate(posted) if t.startswith(f"✅ APPROVED {aid} by @kazum")
        ]
        assert len(card) == 1, aid
        assert len(done) == 1, aid
        assert card[0] < done[0], aid
        assert [e for e in entries if e["kind"] == "approved" and e["target"] == aid], aid


def test_sim_log_shows_the_human_token_decided(chat_run):
    approved = {r["approval_id"] for r in chat_run["decisions"] if r["action"] == "approve"}
    assert approved == {a["id"] for a in chat_run["approvals"]}
    for r in chat_run["decisions"]:
        if r["action"] in ("approve", "reject"):
            assert r["actor"] == "kazum", r


def test_the_clock_held_on_every_card(chat_run):
    assert chat_run["held"], "no holds recorded"
    assert all(chat_run["held"].values()), chat_run["held"]


def test_bot_stopped_the_two_who_may_not(chat_run):
    refused = [e for e in chat_run["sink"].entries if e["kind"] == "refusal"]
    assert {e["actor"] for e in refused} >= {"dispatch", "visitor42"}
    assert all(e["code"] == "FORBIDDEN_ROLE" for e in refused), refused
    logged = {r["actor"] for r in chat_run["decisions"]}
    assert "visitor42" not in logged, "the refused author reached the sim"


def test_take_control_came_from_chat(chat_run):
    assert [e for e in chat_run["sink"].entries if e["kind"] == "control"]
    assert any(t.startswith("🕹 take-control D-04 by @kazum") for _, t in chat_run["room"])


# ---------- rooms ----------


def test_simulated_lines_in_the_room_are_labelled(chat_run):
    voices = [t for _, t in chat_run["room"] if t.startswith(("🛩", "📻"))]
    assert voices
    assert all("simulated" in t for t in voices), voices[:3]


def test_owner_mentioned_on_every_alert(chat_run):
    alerts = [t for _, t in chat_run["room"] if t.startswith("📣")]
    assert len(alerts) == len(chat_run["events"])
    assert all(re.search(r" · @\w+$", t) for t in alerts), alerts[:3]


def test_room_alerts_never_run_ahead_of_their_event(chat_run):
    alerts = [t for _, t in chat_run["room"] if t.startswith("📣")]
    bad = []
    for e, text in zip(chat_run["events"], alerts, strict=True):
        bad += leak_findings(e["t"], f"room {e['clock']}", text)
    assert not bad, "\n".join(bad[:10])


def test_no_token_anywhere_in_the_run(chat_run):
    bad = token_findings("room", "\n".join(t for _, t in chat_run["room"]))
    bad += token_findings("printed", chat_run["printed"])
    bad += token_findings("sink", json.dumps(chat_run["sink"].entries))
    bad += token_findings("sink file", chat_run["sink"].path.read_text(encoding="utf-8"))
    assert not bad, "\n".join(bad)


# ---------- L42: agent-side steps use the CLI ----------

HTTP_WRITES = {"post", "put", "patch", "delete", "request", "stream", "send"}
# The operator side of the harness: no agent or human decision goes through these.
OPERATOR_POSTS = {"/control/skip", "/baseline/run", "/scenario/load"}


def raw_http_writes(source: str) -> list[str]:
    """Every HTTP write in `source` outside the operator allow-list, as readable strings."""
    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            bad += [f"imports {n}" for n in names if n.split(".")[0] in ("requests", "urllib")]
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in HTTP_WRITES:
            continue
        first = node.args[0] if node.args else None
        path = first.value if isinstance(first, ast.Constant) else None
        if path not in OPERATOR_POSTS:
            bad.append(ast.unparse(node)[:100])
    return bad


def test_http_scanner_catches_agent_writes():
    assert raw_http_writes('await self.api.post("/actions", json={})')
    assert raw_http_writes('await api.post("/approvals", json=b)')
    assert raw_http_writes('await api.post(f"/approvals/{a}/approve")')
    assert raw_http_writes("await api.post(path)")
    assert raw_http_writes('httpx.post("http://x/actions")')
    assert raw_http_writes("import requests")
    assert not raw_http_writes('await api.post("/control/skip", json={})')


def test_demo_run_has_no_raw_http_for_agent_steps():
    bad = raw_http_writes(read_text("scripts/demo_run.py"))
    assert not bad, bad


def test_demo_run_drives_agents_through_the_cli():
    src = read_text("scripts/demo_run.py")
    assert '"cli.airgrid"' in src
    assert "AIRGRID_TOKEN" in src


# ---------- L42: hold_clock belongs to the sim ----------

HOLD_CASES = [
    ("track", {"incident": "INC-2", "minutes": 20}, "dispatch"),
    ("broadcast", {"drone": "D-02", "text": "keep left"}, "dispatch"),
    ("footage_release", {"incident": "INC-1", "clip": "C-3", "to": "court"}, "privacy"),
    ("zone", {"zone": "VIP-TEMP", "until": "21:00"}, "airspace"),
    ("out_of_service", {"drone": "D-11", "why": "motor"}, "fleet"),
    ("standing_policy", {}, "dispatch"),
]


@pytest.mark.parametrize("mode", ["demo", "live"])
@pytest.mark.parametrize("extra", [{}, {"hold_clock": False}], ids=["omitted", "client_false"])
@pytest.mark.parametrize("type_,args,by", HOLD_CASES, ids=[x[0] for x in HOLD_CASES])
def test_hold_clock_comes_from_the_sim(c, type_, args, by, extra, mode):
    load(c, mode=mode)
    skip(c, "17:45")
    aid = approval_id(propose(c, type_, args, "x", who=by, **extra))
    view = {a["id"]: a for a in c.get("/approvals").json()["approvals"]}[aid]
    assert view["hold_clock"] is True, view
    assert f"approval:{aid}" in c.get("/status").json()["clock_held"]


def test_no_cli_flag_sets_hold_clock():
    assert "hold" + "_clock" not in read_text("cli/airgrid.py")


# ---------- dry-run report ----------


def test_dry_run_exits_clean(dry_run):
    assert dry_run["code"] == 0, dry_run["stdout"][-1500:] + dry_run["stderr"][-1500:]
    assert "RESULT: golden outcome reproduced" in dry_run["stdout"]


def test_report_is_named_as_the_interface_says(dry_run):
    assert re.fullmatch(r"demo-dryrun-\d{8}-\d{6}\.md", dry_run["reports"][0].name)


def test_report_timeline_has_zero_diffs(report):
    body = section(report, "Event timeline")
    lines = re.search(r"```\n(.*?)```", body, re.S).group(1).splitlines()
    assert golden.diff(lines, EXPECTED_TEXT) == []


def test_report_says_no_differences(report):
    assert "None." in section(report, "Differences from docs/golden-timeline.txt")


def test_report_lists_every_approval_and_decision(report):
    body = section(report, "Approvals and decisions")
    rows = re.findall(r"^\| (A-\d{4}) \|", body, re.M)
    want = 1 + sum(1 for a in FRIDAY["golden_actions"] if a["act"] == "approve")
    assert len(rows) == want, rows
    assert body.count("| approved | kazum |") == want
    assert "| NO |" not in body, "a card without a clock hold"


def scripted_codes() -> set[str]:
    out = {a["code"] for a in FRIDAY["golden_actions"] if a["act"] == "refuse"}
    out |= {a["expect"] for a in FRIDAY["golden_actions"] if a.get("expect")}
    return out


def test_scripted_codes_are_the_four_of_the_scenario():
    assert scripted_codes() == {
        "PRIVACY_POLICY",
        "NO_FLY_ZONE",
        "MISSION_EXPIRED",
        "PILOT_SATURATED",
    }


@pytest.mark.parametrize("code", sorted(scripted_codes() | {"FORBIDDEN_ROLE"}))
def test_report_lists_every_refusal_code(report, code):
    assert f"**{code}**" in section(report, "Refusals"), code


def test_report_lists_every_refusal_of_the_run(report, chat_run):
    """Same decisions, same run: one report line per sim refusal and per bot refusal."""
    body = section(report, "Refusals")
    bullets = re.findall(r"^- .*→ \*\*([A-Z_]+)\*\*", body, re.M)
    sim_codes = [r["code"] for r in chat_run["decisions"] if r["code"]]
    bot_codes = [e["code"] for e in chat_run["sink"].entries if e["kind"] == "refusal"]
    assert sorted(bullets) == sorted(sim_codes + bot_codes)


def test_report_quotes_the_refused_requests(report):
    for a in FRIDAY["golden_actions"]:
        if a["act"] == "refuse":
            assert a["request"] in report, a["request"]


def test_report_lists_simulated_lines_with_labels(report):
    body = section(report, "Simulated lines")
    lines = [ln for ln in body.splitlines() if ln.startswith("- ")]
    assert lines
    assert all("simulated" in ln for ln in lines), lines[:3]


def test_report_compares_kpis_with_the_baseline(report):
    body = section(report, "Final KPIs vs baseline")
    assert "114" in body
    assert "17.0" in body
    assert "lost" in body.lower()


def test_report_is_labelled_simulated(report):
    assert "simulated" in report.splitlines()[0]


def test_report_holds_no_credentials(report):
    assert "Bearer" not in report
    assert "AIRGRID_TOKEN" not in report
    assert not token_findings("report", report)


def test_dry_run_wrote_the_decision_sink_next_to_the_report(dry_run):
    files = list(dry_run["dir"].glob("demo-dryrun-*-decisions.jsonl"))
    assert len(files) == 1
    rows = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    assert {"approved", "refusal"} <= {r["kind"] for r in rows}
