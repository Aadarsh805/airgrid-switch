"""Layer 2 behaviours, asserted on the golden run's public outputs (lines + frames) only."""

import re

import pytest

from tests.sim.helpers import (
    EPS,
    dist,
    event_lines,
    find,
    frame_at_or_after,
    frame_at_or_before,
    in_rect,
    load_yaml,
    north,
    tmin,
)

WORLD = load_yaml("world/airgrid.yaml")
MODEL = WORLD["drone_model"]
RED = next(z for z in WORLD["zones"] if z["id"] == "RED-AIRFIELD")["rect"]
SHIFT_END = tmin("20:00")


@pytest.fixture(scope="module")
def run():
    from sim import golden

    return golden.run(name="FRIDAY-RUSH")


@pytest.fixture(scope="module")
def lines(run):
    out = event_lines(run.lines)
    assert out, "golden run produced no timeline lines"
    return out


@pytest.fixture(scope="module")
def frames(run):
    fr = sorted(run.frames, key=lambda f: f["t"])
    assert len(fr) > 300, f"only {len(fr)} frames"
    assert abs(fr[0]["t"] - tmin("17:00")) <= 0.5 and abs(fr[-1]["t"] - SHIFT_END) <= 0.5
    return fr


def _airborne(frame):
    return {k: v for k, v in frame["drones"].items() if v["state"] == "airborne"}


# ---------- patrol rotation ----------

RELIEF = r"^🔄 (D-\d\d) launches for (NORTH|CENTRAL|SOUTH) \(relief for (D-\d\d) at (\d+)%\)"


def test_relief_launches_at_or_below_48(lines):
    reliefs = find(lines, RELIEF)
    assert len(reliefs) == 15  # golden timeline count
    for _t, msg, m in reliefs:
        assert int(m.group(4)) <= MODEL["relief_trigger_pct"], msg


def test_relief_on_station_before_old_rtb(lines, frames):
    reliefs = find(lines, RELIEF)
    checked = 0
    for t, msg, m in reliefs:
        relief, loop, old = m.group(1), m.group(2), m.group(3)
        rtbs = [
            (tr, mr)
            for tr, mr, _ in find(lines, rf"^↩ {old} RTB relieved by {relief} on {loop} ")
            if tr >= t - EPS
        ]
        if not rtbs:
            assert t > SHIFT_END - 3, f"relief never completed: {msg}"
            continue
        t_rtb = rtbs[0][0]
        # the line order: launch, then RTB
        assert lines.index((t, msg)) < lines.index(rtbs[0])
        # the tired drone stays on the loop until the RTB moment (relief airborne before it leaves)
        for f in frames:
            if t + EPS < f["t"] < t_rtb - EPS:
                d = f["drones"][old]
                assert (d["state"], d["mission"], d["loop"]) == ("airborne", "patrol", loop), (
                    f"{f['clock']}: {old} left {loop} before relief {relief} arrived"
                )
                r = f["drones"][relief]
                assert r["state"] == "airborne" and r["loop"] == loop, f"{f['clock']} {relief}"
        # at the RTB, the relief is on station at the loop centre
        f = frame_at_or_after(frames, t_rtb)
        r = f["drones"][relief]
        centre = WORLD["patrol_loops"][loop]["center"]
        assert r["mission"] == "patrol" and r["loop"] == loop, f"{f['clock']} {relief} {r}"
        assert dist(r["pos"], centre) <= 0.05, f"{f['clock']} {relief} not on station: {r['pos']}"
        checked += 1
    assert checked == 13  # the two 19:59 reliefs complete after 20:00


def test_backfill_same_tick_as_d13_diversion(lines):
    div = find(lines, r"^🎯 D-13 diverted from CENTRAL patrol to TRACK INC-2 at (\d+)%")
    assert len(div) == 1
    t_div = div[0][0]
    assert int(div[0][2].group(1)) == 60
    backfill = find(lines, r"^🔄 (D-\d\d) launches for CENTRAL \(backfill — loop uncovered\)")
    same = [b for b in backfill if abs(b[0] - t_div) <= EPS]
    assert same, f"no CENTRAL backfill at {t_div} (backfills: {[b[0] for b in backfill]})"


# ---------- track relay ----------


def test_relay_launch_at_or_below_45_and_gap_0(lines, frames):
    relay = find(lines, r"^🤝 relay planned: (D-\d\d) launches to relieve (D-\d\d) \((\d+)%\)")
    assert len(relay) == 1
    t_relay, _, m = relay[0]
    new, old = m.group(1), m.group(2)
    assert (new, old) == ("D-12", "D-13")
    assert int(m.group(3)) <= MODEL["track_relief_trigger_pct"]
    hand = find(lines, rf"^🤝 HANDOFF {old} → {new} \(gap (\d+) s\); {old} at (\d+)%")
    assert len(hand) == 1
    t_hand, _, hm = hand[0]
    assert hm.group(1) == "0"
    assert t_hand > t_relay
    # relief airborne on relay before the handoff, old tracker still tracking
    for f in frames:
        if t_relay + 0.5 <= f["t"] < t_hand - EPS:
            assert f["drones"][new]["state"] == "airborne", f["clock"]
            assert f["drones"][old]["mission"] == "track", f["clock"]
    after = frame_at_or_after(frames, t_hand)
    assert after["drones"][new]["mission"] == "track"
    assert after["drones"][old]["mission"] != "track"
    assert (
        after["drones"][old]["mission"] == "returning" or after["drones"][old]["state"] == "docked"
    )
    rtb = find(lines, rf"^↩ {old} RTB track handed off")
    assert rtb and abs(rtb[0][0] - t_hand) <= EPS


def test_track_never_dropped_before_hold(lines, frames):
    """From the diversion to the boundary hold, some drone is on track in every frame (gap 0)."""
    t_div = find(lines, r"^🎯 D-13 diverted")[0][0]
    t_hold = find(lines, r"^⛔ (D-\d\d) holds at RED-AIRFIELD boundary")[0][0]
    for f in frames:
        if t_div + EPS < f["t"] < t_hold - EPS:
            assert any(d["mission"] == "track" for d in f["drones"].values()), f["clock"]


# ---------- red zone ----------


def test_no_drone_inside_red_airfield_any_frame(frames):
    for f in frames:
        for did, d in f["drones"].items():
            assert not in_rect(d["pos"], RED), (
                f"{f['clock']} {did} inside RED-AIRFIELD at {d['pos']}"
            )


def test_tracker_holds_at_boundary(lines, frames):
    hold = find(lines, r"^⛔ (D-\d\d) holds at RED-AIRFIELD boundary")
    assert len(hold) == 1 and hold[0][2].group(1) == "D-12"
    t_hold = hold[0][0]
    for f in frames:
        if t_hold + 0.5 <= f["t"] <= tmin("18:07"):
            d = f["drones"]["D-12"]
            assert d["mission"] == "hold", f["clock"]
            assert d["pos"][0] < RED["x"][0] and RED["x"][0] - d["pos"][0] <= 0.1, f["clock"]


# ---------- weather ----------

W_START, W_END = tmin("18:20"), tmin("18:50")


def test_grounded_field_follows_weather(frames):
    for f in frames:
        want = ["NE", "NW"] if W_START - EPS <= f["t"] < W_END - EPS else []
        assert sorted(f["grounded"]) == want, f"{f['clock']} grounded={f['grounded']}"


def test_no_launch_in_north_during_grounding(lines, frames):
    for prev, cur in zip(frames, frames[1:], strict=False):
        if not (W_START - EPS <= cur["t"] < W_END - EPS):
            continue
        for did, d in cur["drones"].items():
            p = prev["drones"][did]
            if p["state"] == "docked" and d["state"] == "airborne":
                assert not north(p["pos"]), f"{cur['clock']} {did} launched from {p['slot']}"
    for t, msg in lines:
        if W_START - EPS <= t < W_END - EPS and msg.startswith(("🚀", "🔄")):
            assert "NORTH" not in msg, msg


def test_north_patrol_absent_while_grounded(frames):
    seen = 0
    for f in frames:
        if W_START - EPS <= f["t"] < W_END - EPS:
            seen += 1
            for did, d in _airborne(f).items():
                assert not (d["mission"] == "patrol" and d["loop"] == "NORTH"), (
                    f"{f['clock']} {did}"
                )
    assert seen >= 50


def test_north_reopens_with_backfill(lines):
    reopen = find(lines, r"^🌬 wind back under limit")
    assert reopen and abs(reopen[0][0] - W_END) <= EPS
    bf = find(lines, r"^🔄 D-\d\d launches for NORTH \(backfill — loop uncovered\)")
    assert any(abs(t - W_END) <= EPS for t, _, _ in bf)


# ---------- slot booking ----------

SKIP = r"^🅿 (D-\d\d) nearest dock (\S+) unavailable → (DOCK_OFFLINE|DOCK_FULL), rerouting"


def test_dock_rejections_precede_reroute_rtb(lines):
    skips = find(lines, SKIP)
    assert len(skips) == 11  # golden timeline count
    for t, msg, m in skips:
        did, dock = m.group(1), m.group(2)
        i = lines.index((t, msg))
        later = [
            (j, tt, mm)
            for j, (tt, mm) in enumerate(lines)
            if j > i and mm.startswith(f"↩ {did} RTB ")
        ]
        assert later, f"no RTB after {msg}"
        j, tt, mm = later[0]
        assert abs(tt - t) <= EPS, f"RTB not same tick: {msg} / {mm}"
        assert not mm.rstrip().split("→")[-1].strip().startswith(dock + " "), (
            f"rerouted to {dock}: {mm}"
        )


def test_d09_reroute_p9_offline_p10_full_hub_b(lines):
    at = [(t, m) for t, m in lines if abs(t - tmin("17:35")) <= EPS and "D-09" in m]
    msgs = [m for _, m in at]
    assert len(msgs) == 3, msgs
    assert msgs[0] == "🅿 D-09 nearest dock P9 unavailable → DOCK_OFFLINE, rerouting"
    assert msgs[1] == "🅿 D-09 nearest dock P10 unavailable → DOCK_FULL, rerouting"
    m = re.fullmatch(r"↩ D-09 RTB accident cleared → HUB-B \(battery (\d+)%\)", msgs[2])
    assert m and abs(int(m.group(1)) - 66) <= 1, msgs[2]


def test_dock_rejection_reasons_true_in_frames(lines, frames):
    slots = {k: v["slots"] for k, v in WORLD["docks"].items()}
    for t, msg, m in find(lines, SKIP):
        dock, code = m.group(2), m.group(3)
        f = frame_at_or_before(frames, t)
        d = f["docks"][dock]
        if code == "DOCK_OFFLINE":
            assert d["online"] is False, f"{msg}: frame {f['clock']} says online"
        else:
            assert d["online"] is True, msg
            assert len(d["occ"]) + len(d["res"]) >= slots[dock], f"{msg}: {f['clock']} {d}"


def test_slot_exclusivity_every_frame(frames):
    slots = {k: v["slots"] for k, v in WORLD["docks"].items()}
    for f in frames:
        seen = {}
        for k, d in f["docks"].items():
            assert len(d["occ"]) <= slots[k], f"{f['clock']} {k} occupants {d['occ']}"
            assert len(d["occ"]) + len(d["res"]) <= slots[k], f"{f['clock']} {k} {d}"
            if not d["online"]:
                assert d["res"] == [], f"{f['clock']} reservation on offline {k}"
            for did in d["occ"] + d["res"]:
                assert did not in seen, f"{f['clock']} {did} in {seen[did]} and {k}"
                seen[did] = k
        for did, d in f["drones"].items():
            if d["state"] == "docked":
                assert did in f["docks"][d["slot"]]["occ"], f"{f['clock']} {did} not in {d['slot']}"
            elif d["mission"] == "returning":
                assert did in seen, f"{f['clock']} {did} returning without a booked slot"


# ---------- pilots ----------


def test_at_most_four_per_pilot_every_frame(frames):
    caps = {k: v["max_drones"] for k, v in WORLD["pilots"].items()}
    for f in frames:
        load = {}
        for did, d in _airborne(f).items():
            assert d["pilot"] in caps, f"{f['clock']} {did} airborne without a named pilot"
            load[d["pilot"]] = load.get(d["pilot"], 0) + 1
        for p, n in load.items():
            assert n <= min(caps[p], 4), f"{f['clock']} {p} has {n} drones"
        assert len(_airborne(f)) <= 12, f["clock"]


def test_pilot_saturated_falls_back(lines, frames):
    rej = find(lines, r"^❌ REJECTED D-03 under kazum: PILOT_SATURATED .* → DP-2")
    assert len(rej) == 1
    f = frame_at_or_before(frames, rej[0][0])
    n = sum(1 for d in _airborne(f).values() if d["pilot"] == "kazum")
    assert n == 4, f"kazum had {n} drones at {f['clock']}"


# ---------- battery ----------


def test_every_landing_at_least_25(lines):
    lands = find(lines, r"^🛬 (D-\d\d) lands at (\S+) with (\d+)%")
    assert len(lands) == 22  # golden timeline count
    for _, msg, m in lands:
        assert int(m.group(3)) >= MODEL["reserve_pct"], msg


def test_airborne_battery_never_below_reserve(frames):
    for f in frames:
        for did, d in _airborne(f).items():
            assert d["bat"] >= MODEL["reserve_pct"], f"{f['clock']} {did} {d['bat']}%"


def test_docked_charge_rate_and_cap(frames):
    rate, cap = MODEL["charge_pct_per_min"], MODEL["charge_cap_pct"]
    checked = 0
    for a, b in zip(frames, frames[1:], strict=False):
        dt = b["t"] - a["t"]
        for did, db in b["drones"].items():
            da = a["drones"][did]
            if da["state"] == db["state"] == "docked" and da["slot"] == db["slot"]:
                want = min(cap, da["bat"] + rate * dt)
                assert abs(db["bat"] - want) <= 0.15, (
                    f"{a['clock']}→{b['clock']} {did}: {da['bat']} → {db['bat']} (want {want:.1f})"
                )
                assert db["bat"] <= cap
                if da["bat"] < cap - rate * dt:
                    checked += 1
    assert checked >= 100, "too few charging intervals observed"
