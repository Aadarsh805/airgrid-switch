"""The agent harness with a stub in place of `claude`: no LLM, no tokens spent.

The stub runs the real `_bin/airgrid` from the agent's install dir, so the wrapper log, the
isolation of the install and the stage of every headless case are checked on each `pytest -q`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agents.harness import formats as f
from agents.harness.run_agent import AGENTS, parse_claude_output, stage
from tests.agents.cases import BY_NAME, CASES, quiet_rotation_line, quiet_rotation_time

STUB = """
import json, os, shutil, subprocess, sys
prompt = sys.stdin.buffer.read().decode("utf-8")
bash = os.environ["STUB_BASH"]
p = subprocess.run([bash, "-c", "airgrid status; airgrid launch --drone D-99 --mission incident "
    "--incident INC-1 --to 1,1 --why 'two words'"], capture_output=True)
print(json.dumps({"subtype": "success", "is_error": False, "total_cost_usd": 0.01,
    "result": "NO_REPLY" + str(len(prompt))}))
"""


@pytest.fixture(scope="module")
def s(tmp_path_factory):
    work = tmp_path_factory.mktemp("stub")
    templates = work / "templates"
    for name in AGENTS:
        (templates / name).mkdir(parents=True)
        (templates / name / "CLAUDE.md").write_text(f"# {name}\n", encoding="utf-8")
    script = work / "stub_claude.py"
    script.write_text(STUB, encoding="utf-8")
    shim = work / "claude.cmd"
    shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    from agents.harness.run_agent import find_bash

    saved = dict(os.environ)
    os.environ |= {
        "AIRGRID_AGENT_TEMPLATES": str(templates),
        "AIRGRID_CLAUDE_BIN": str(shim),
        "STUB_BASH": find_bash(),
    }
    try:
        with stage() as st:
            yield st
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_case_stage_and_forced_rejection(s, case):
    s.drive(case.at)
    forced = case.probe(s)
    assert forced.code == case.forced_code, forced


def test_quiet_stage_reaches_its_time():
    with stage("QUIET-SHIFT") as q:
        at = quiet_rotation_time()
        q.drive(at)
        assert q.get("/status")["clock"] == at
        assert "launches for" in quiet_rotation_line(q)


def test_red_zone_probe_still_refused_after_the_agent_holds(s):
    s.drive("17:55")
    held = s.cli("airspace", "hold", "--drone", "D-12", "--at-boundary", "RED-AIRFIELD")
    assert held.exit_code == 0, held
    assert BY_NAME["red_zone"].probe(s).code == "NO_FLY_ZONE"


def test_route_check_takes_dock_ids(s):
    s.drive("17:35")
    for dock, at in (("HUB-B", [3.0, 2.0]), ("P3", [3.75, 5.0])):
        r = s.cli("fleet", "route", "check", "--drone", "D-09", "--to", dock)
        assert r.exit_code == 0, r
        assert r.json["to"] == at, r
    assert s.cli("fleet", "route", "check", "--drone", "D-09", "--to", "NOPE").code == "NOT_FOUND"


def test_slot_case_reroutes_around_p9_and_p10(s):
    s.drive("17:35")
    r = s.cli("fleet", "rtb", "--drone", "D-09", "--why", "accident cleared")
    assert r.exit_code == 0, r
    assert r.json["result"]["dock"] == "HUB-B", r
    assert {"lands_at", "lands_pct"} <= set(r.json["result"]), r
    events = json.dumps(s.get("/events", since="17:35"), ensure_ascii=False)
    assert "DOCK_OFFLINE" in events and "DOCK_FULL" in events


def test_turn_through_the_wrapper_is_logged_and_scoped(s):
    s.drive("17:14")
    r = s.ask("dispatch", "[#dispatch] kazum: hello 🚁 | pipe\nsecond line", "17:14")
    assert r.reply.startswith("NO_REPLY"), r
    assert r.cost == pytest.approx(0.01)
    assert [c.command for c in r.cli_calls] == ["status", "launch"], r.dump()
    assert r.exit_codes == [0, 2], r.dump()
    status, launch = r.cli_calls
    assert status.json["ok"] is True
    assert launch.code == "NOT_FOUND", launch
    assert launch.argv[-1] == "two words", launch
    assert not f.forbidden_calls("dispatch", [c.argv for c in r.cli_calls])
    assert s.tokens["dispatch"] not in Path(s.work / "calls-1-dispatch.log").read_text("utf-8")


def test_real_login_failure_surfaces_claude_words():
    from agents.harness.run_agent import ClaudeError

    out = json.dumps({"is_error": True, "subtype": "success", "result": "Not logged in"})
    with pytest.raises(ClaudeError, match="Not logged in"):
        parse_claude_output(out, "", 1)
    with pytest.raises(ClaudeError, match="boom"):
        parse_claude_output("", "boom", 1)
    budget = json.dumps({"is_error": False, "subtype": "error_max_budget_usd"})
    with pytest.raises(ClaudeError, match="error_max_budget_usd"):
        parse_claude_output(budget, "", 0)
