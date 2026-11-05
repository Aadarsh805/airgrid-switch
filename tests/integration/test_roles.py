"""Layer 4 · roles: no agent token can approve; each role's commands per config/roles.yaml."""

import pytest
import yaml

from tests.integration.harness import assert_ok, assert_rejected, run_cli, run_many
from tests.sim.helpers import AGENT_ROLES, read_text
from tests.sim.p3 import KAZUM_RP, approval_id, approvals, approve, at, auth, propose, reject

ROLES = yaml.safe_load(read_text("config/roles.yaml"))

# config/roles.yaml permission -> (when, §4.1 argv)
PERMISSIONS = {
    "incident_open": ("17:45", ["incident", "open", "--from-alert", "INC-2"]),
    "incident_close": ("17:45", ["incident", "close", "--id", "INC-1", "--note", "x"]),
    "launch": (
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
    "propose_track": (
        "17:45",
        ["propose", "track", "--incident", "INC-2", "--scope", "vehicle; 30 min"],
    ),
    "propose_broadcast": ("17:45", ["propose", "broadcast", "--drone", "D-14", "--text", "x"]),
    "rtb": ("17:45", ["rtb", "--drone", "D-14"]),
    "propose_out_of_service": (
        "17:45",
        ["propose", "out-of-service", "--drone", "D-11", "--why", "x"],
    ),
    "hold": ("17:45", ["hold", "--drone", "D-02", "--at-boundary", "RED-AIRFIELD"]),
    "corridor": (
        "17:45",
        ["corridor", "--drone", "D-04", "--incident", "INC-2", "--to", "5.45,4.45"],
    ),
    "propose_zone": ("17:45", ["propose", "zone", "--id", "VIP-TEMP", "--until", "21:00"]),
    "evidence_tag": (
        "17:45",
        ["evidence", "tag", "--incident", "INC-1", "--clip", "C-3", "--what", "x"],
    ),
    "propose_footage_release": (
        "17:45",
        ["propose", "footage-release", "--incident", "INC-1", "--clip", "C-3", "--to", "court"],
    ),
    "privacy_check": ("17:45", ["privacy", "check", "--request", "recording in the geofence"]),
    "unit_task": ("17:45", ["unit", "task", "--unit", "patrol_car_3", "--incident", "INC-2"]),
    "unit_release": (
        "17:20",
        [
            "unit",
            "release",
            "--unit",
            "patrol_car_2",
            "--incident",
            "INC-1",
            "--evidence",
            "2 minor, walking",
        ],
    ),
    "advisory": ("17:45", ["advisory", "--area", "J9", "--text", "x", "--until", "18:00"]),
    "log_refusal": (
        "17:45",
        ["log-refusal", "--about", "x", "--code", "PRIVACY_POLICY", "--by", "kazum"],
    ),
    "simulate": ("17:45", ["simulate", "rtb", "--drone", "D-14"]),
}
READS = [
    ["status"],
    ["fleet"],
    ["docks"],
    ["pilots"],
    ["incidents"],
    ["airspace"],
    ["units"],
    ["events", "--since", "17:40"],
    ["approvals"],
    ["privacy-log"],
    ["kpi"],
    ["decisions"],
]
ANY = set(ROLES["agents"]["any"])
OWNED = [(r, p) for r in AGENT_ROLES for p in ROLES["agents"][r]]
GRANTED = [(r, p) for r in AGENT_ROLES for p in ANY]
FORBIDDEN = [
    (r, p) for r in AGENT_ROLES for p in PERMISSIONS if p not in ROLES["agents"][r] and p not in ANY
]


def test_guard_every_roles_yaml_permission_is_tested():
    in_yaml = {p for v in ROLES["agents"].values() for p in v}
    assert in_yaml == set(PERMISSIONS), in_yaml ^ set(PERMISSIONS)


def test_guard_roles_are_the_agents():
    assert set(ROLES["agents"]) - {"any"} == set(AGENT_ROLES)


def test_contract_table_matches_roles_yaml():
    """04 §4.3 and config/roles.yaml state the same grants for the three plain cases."""
    assert ROLES["agents"]["relay"] == []
    assert ROLES["agents"]["intel"] == ["evidence_tag"]
    assert set(ROLES["approvals"]["remote_pilot"]) == {"take_control", "release_control"}
    assert ROLES["approvals"]["field_officer"] == []


@pytest.mark.parametrize("role,perm", OWNED + GRANTED, ids=lambda v: v)
def test_role_may_run_its_commands(sim, c, role, perm):
    when, argv = PERMISSIONS[perm]
    at(c, when)
    assert_ok(run_cli(sim.url, argv, role))


@pytest.fixture(scope="module")
def forbidden_runs(sim, c):
    """Forbidden calls change nothing, so they share one state per scenario time."""
    out = {}
    for when in sorted({PERMISSIONS[p][0] for _, p in FORBIDDEN}):
        at(c, when)
        cases = [(r, p) for r, p in FORBIDDEN if PERMISSIONS[p][0] == when]
        for case, res in zip(
            cases, run_many(sim.url, [(PERMISSIONS[p][1], r) for r, p in cases]), strict=True
        ):
            out[case] = res
    return out


@pytest.mark.parametrize("role,perm", FORBIDDEN, ids=lambda v: v)
def test_role_is_refused_outside_its_commands(forbidden_runs, role, perm):
    assert_rejected(forbidden_runs[(role, perm)], "FORBIDDEN_ROLE")


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_every_role_can_read(sim, c, role):
    at(c, "17:45")
    for r in run_many(sim.url, [(argv, role) for argv in READS]):
        assert_ok(r)


# ---------- L42 dispatch commands ----------

DISPATCH_ONLY = [
    ["divert", "--drone", "D-13", "--incident", "INC-2", "--mission", "track"],
    ["track", "assign", "--drone", "D-13", "--incident", "INC-2"],
    ["propose", "standing-policy", "--scope", "x"],
]


@pytest.mark.parametrize("argv", DISPATCH_ONLY, ids=lambda v: " ".join(v[:2]))
@pytest.mark.parametrize("role", [r for r in AGENT_ROLES if r != "dispatch"])
def test_only_dispatch_runs_the_l42_commands(sim, c, role, argv):
    at(c, "17:45")
    assert_rejected(run_cli(sim.url, argv, role), "FORBIDDEN_ROLE")


# ---------- nobody but a human approves ----------


def _pending_track(c) -> str:
    at(c, "17:45")
    return approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}))


def _status(c, aid: str) -> str:
    return {a["id"]: a for a in approvals(c)}[aid]["status"]


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_no_agent_token_approves_over_http(c, role):
    aid = _pending_track(c)
    r = approve(c, aid, who=role)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "FORBIDDEN_ROLE"
    assert _status(c, aid) == "pending"


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_no_agent_token_rejects_over_http(c, role):
    aid = _pending_track(c)
    r = reject(c, aid, "agent says no", who=role)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "FORBIDDEN_ROLE"
    assert _status(c, aid) == "pending"


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_no_agent_token_approves_through_the_cli(sim, c, role):
    aid = _pending_track(c)
    assert_rejected(run_cli(sim.url, ["approve", aid], role), "FORBIDDEN_ROLE")
    assert _status(c, aid) == "pending"


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_agent_cannot_claim_a_human_role(sim, c, role):
    """AIRGRID_ROLE=watch_commander with an agent token buys nothing."""
    aid = _pending_track(c)
    r = run_cli(sim.url, ["approve", aid], (role, "watch_commander"))
    assert_rejected(r, "FORBIDDEN_ROLE")
    assert _status(c, aid) == "pending"


def test_agent_cannot_borrow_another_desk(sim, c):
    """dispatch token, AIRGRID_ROLE=fleet: the sim refuses the mismatch."""
    at(c, "17:45")
    r = run_cli(sim.url, ["rtb", "--drone", "D-14"], ("dispatch", "fleet"))
    assert_rejected(r, "FORBIDDEN_ROLE")


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_no_agent_takes_control(sim, c, role):
    at(c, "17:45")
    assert_rejected(run_cli(sim.url, ["take-control", "D-14"], role), "FORBIDDEN_ROLE")


def test_simulate_cannot_launder_an_approval(c):
    aid = _pending_track(c)
    body = {
        "role": "dispatch",
        "action": "simulate",
        "args": {"action": "approve", "args": {"id": aid}},
    }
    r = c.post("/actions", json=body, headers=auth("dispatch"))
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "FORBIDDEN_ROLE"
    assert _status(c, aid) == "pending"


def test_wrong_role_human_cannot_approve(sim, c):
    """t2 is a field officer; track needs a watch commander."""
    aid = _pending_track(c)
    assert_rejected(run_cli(sim.url, ["approve", aid], ("t2", "field_officer")), "FORBIDDEN_ROLE")
    assert _status(c, aid) == "pending"


def test_wrong_role_human_cannot_take_control(sim, c):
    at(c, "17:45")
    r = run_cli(sim.url, ["take-control", "D-14"], ("t2", "field_officer"))
    assert_rejected(r, "FORBIDDEN_ROLE")


def test_human_token_decides(sim, c):
    aid = _pending_track(c)
    assert_ok(run_cli(sim.url, ["approve", aid], ("kazum", "watch_commander")))
    assert _status(c, aid) == "approved"


def test_remote_pilot_takes_control(sim, c):
    at(c, "17:45")
    assert_ok(run_cli(sim.url, ["take-control", "D-14"], KAZUM_RP))
