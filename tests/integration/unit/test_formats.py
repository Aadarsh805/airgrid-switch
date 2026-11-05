"""agents/harness/formats.py on hand-written samples and on the World Bot's own output. No LLM."""

from __future__ import annotations

import pytest

from agents.harness import formats as f
from agents.harness.run_agent import parse_cli_log
from worldbot import cards, lines

GOOD = {
    "INCIDENT": "🚨 INCIDENT | INC-2 robbery | market_street | P1 | report: scooter fleeing south",
    "LAUNCH": "🚀 LAUNCH | D-09 → INC-1 incident | ETA 1:24 | battery 100% | pilot kazum | "
    "basis: auto-policy",
    "EYES_ON": "👁 EYES-ON | INC-1 | D-09 on station 17:13:54 (114 s after alert)",
    "FLEET": "🔋 FLEET | airborne 6 · ready 5 · charging 3 · slots free 8/18 | "
    "tightest: D-10 52.4%",
    "ROTATION": "🔄 ROTATION | D-03 takes NORTH from D-02 (48%) | relief on station before RTB | "
    "coverage continuous",
    "SLOT": "🅿 SLOT | D-09 → HUB-B | DOCK_OFFLINE at P9, DOCK_FULL at P10 | lands ~17:37 at ~62%",
    "ROUTE": "🧭 ROUTE | D-06 | P6 → 2.25,5.0 | zones: NR-HOSPITAL transit, recording OFF | "
    "ETA 1:12",
    "ROUTE_DENIED": "⛔ ROUTE DENIED | D-12 | crosses RED-AIRFIELD | reason: NO_FLY_ZONE | "
    "holding at boundary | alternative: corridor north exit 5.45,4.45",
    "WEATHER": "🌬 WEATHER | NW, NE wind 13 m/s (limit 12) | grounded until 18:50 | "
    "reason: WEATHER_GROUNDED | coverage gap: NORTH",
    "SITREP": "📋 SITREP | INC-1 | 3 vehicles, 2 minor, walking | confidence: direct",
    "EVIDENCE": "🎥 EVIDENCE | INC-1 | clip C-3 | lane 2 blocked | inside geofence ✓ | "
    "persons identified: none (capability absent)",
    "TRACK": "🎯 TRACK | INC-2 | target: red scooter | held by D-13 (60%) | "
    "auth A-0003 @kazum, expires 18:11",
    "HANDOFF": "🤝 HANDOFF | D-13 → D-12 | reason: D-13 at 43% | gap 0 s",
    "AUTH": "🔐 AUTH | track approved by @kazum | scope: suspect vehicle only | expires 18:11 | "
    "logged",
    "PRIVACY_REFUSAL": "⛔ PRIVACY REFUSAL | request: face recognition | by: kazum (FO) | "
    "reason: PRIVACY_POLICY | lawful alternative: keep the scooter in view",
    "FOOTAGE": "📼 FOOTAGE | INC-1 clip C-3 | release to traffic court | status: pending A-0005",
    "UNIT": "🚓 UNIT | patrol_car_2 → INC-1 | ETA 6:30 | en route",
    "RELEASED": "✅ RELEASED | patrol_car_2 from INC-1 | evidence: 2 minor | saves ~12 min",
    "ADVISORY": "📣 ADVISORY | J9 | Ring Road eastbound lane 2 closed | until 18:00",
    "SHIFT_REPORT": "📊 SHIFT REPORT | flights 27 · eyes-on 114 s · units released 1 · "
    "coverage 93.9% · reserve breaches 0 · red-zone 0 · refusals 5",
    "REJECTED": "❌ REJECTED | action: launch D-03 | reason: PILOT_SATURATED | "
    "instead: D-03 goes to DP-2",
}
BAD = {
    "INCIDENT": "🚨 INCIDENT | INC-2 robbery | market_street | P7 | report: scooter",
    "LAUNCH": "🚀 LAUNCH | D-09 → INC-1 incident | ETA soon | battery 100% | pilot kazum | "
    "basis: auto-policy",
    "EYES_ON": "👁 EYES-ON | INC-1 | D-09 on station 17:13:54",
    "FLEET": "🔋 FLEET | airborne 6 · ready 5 | tightest: D-10 52.4%",
    "ROTATION": "🔄 ROTATION | D-03 takes NORTH from D-02 | relief on station before RTB | "
    "coverage continuous",
    "SLOT": "🅿 SLOT | D-09 → HUB-B | circling | lands ~17:37 at ~62%",
    "ROUTE": "🧭 ROUTE | D-06 | P6 → P2 | zones: hospital | ETA 1:12",
    "ROUTE_DENIED": "⛔ ROUTE DENIED | D-12 | crosses RED-AIRFIELD | reason: WEATHER_GROUNDED | "
    "holding at boundary | alternative: x",
    "WEATHER": "🌬 WEATHER | NW a bit windy | grounded until 18:50 | reason: WEATHER_GROUNDED | "
    "coverage gap: NORTH",
    "SITREP": "📋 SITREP | INC-1 | 3 vehicles | confidence: high",
    "EVIDENCE": "🎥 EVIDENCE | INC-1 | clip C-3 | lane | inside geofence ✓ | persons identified: 2",
    "TRACK": "🎯 TRACK | INC-2 | target: red scooter | held by D-13 (60%)",
    "HANDOFF": "🤝 HANDOFF | D-13 → D-12 | reason: D-13 at 43% | gap 12 s",
    "AUTH": "🔐 AUTH | track approved | scope: x | expires 18:11 | logged",
    "PRIVACY_REFUSAL": "⛔ PRIVACY REFUSAL | request: x | by: y | reason: AUTH_REQUIRED | "
    "lawful alternative: z",
    "FOOTAGE": "📼 FOOTAGE | INC-1 clip C-3 | release to court | status: maybe",
    "UNIT": "🚓 UNIT | patrol_car_2 → INC-1 | ETA 6:30 | lost",
    "RELEASED": "✅ RELEASED | patrol_car_2 from INC-1 | saves ~12 min",
    "ADVISORY": "📣 ADVISORY | J9 | closed",
    "SHIFT_REPORT": "📊 SHIFT REPORT | flights 27 · eyes-on 114 s · units released 1 · "
    "coverage 93.9% · reserve breaches 2 · red-zone 0 · refusals 5",
    "REJECTED": "❌ REJECTED | action: launch | reason: TOO_TIRED | instead: wait",
}
AGENT_FORMATS = [n for n in f.FORMATS if not n.startswith("BOT_")]


def test_every_agent_format_has_samples():
    assert set(GOOD) == set(AGENT_FORMATS) == set(BAD)


@pytest.mark.parametrize("name", AGENT_FORMATS)
def test_good_sample_matches_only_its_format(name):
    assert f.matches(name, GOOD[name]), GOOD[name]
    assert f.which(GOOD[name]) == name


@pytest.mark.parametrize("name", AGENT_FORMATS)
def test_bad_sample_is_refused(name):
    assert not f.matches(name, BAD[name]), BAD[name]


def test_emoji_variation_selector_accepted():
    assert f.matches("SLOT", GOOD["SLOT"].replace("🅿", "🅿️"))


def test_world_bot_output_matches():
    event = {"type": "launch", "text": "🚀 D-09 → incident INC-1"}
    assert f.matches("BOT_EVENT", cards.event_alert(event, "#dispatch", "dispatch"))
    approval = {"id": "A-0007", "type": "track", "created_by": "dispatch", "summary": "track INC-2"}
    assert f.matches("BOT_APPROVAL", cards.approval_card(approval, "watch_commander"))
    assert f.matches("BOT_APPROVED", cards.approved_line("A-0007", "kazum", "track approved"))
    assert f.matches("BOT_REJECTED", cards.rejected_line("A-0007", "kazum", "scope too wide"))
    assert f.matches("REJECTED", cards.refusal_line("launch D-03", "PILOT_SATURATED", "DP-2"))
    assert f.matches("BOT_PILOT", lines.duty_pilot("DP-1", "Asha Menon", "D-03 is mine."))
    assert f.matches("BOT_RADIO", lines.radio("patrol_car_2", "Copy."))


def test_lines_matching_tolerates_a_handoff_line():
    reply = GOOD["LAUNCH"] + "\n@relay INC-2 track approved A-0003, D-13 on it at 60%."
    assert f.lines_matching("LAUNCH", reply) == [GOOD["LAUNCH"]]
    assert f.lines_matching("ROUTE", reply) == []


def test_every_prefixed_line_must_match():
    good = GOOD["LAUNCH"]
    assert f.malformed_lines(good + "\n@relay over to you") == []
    bad = good.replace("ETA 1:24", "ETA unknown")
    assert f.malformed_lines(GOOD["INCIDENT"] + "\n" + bad) == [bad]
    target = GOOD["TRACK"].replace("target: red scooter", "target: unknown (no fact)")
    assert f.malformed_lines(target) == [target]


def test_spelled_out_numbers_need_a_source():
    assert f.unproven_numbers("three vehicles", ["vehicles: 3"]) == []
    assert f.unproven_numbers("Three vehicles", ["2 only"]) == ["Three"]
    assert f.unproven_numbers("one drone", ["x"]) == []


def test_quiet():
    assert f.is_quiet(" NO_REPLY\n")
    assert not f.is_quiet("NO_REPLY, nothing to add")


def test_codes():
    assert f.codes_in("reason: NO_FLY_ZONE and DOCK_FULL") == ["NO_FLY_ZONE", "DOCK_FULL"]
    assert f.codes_in("no_fly_zone") == []
    assert f.unknown_codes("reason: TOO_TIRED_NOW, BATTERY_RESERVE") == ["TOO_TIRED_NOW"]
    assert f.unknown_codes("NO_REPLY_X") == []


# ---------------------------------------------------------------- provenance
CLI = (
    '{"drone":"D-13","battery":60.0,"eta":"1:24","clock":"17:42:00","lands_pct":62.4,"id":"A-0003"}'
)


def test_provenance_passes_on_sourced_numbers():
    reply = "🚀 LAUNCH | D-13 → INC-2 track | ETA 1:24 | battery 60% | pilot DP-2 | 17:42"
    assert f.unproven_numbers(reply, [CLI, "kazum: INC-2 DP-2"]) == []


def test_provenance_catches_invented_rounded_and_summed():
    assert f.unproven_numbers("battery 61%", [CLI]) == ["61"]
    assert f.unproven_numbers("lands at 62%", [CLI]) == ["62"]  # 62.4 rounded: not what it said
    assert f.unproven_numbers("ETA 1:25", [CLI]) == ["1:25"]
    assert f.unproven_numbers("D-14 up, D-13 up", [CLI]) == ["D-14"]
    assert f.unproven_numbers("INC-20", ["INC-2"]) == ["INC-20"]
    assert f.unproven_numbers("A-0004", [CLI]) == ["A-0004"]
    assert f.unproven_numbers("dock P9", [CLI]) == ["9"]


def test_provenance_ignores_frame_literals_and_rooms():
    w = "🌬 WEATHER | NW wind 13 m/s (limit 12) | #incident-2"
    assert f.unproven_numbers(w, ["13"]) == []
    fleet = "slots free 8/18 | gap 0 s | reserve breaches 0 · red-zone 0"
    assert f.unproven_numbers(fleet, ["8"]) == []
    assert f.unproven_numbers("slots free 9/18", ["8"]) == ["9"]


# ---------------------------------------------------------------- CLI roles
@pytest.mark.parametrize(
    "argv,expected",
    [
        (["propose", "track", "--incident", "INC-2"], "propose track"),
        (["track", "status", "--incident", "INC-2"], "track status"),
        (["launch", "--drone", "D-01"], "launch"),
        (["status"], "status"),
        (["--help"], ""),
        ([], ""),
    ],
)
def test_command_of(argv, expected):
    assert f.command_of(argv) == expected


def test_role_limits():
    assert not f.forbidden_calls("dispatch", [["launch", "--drone", "D-01"], ["fleet"]])
    assert f.forbidden_calls("fleet", [["launch", "--drone", "D-01"]])
    assert f.forbidden_calls("relay", [["incident", "close", "--id", "INC-2"]])
    assert f.forbidden_calls("intel", [["unit", "release", "--unit", "x"]])
    for agent in f.WRITES:
        for human in f.HUMAN_ONLY:
            assert f.forbidden_calls(agent, [[human, "A-0001"]])


def test_parse_cli_log():
    log = (
        ">>> airgrid status\n"
        '{"ok":true}\n'
        "<<< exit 0\n"
        ">>> airgrid launch --why two\\ words --drone D-1\n"
        '{"ok":false,"code":"NOT_FOUND","reason":"r","hint":""}\n'
        "<<< exit 2\n"
        ">>> airgrid fleet\n"
    )
    a, b, c = parse_cli_log(log)
    assert (a.argv, a.exit_code, a.code) == (["status"], 0, None)
    assert b.argv == ["launch", "--why", "two words", "--drone", "D-1"]
    assert (b.command, b.exit_code, b.code) == ("launch", 2, "NOT_FOUND")
    assert (c.command, c.exit_code) == ("fleet", -1)


def test_simulated_rejection_has_its_code():
    log = (
        ">>> airgrid simulate track assign --drone D-13\n"
        '{"ok":true,"result":{"would":{"ok":false,"code":"AUTH_REQUIRED"}}}\n'
        "<<< exit 0\n"
    )
    assert parse_cli_log(log)[0].code == "AUTH_REQUIRED"


def test_tilde_allows_rounding_a_real_number_only():
    out = ['{"lands_at":"17:37:06","lands_pct":62.2}']
    assert f.unproven_numbers("lands ~17:37 at ~62%", out) == []
    assert f.unproven_numbers("lands ~17:37 at ~63%", out) == ["63"]
    assert f.unproven_numbers("lands ~17:37 at 62%", out) == ["62"]
