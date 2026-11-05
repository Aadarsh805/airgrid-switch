"""The §4.1 command table used by test_cli.py and test_leak.py.

One Cmd per §4.1 command form. The sim runs the golden actions in headless mode, so `when` picks a
known state (see docs/golden-timeline.txt). `setup` prepares anything else and may return format
values for `{placeholders}` in argv. Numbers and ids come from scenarios/friday_rush.yaml.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tests.integration.harness import find_key, has_key
from tests.sim.p3 import approval_id, approvals, approve, drone, ok, propose, rows
from tests.sim.p4 import UNIT_IDS, get_ok, incident, trigger_ok, units

READER = "intel"
KAZUM_WC = ("kazum", "watch_commander")
KAZUM_RP = ("kazum", "remote_pilot")
BAD_TOKEN = "not-a-real-token"


@dataclass(frozen=True)
class Rej:
    argv: tuple
    code: str
    when: str | None = None
    setup: Callable | None = None
    who: object = None
    tok: str | None = None
    label: str = ""


@dataclass(frozen=True)
class Cmd:
    key: str
    argv: tuple
    who: object = READER
    when: str = "17:45"
    setup: Callable | None = None
    check: Callable | None = None
    rejects: tuple = ()
    no_reject: str = ""  # why this command has no rejection path
    writes: bool = False
    tags: tuple = field(default_factory=tuple)


def _track_proposal(c):
    return {"aid": approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}))}


def _ooo_proposal(c):
    return {"aid": approval_id(propose(c, "out_of_service", {"drone": "D-11", "why": "motor"}))}


def _take_d14(c):
    from tests.sim.p3 import act, ok

    ok(act(c, "take_control", {"drone": "D-14"}))


def _pending(c, type_):
    return [a for a in approvals(c, "pending") if a["type"] == type_]


def _has_pending(type_, **want):
    def check(p, c):
        found = _pending(c, type_)
        assert found, f"no pending {type_}: {p}"
        a = found[-1]
        assert a["created_by"], a
        for k, v in want.items():
            assert a["args"].get(k) == v, a
        assert find_key(p, "id") == a["id"] or a["id"] in str(p), p

    return check


def _fleet_check(p, c):
    assert len(rows(p, "drones")) == 14, p


def _fleet_one(p, c):
    assert find_key(p, "id") == "D-09", p
    assert has_key(p, "bat", "state"), p


def _docks_check(p, c):
    assert len(rows(p, "docks")) == 14, p
    assert p["slots_total"] == 18, p


def _status_check(p, c):
    for k in ("clock", "mode", "scenario", "pending_approvals", "kpi"):
        assert k in p, f"status lacks {k}: {p}"
    assert p["scenario"] == "FRIDAY-RUSH", p


def _incidents_check(p, c):
    assert {i["id"] for i in p["incidents"]} == {"INC-1", "INC-2"}, p


def _incident_one(p, c):
    assert p["incident"]["id"] == "INC-2", p


def _airspace_check(p, c):
    assert "zones" in p and "weather" in p, p


def _units_check(p, c):
    assert {u["id"] for u in p["units"]} == set(UNIT_IDS), p


def _events_check(p, c):
    assert p["events"], p
    assert all(e["t"] >= 17 * 60 + 40 - 1e-6 for e in p["events"]), p


def _pending_check(p, c):
    assert [a["status"] for a in p["approvals"]] == ["pending"], p


def _privacy_check(p, c):
    assert isinstance(p["privacy_log"], list) and p["privacy_log"], p


def _kpi_check(p, c):
    assert "managed" in p, p


def _decisions_check(p, c):
    assert isinstance(p["decisions"], list) and p["decisions"], p


def _feed_check(p, c):
    served = str(p)
    assert "2 minor, walking" in served, p


def _rotation_check(p, c):
    assert "NORTH" in str(p) and "D-" in str(p), p


def _route_check(p, c):
    assert has_key(p, "zones"), p
    assert has_key(p, "recording"), p
    assert has_key(p, "eta"), p


def _track_check(p, c):
    assert "D-13" in str(p), p
    assert has_key(p, "relief"), p
    assert has_key(p, "handoff"), p


def _relay_check(p, c):
    assert "INC-2" in str(p) or "D-" in str(p), p


def _launched(did, mission):
    def check(p, c):
        d = drone(c, did)
        assert d["state"] == "airborne", d
        assert d["mission"] == mission, d

    return check


def _closed(iid):
    def check(p, c):
        assert incident(c, iid)["status"] == "closed"

    return check


def _returning(did, dock=None):
    def check(p, c):
        d = drone(c, did)
        assert d["mission"] == "returning", d
        if dock:
            assert dock in str(p) or dock in str(d), (p, d)

    return check


def _held(did):
    def check(p, c):
        assert drone(c, did)["mission"] == "hold"

    return check


def _lawful(p, c):
    assert "lawful" in str(p), p


def _tagged(p, c):
    assert "lane 2 blocked" in str(get_ok(c, "/events")), p


def _unit_tasked(p, c):
    assert units(c)["patrol_car_2"]["status"] == "en_route", p
    assert units(c)["patrol_car_2"]["incident"] == "INC-1", p


def _unit_released(p, c):
    assert units(c)["patrol_car_2"]["status"] == "returning", p


def _advisory(p, c):
    blob = str(get_ok(c, "/events"))
    assert "Ring Road eastbound lane 2 closed" in blob, p


def _refusal_logged(p, c):
    assert "face recognition on the crowd" in str(get_ok(c, "/privacy-log")), p


def _approved(aid_key="aid"):
    def check(p, c):
        assert "approved" in str(p), p

    return check


def _simulated(p, c):
    would = find_key(p, "would")
    assert isinstance(would, dict), p
    assert would.get("ok") is True, p
    assert isinstance(find_key(p, "kpi_delta"), dict), p
    assert drone(c, "D-05")["state"] == "docked", "simulate changed the world"


def _ctrl(state):
    def check(p, c):
        assert drone(c, "D-14").get("manual") is state, drone(c, "D-14")

    return check


LAUNCH = ("launch", "--drone", "D-05", "--mission", "incident", "--incident", "INC-1")


def _bad_token(*argv, who=None):
    return Rej(tuple(argv), "FORBIDDEN_ROLE", who=who, tok=BAD_TOKEN, label="bad_token")


def assert_recording(p, on: bool):
    assert find_key(p, "recording") is on, p


def assert_released(p):
    assert find_key(p, "released") is True, p


def _footage_pending(c):
    args = {"incident": "INC-1", "clip": "C-3", "to": "traffic court"}
    return {"aid": approval_id(propose(c, "footage_release", args))}


def _footage_approved(c):
    values = _footage_pending(c)
    ok(approve(c, values["aid"]))
    return values


def _inc_t(c):
    trigger_ok(c, "fire")


def _intruder(c):
    trigger_ok(c, "unauthorised_drone")


def _nothing(c):
    """A rejection case that must not inherit its command's setup."""
    return {}


def _track_authorised(c):
    aid = approval_id(propose(c, "track", {"incident": "INC-2", "minutes": 20}))
    ok(approve(c, aid))


def _tracking(did):
    def check(p, c):
        d = drone(c, did)
        assert d["mission"] == "track" and d["incident"] == "INC-2", d

    return check


def _flown_by(did, pilot):
    def check(p, c):
        assert drone(c, did)["pilot"] == pilot, drone(c, did)

    return check


CORRIDOR_D01 = (
    "launch",
    "--drone",
    "D-01",
    "--mission",
    "corridor",
    "--incident",
    "INC-2",
    "--to",
    "4.4,5.0",
    "--why",
    "J4 approach",
)
INC1_D01 = (
    "launch",
    "--drone",
    "D-01",
    "--mission",
    "incident",
    "--incident",
    "INC-1",
    "--to",
    "0.75,2.2",
    "--why",
    "upstream queue",
)
UNIT_RELEASE = ("unit", "release", "--unit", "patrol_car_2", "--incident", "INC-1")

COMMANDS: tuple[Cmd, ...] = (
    # ---- Read (all agent roles)
    Cmd("status", ("status",), check=_status_check, no_reject="plain read, takes no id"),
    Cmd("fleet", ("fleet",), check=_fleet_check, no_reject="plain read, takes no id"),
    Cmd(
        "fleet --id",
        ("fleet", "--id", "D-09"),
        check=_fleet_one,
        rejects=(Rej(("fleet", "--id", "D-99"), "NOT_FOUND"),),
    ),
    Cmd("docks", ("docks",), check=_docks_check, no_reject="plain read, takes no id"),
    Cmd("pilots", ("pilots",), no_reject="plain read, takes no id"),
    Cmd("incidents", ("incidents",), check=_incidents_check, no_reject="plain read"),
    Cmd(
        "incidents --id",
        ("incidents", "--id", "INC-2"),
        check=_incident_one,
        rejects=(Rej(("incidents", "--id", "INC-99"), "NOT_FOUND"),),
    ),
    Cmd("airspace", ("airspace",), check=_airspace_check, no_reject="plain read"),
    Cmd("units", ("units",), check=_units_check, no_reject="plain read"),
    Cmd(
        "events",
        ("events", "--since", "17:40"),
        check=_events_check,
        no_reject="plain read; a bad --since is malformed (exit 1), see test_cli",
    ),
    Cmd(
        "approvals",
        ("approvals", "--status", "pending"),
        setup=_track_proposal,
        check=_pending_check,
        no_reject="plain read; a bad --status is malformed (exit 1), see test_cli",
    ),
    Cmd(
        "privacy-log",
        ("privacy-log", "--since", "17:00"),
        check=_privacy_check,
        no_reject="plain read; a bad --since is malformed (exit 1), see test_cli",
    ),
    Cmd("kpi", ("kpi",), check=_kpi_check, no_reject="plain read"),
    Cmd("decisions", ("decisions",), check=_decisions_check, no_reject="plain read"),
    # ---- dispatch
    Cmd(
        "incident open",
        ("incident", "open", "--from-alert", "INC-2"),
        who="dispatch",
        rejects=(
            Rej(("incident", "open", "--from-alert", "CAD-99"), "NOT_FOUND", who="dispatch"),
            _bad_token("incident", "open", "--from-alert", "INC-2", who="dispatch"),
        ),
        writes=True,
    ),
    Cmd(
        "incident close",
        ("incident", "close", "--id", "INC-1", "--note", "tow cleared"),
        who="dispatch",
        check=_closed("INC-1"),
        rejects=(
            Rej(
                ("incident", "close", "--id", "INC-2", "--note", "again"),
                "MISSION_EXPIRED",
                when="18:10",
                who="dispatch",
            ),
            Rej(
                ("incident", "close", "--id", "INC-99", "--note", "x"), "NOT_FOUND", who="dispatch"
            ),
        ),
        writes=True,
    ),
    Cmd(
        "launch",
        (*LAUNCH, "--to", "0.75,2.2", "--why", "upstream queue"),
        who="dispatch",
        when="17:15",
        check=_launched("D-05", "incident"),
        rejects=(
            Rej(
                (
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
                    "through the airfield",
                ),
                "NO_FLY_ZONE",
                when="17:15",
                who="dispatch",
            ),
            Rej(
                (
                    "launch",
                    "--drone",
                    "D-09",
                    "--mission",
                    "incident",
                    "--incident",
                    "INC-1",
                    "--to",
                    "0.75,2.2",
                    "--why",
                    "already flying",
                ),
                "DRONE_NOT_READY",
                when="17:15",
                who="dispatch",
            ),
            Rej(
                (
                    "launch",
                    "--drone",
                    "D-01",
                    "--mission",
                    "incident",
                    "--incident",
                    "INC-1",
                    "--to",
                    "12.0,5.0",
                    "--why",
                    "too far",
                ),
                "OUT_OF_RANGE",
                when="17:15",
                who="dispatch",
            ),
            Rej(
                (
                    "launch",
                    "--drone",
                    "D-01",
                    "--mission",
                    "incident",
                    "--incident",
                    "INC-T1",
                    "--to",
                    "1.0,5.0",
                    "--why",
                    "into the front",
                ),
                "WEATHER_GROUNDED",
                when="18:25",
                setup=_inc_t,
                who="dispatch",
            ),
            Rej(
                (
                    "launch",
                    "--drone",
                    "D-01",
                    "--mission",
                    "incident",
                    "--incident",
                    "INC-1",
                    "--to",
                    "1.0,5.2",
                    "--why",
                    "intruder sector",
                ),
                "SEPARATION",
                when="17:20",
                setup=_intruder,
                who="dispatch",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "propose track",
        (
            "propose",
            "track",
            "--incident",
            "INC-2",
            "--scope",
            "suspect vehicle only; district; 30 min",
        ),
        who="dispatch",
        when="17:40:30",
        check=_has_pending("track", incident="INC-2"),
        rejects=(
            Rej(
                ("propose", "track", "--incident", "INC-99", "--scope", "x; 30 min"),
                "NOT_FOUND",
                who="dispatch",
            ),
            Rej(
                ("propose", "track", "--incident", "INC-2", "--scope", "x; 30 min"),
                "MISSION_EXPIRED",
                when="18:10",
                who="dispatch",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "propose broadcast",
        ("propose", "broadcast", "--drone", "D-09", "--text", "Lane 2 closed, keep left"),
        who="dispatch",
        when="17:16",
        check=_has_pending("broadcast", drone="D-09", text="Lane 2 closed, keep left"),
        rejects=(
            Rej(
                ("propose", "broadcast", "--drone", "D-99", "--text", "x"),
                "NOT_FOUND",
                who="dispatch",
            ),
        ),
        writes=True,
    ),
    # ---- fleet
    Cmd(
        "plan rotation",
        ("plan", "rotation", "--loop", "NORTH"),
        who="fleet",
        check=_rotation_check,
        rejects=(Rej(("plan", "rotation", "--loop", "NOWHERE"), "NOT_FOUND", who="fleet"),),
    ),
    Cmd(
        "rtb",
        ("rtb", "--drone", "D-14", "--dock", "HUB-B"),
        who="fleet",
        check=_returning("D-14", "HUB-B"),
        rejects=(
            Rej(("rtb", "--drone", "D-12"), "DRONE_NOT_READY", who="fleet"),
            Rej(("rtb", "--drone", "D-99"), "NOT_FOUND", who="fleet"),
            Rej(("rtb", "--drone", "D-14", "--dock", "P9"), "DOCK_OFFLINE", who="fleet"),
            Rej(("rtb", "--drone", "D-14", "--dock", "P10"), "DOCK_FULL", who="fleet"),
        ),
        writes=True,
    ),
    Cmd(
        "propose out-of-service",
        ("propose", "out-of-service", "--drone", "D-11", "--why", "motor temp"),
        who="fleet",
        check=_has_pending("out_of_service", drone="D-11"),
        rejects=(
            Rej(
                ("propose", "out-of-service", "--drone", "D-99", "--why", "x"),
                "NOT_FOUND",
                who="fleet",
            ),
        ),
        writes=True,
    ),
    # ---- airspace
    Cmd(
        "route check",
        ("route", "check", "--drone", "D-04", "--to", "5.45,4.45"),
        who="airspace",
        check=_route_check,
        rejects=(
            Rej(
                ("route", "check", "--drone", "D-99", "--to", "5.45,4.45"),
                "NOT_FOUND",
                who="airspace",
            ),
        ),
    ),
    Cmd(
        "hold",
        ("hold", "--drone", "D-02", "--at-boundary", "RED-AIRFIELD"),
        who="airspace",
        check=_held("D-02"),
        rejects=(
            Rej(
                ("hold", "--drone", "D-12", "--at-boundary", "RED-AIRFIELD"),
                "DRONE_NOT_READY",
                who="airspace",
            ),
            Rej(("hold", "--drone", "D-02", "--at-boundary", "NOPE"), "NOT_FOUND", who="airspace"),
        ),
        writes=True,
    ),
    Cmd(
        "corridor",
        ("corridor", "--drone", "D-04", "--incident", "INC-2", "--to", "5.45,4.45"),
        who="airspace",
        check=_launched("D-04", "corridor"),
        rejects=(
            Rej(
                ("corridor", "--drone", "D-04", "--incident", "INC-2", "--to", "5.8,3.4"),
                "NO_FLY_ZONE",
                who="airspace",
            ),
            Rej(
                ("corridor", "--drone", "D-04", "--incident", "INC-1", "--to", "5.45,4.45"),
                "MISSION_EXPIRED",
                when="18:10",
                setup=lambda c: _close_inc1(c),
                who="airspace",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "propose zone",
        ("propose", "zone", "--id", "VIP-TEMP", "--until", "21:00"),
        who="airspace",
        check=_has_pending("zone", zone="VIP-TEMP", until="21:00"),
        rejects=(
            Rej(
                ("propose", "zone", "--id", "NOPE", "--until", "21:00"), "NOT_FOUND", who="airspace"
            ),
        ),
        writes=True,
    ),
    # ---- intel
    Cmd(
        "feed",
        ("feed", "--incident", "INC-1"),
        who="intel",
        check=_feed_check,
        rejects=(Rej(("feed", "--incident", "INC-99"), "NOT_FOUND", who="intel"),),
    ),
    Cmd(
        "evidence tag",
        ("evidence", "tag", "--incident", "INC-1", "--clip", "C-3", "--what", "lane 2 blocked"),
        who="intel",
        check=_tagged,
        rejects=(
            Rej(
                ("evidence", "tag", "--incident", "INC-99", "--clip", "C-3", "--what", "x"),
                "NOT_FOUND",
                who="intel",
            ),
        ),
        writes=True,
    ),
    # ---- relay
    Cmd(
        "track status",
        ("track", "status", "--incident", "INC-2"),
        who="relay",
        check=_track_check,
        rejects=(Rej(("track", "status", "--incident", "INC-99"), "NOT_FOUND", who="relay"),),
    ),
    Cmd(
        "relay plan",
        ("relay", "plan", "--incident", "INC-2"),
        who="relay",
        check=_relay_check,
        rejects=(Rej(("relay", "plan", "--incident", "INC-99"), "NOT_FOUND", who="relay"),),
    ),
    # ---- privacy
    Cmd(
        "propose footage-release",
        (
            "propose",
            "footage-release",
            "--incident",
            "INC-1",
            "--clip",
            "C-3",
            "--to",
            "traffic court",
        ),
        who="privacy",
        check=_has_pending("footage_release", incident="INC-1", clip="C-3", to="traffic court"),
        rejects=(
            Rej(
                (
                    "propose",
                    "footage-release",
                    "--incident",
                    "INC-99",
                    "--clip",
                    "C-3",
                    "--to",
                    "x",
                ),
                "NOT_FOUND",
                who="privacy",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "privacy check",
        ("privacy", "check", "--request", "recording inside the INC-1 geofence"),
        who="privacy",
        check=_lawful,
        rejects=(
            Rej(
                ("privacy", "check", "--request", "run face recognition on the crowd"),
                "PRIVACY_POLICY",
                who="privacy",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "set-recording",
        ("set-recording", "--drone", "D-09", "--on"),
        who="privacy",
        when="17:20",
        check=lambda p, c: assert_recording(p, True),
        rejects=(
            Rej(
                ("set-recording", "--drone", "D-02", "--on"),
                "PRIVACY_POLICY",
                "17:20",
                None,
                "privacy",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "set-recording --off",
        ("set-recording", "--drone", "D-09", "--off"),
        who=KAZUM_WC,
        when="17:20",
        check=lambda p, c: assert_recording(p, False),
        no_reject="rejections covered by the --on row",
        writes=True,
    ),
    Cmd(
        "release-footage",
        ("release-footage", "--approval", "{aid}"),
        who="privacy",
        when="17:20",
        setup=_footage_approved,
        check=lambda p, c: assert_released(p),
        rejects=(
            Rej(
                ("release-footage", "--approval", "{aid}"),
                "AUTH_REQUIRED",
                "17:20",
                _footage_pending,
                "privacy",
            ),
        ),
        writes=True,
    ),
    # ---- comms
    Cmd(
        "unit task",
        ("unit", "task", "--unit", "patrol_car_2", "--incident", "INC-1"),
        who="comms",
        when="17:12:30",
        check=_unit_tasked,
        rejects=(
            Rej(
                ("unit", "task", "--unit", "patrol_car_99", "--incident", "INC-1"),
                "NOT_FOUND",
                who="comms",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "unit release",
        (*UNIT_RELEASE, "--evidence", "D-09 feed: 2 minor, walking"),
        who="comms",
        when="17:20",
        check=_unit_released,
        rejects=(
            # The §4.1 example text quotes no revealed fact of INC-1, so the sim refuses it.
            Rej(
                (*UNIT_RELEASE, "--evidence", "D-09 feed: 2 cars, minor injuries"),
                "AUTH_REQUIRED",
                when="17:20",
                who="comms",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "advisory",
        (
            "advisory",
            "--area",
            "J9",
            "--text",
            "Ring Road eastbound lane 2 closed",
            "--until",
            "18:00",
        ),
        who="comms",
        check=_advisory,
        rejects=(
            _bad_token("advisory", "--area", "J9", "--text", "x", "--until", "18:00", who="comms"),
        ),
        writes=True,
    ),
    # ---- any agent
    Cmd(
        "log-refusal",
        (
            "log-refusal",
            "--about",
            "face recognition on the crowd",
            "--code",
            "PRIVACY_POLICY",
            "--by",
            "kazum",
        ),
        who="privacy",
        check=_refusal_logged,
        rejects=(
            _bad_token(
                "log-refusal",
                "--about",
                "x",
                "--code",
                "PRIVACY_POLICY",
                "--by",
                "k",
                who="privacy",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "simulate",
        ("simulate", *LAUNCH, "--to", "0.75,2.2", "--why", "upstream queue"),
        who="dispatch",
        when="17:15",
        check=_simulated,
        rejects=(
            _bad_token("simulate", *LAUNCH, "--to", "0.75,2.2", "--why", "x", who="dispatch"),
        ),
    ),
    # ---- L41 / L42 additions
    Cmd(
        "launch --pilot",
        (*CORRIDOR_D01, "--pilot", "DP-2"),
        who="dispatch",
        when="18:04",
        check=_flown_by("D-01", "DP-2"),
        rejects=(
            Rej((*CORRIDOR_D01, "--pilot", "kazum"), "PILOT_SATURATED", who="dispatch"),
            Rej((*CORRIDOR_D01, "--pilot", "DP-9"), "NOT_FOUND", who="dispatch"),
        ),
        writes=True,
    ),
    Cmd(
        "launch --for-min",
        (*INC1_D01, "--for-min", "5"),
        who="dispatch",
        when="17:15",
        check=_launched("D-01", "incident"),
        rejects=(Rej((*INC1_D01, "--for-min", "60"), "BATTERY_RESERVE", who="dispatch"),),
        writes=True,
    ),
    Cmd(
        "divert",
        ("divert", "--drone", "D-13", "--incident", "INC-2", "--mission", "track"),
        who="dispatch",
        when="17:40:30",
        setup=_track_authorised,
        check=_tracking("D-13"),
        rejects=(
            Rej(
                ("divert", "--drone", "D-13", "--incident", "INC-2", "--mission", "track"),
                "AUTH_REQUIRED",
                setup=_nothing,
                who="dispatch",
            ),
            Rej(
                ("divert", "--drone", "D-99", "--incident", "INC-2", "--mission", "track"),
                "NOT_FOUND",
                who="dispatch",
            ),
            Rej(
                ("divert", "--drone", "D-12", "--incident", "INC-2", "--mission", "track"),
                "DRONE_NOT_READY",
                who="dispatch",
            ),
            _bad_token(
                "divert",
                "--drone",
                "D-13",
                "--incident",
                "INC-2",
                "--mission",
                "track",
                who="dispatch",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "track assign",
        ("track", "assign", "--drone", "D-13", "--incident", "INC-2"),
        who="dispatch",
        when="17:40:30",
        setup=_track_authorised,
        check=_tracking("D-13"),
        rejects=(
            Rej(
                ("track", "assign", "--drone", "D-13", "--incident", "INC-2"),
                "AUTH_REQUIRED",
                setup=_nothing,
                who="dispatch",
            ),
            Rej(
                ("track", "assign", "--drone", "D-99", "--incident", "INC-2"),
                "NOT_FOUND",
                who="dispatch",
            ),
            Rej(
                ("track", "assign", "--drone", "D-13", "--incident", "INC-2"),
                "MISSION_EXPIRED",
                when="18:10",
                setup=_nothing,
                who="dispatch",
            ),
        ),
        writes=True,
    ),
    Cmd(
        "propose standing-policy",
        ("propose", "standing-policy", "--scope", "auto-launch accident/fire/flood this shift"),
        who="dispatch",
        check=_has_pending("standing_policy", scope="auto-launch accident/fire/flood this shift"),
        rejects=(
            Rej(
                ("propose", "standing-policy", "--scope", "x"),
                "FORBIDDEN_ROLE",
                who="fleet",
            ),
            _bad_token("propose", "standing-policy", "--scope", "x", who="dispatch"),
        ),
        writes=True,
    ),
    # ---- human-only
    Cmd(
        "approve",
        ("approve", "{aid}"),
        who=KAZUM_WC,
        setup=_track_proposal,
        check=_approved(),
        rejects=(
            Rej(("approve", "{aid}"), "FORBIDDEN_ROLE", setup=_track_proposal, who="dispatch"),
            Rej(("approve", "A-9999"), "NOT_FOUND", who=KAZUM_WC),
        ),
        writes=True,
    ),
    Cmd(
        "reject",
        ("reject", "{aid}", "not needed"),
        who=KAZUM_WC,
        setup=_ooo_proposal,
        check=lambda p, c: None,
        rejects=(
            Rej(("reject", "{aid}", "no"), "FORBIDDEN_ROLE", setup=_ooo_proposal, who="fleet"),
            Rej(("reject", "A-9999", "no"), "NOT_FOUND", who=KAZUM_WC),
        ),
        writes=True,
    ),
    Cmd(
        "take-control",
        ("take-control", "D-14"),
        who=KAZUM_RP,
        check=_ctrl(True),
        rejects=(
            Rej(("take-control", "D-14"), "FORBIDDEN_ROLE", who="dispatch"),
            Rej(("take-control", "D-12"), "DRONE_NOT_READY", who=KAZUM_RP),
            # kazum is at 4/4 here; §4.1 launch has no --pilot, so this is the CLI path to the code.
            Rej(("take-control", "D-02"), "PILOT_SATURATED", when="18:03:30", who=KAZUM_RP),
        ),
        writes=True,
    ),
    Cmd(
        "release-control",
        ("release-control", "D-14"),
        who=KAZUM_RP,
        setup=_take_d14,
        check=_ctrl(False),
        rejects=(
            Rej(("release-control", "D-14"), "FORBIDDEN_ROLE", who="dispatch"),
            Rej(("release-control", "D-99"), "NOT_FOUND", who=KAZUM_RP),
        ),
        writes=True,
    ),
)


def _close_inc1(c):
    from tests.sim.p3 import act, ok

    ok(act(c, "close_incident", {"incident": "INC-1", "note": "done"}))
