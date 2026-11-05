"""Golden run: applies scenario golden_actions headlessly and diffs against the checker timeline.

    python -m sim.golden FRIDAY-RUSH [--out runs/] [--seed S] [--expected docs/golden-timeline.txt]

Writes runs/golden-<YYYYmmdd-HHMMSS>.txt (checker format) + .jsonl (frames every 0.5 sim-min,
same shape as fleet_check.py --dump-jsonl; last line {"log": [...]}), prints a summary and
exits 1 on any diff or violation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sim.geo import fmt

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {"FRIDAY-RUSH": ROOT / "docs" / "golden-timeline.txt"}


# ---------------------------------------------------------------- golden actions (checker port)
def _launch_assertion(eng, t: float, a: dict) -> bool:
    """A golden `launch` with basis auto_policy that the policy already performed is an assertion
    (L29b). Returns True if handled as an assertion."""
    if a.get("basis") != "auto_policy" or not a.get("incident"):
        return False
    for rec in eng.policy_launches.get(a["incident"], []):
        if not rec["asserted"] and abs(rec["t"] - t) <= 0.1 + 1e-9:
            rec["asserted"] = True
            if rec["drone"] != a["drone"]:
                eng.violation(t, f"auto-launch sent {rec['drone']}, golden expects {a['drone']}")
            if a.get("pilot") and rec["pilot"] != a["pilot"]:
                eng.violation(t, f"auto-launch pilot {rec['pilot']}, golden expects {a['pilot']}")
            if a.get("mission", "incident") != "incident":
                eng.violation(t, f"auto-launch mission incident, golden expects {a['mission']}")
            return True
    return False


# Golden identities: agents by desk; humans from the solo example config (deterministic, L3).
GOLDEN_AGENT = {
    "launch": "dispatch",
    "divert": "dispatch",
    "reacquire": "dispatch",
    "close_incident": "dispatch",
    "release_unit": "comms",
    "task_unit": "comms",
    "rtb": "fleet",
    "rtb_incident": "fleet",
    "hold": "airspace",
}
APPROVE_TYPE = {"loudspeaker": "broadcast"}  # golden `what` -> approval type


def _human(name: str):
    from sim.auth import EXAMPLE_HUMANS, Identity, load_humans

    return Identity.human(name, load_humans(EXAMPLE_HUMANS).get(name, ()))


def _agent(role: str):
    from sim.auth import Identity

    return Identity.agent(role)


def _run(eng, t, ident, action, args):
    from sim.actions import execute

    out = execute(eng, ident, action, args)
    if not out.ok:
        r = out.rejection
        eng.violation(t, f"golden {action} {args} rejected: {r.code} {r.reason}")
    return out


def _refusal_call(eng, a: dict):
    """Map a scripted refusal onto the real request that the validators refuse (L31)."""
    code = a["code"]
    if code == "PRIVACY_POLICY":
        return _human("kazum"), "request_face_id", {"request": a["request"], "by": a.get("by")}
    if code == "NO_FLY_ZONE":
        d = eng.drones[eng.tracker_id]
        r = eng.airspace.red
        centre = [(r["x"][0] + r["x"][1]) / 2, (r["y"][0] + r["y"][1]) / 2]
        return (
            _agent("airspace"),
            "corridor_watch",
            {"drone": d.id, "incident": d.incident, "to": centre},
        )
    if code == "MISSION_EXPIRED":
        m = re.search(r"D-\d+", a["request"])
        closed = [k for k, v in eng.incidents.items() if v["status"] == "closed"]
        return (
            _agent("dispatch"),
            "assign_perimeter",
            {"drone": m.group(0) if m else None, "incident": closed[-1] if closed else None},
        )
    return None


def _quote_fact(eng, a: dict) -> str:
    """The full revealed fact value the scripted release note paraphrases ('' if none)."""
    from sim import incidents

    unit = eng.ground.units.get(a["unit"])
    words = set(re.findall(r"[a-z]{4,}", a["note"].lower()))
    for f in incidents.revealed(eng, unit.incident if unit else None):
        for v in f["fact"].values():
            if isinstance(v, str) and words & set(re.findall(r"[a-z]{4,}", v.lower())):
                return v
    return a["note"]


def apply_action(eng, t: float, a: dict) -> None:
    from sim.actions import decide, execute, propose

    act = a["act"]
    if act == "standing_policy":  # 17:00 brief: A-0001, no timeline line
        p = propose(
            eng,
            _agent("dispatch"),
            "standing_policy",
            {},
            "standing auto-launch policy for accident/fire/flood",
        )
        decide(eng, _human("kazum"), p.result["id"], True, "shift brief")
    elif act == "launch":
        if _launch_assertion(eng, t, a):
            return
        args = {k: a[k] for k in ("drone", "mission", "incident", "to", "pilot", "basis") if k in a}
        if a.get("note"):
            args["why"] = a["note"]
        if a.get("expect") == "PILOT_SATURATED":
            p = a["pilot"]
            out = execute(eng, _agent("dispatch"), "launch", args)
            if out.ok or out.rejection.code != "PILOT_SATURATED":
                eng.violation(t, f"expected PILOT_SATURATED for {p}, got {out.body()}")
                return
            eng.emit(
                t,
                "rejected",
                f"❌ REJECTED {a['drone']} under {p}: {out.rejection.code} "
                f"({sorted(eng.pilots[p].drones)}) → {a['fallback_pilot']}",
                {
                    "drone": a["drone"],
                    "pilot": p,
                    "code": out.rejection.code,
                    "fallback_pilot": a["fallback_pilot"],
                },
            )
            args["pilot"] = a["fallback_pilot"]
        _run(eng, t, _agent("dispatch"), "launch", args)
    elif act == "divert":
        _run(
            eng,
            t,
            _agent("dispatch"),
            "divert",
            {
                "drone": a["drone"],
                "incident": a["incident"],
                "mission": a["mission"],
                "pilot": a["pilot"],
                "why": a.get("note"),
            },
        )
    elif act == "approve":
        type_ = APPROVE_TYPE.get(a["what"], a["what"])
        args = {k: a[k] for k in ("drone", "incident", "scope") if k in a}
        if type_ == "track":
            from sim.approvals import track_minutes

            args["minutes"] = track_minutes(args)
        p = propose(eng, _agent("dispatch"), type_, args, f"{a['what']} ({a.get('scope', '')})")
        if not p.ok:
            eng.violation(t, f"golden propose {type_} rejected: {p.body()}")
            return
        d = decide(eng, _human(a["by"]), p.result["id"], True)
        if not d.ok:
            eng.violation(t, f"golden approve {type_} rejected: {d.body()}")
    elif act == "release_unit":
        args = {"unit": a["unit"], "evidence": _quote_fact(eng, a), "note": a["note"]}
        _run(eng, t, _agent("comms"), "release_unit", args)
    elif act == "task_unit":  # units_golden: Comms tasking (no timeline line)
        args = {"unit": a["unit"], "incident": a["incident"]}
        if a.get("note"):
            args["why"] = a["note"]
        _run(eng, t, _agent("comms"), "task_unit", args)
    elif act == "rtb":
        _run(
            eng,
            t,
            _agent("fleet"),
            "rtb",
            {"drone": a["drone"], "why": a.get("note", "incident task done")},
        )
    elif act == "refuse":
        call = _refusal_call(eng, a)
        if call is None:
            eng.violation(t, f"no validator mapping for scripted refusal {a['code']}")
            return
        out = execute(eng, *call)
        if out.ok or out.rejection.code != a["code"]:
            eng.violation(t, f"expected {a['code']} from {call[1]}, got {out.body()}")
            return
        eng.emit(
            t,
            "refusal",
            f"⛔ {out.rejection.code}: '{a['request']}'",
            {"code": out.rejection.code, "request": a["request"], "by": a.get("by")},
        )
    elif act == "hold":
        drone = eng.tracker_id if a["drone"] == "TRACKER" else a["drone"]
        _run(
            eng,
            t,
            _agent("airspace"),
            "hold",
            {"drone": drone, "at_boundary": a.get("at_boundary") or eng.airspace.red_id},
        )
    elif act == "reacquire":
        d = eng.drones[a["drone"]]
        inc = d.incident or (eng.drones[eng.tracker_id].incident if eng.tracker_id else None)
        _run(eng, t, _agent("dispatch"), "assign_track", {"drone": d.id, "incident": inc})
    elif act == "take_control":
        _run(eng, t, _human(a["pilot"]), "take_control", {"drone": a["drone"]})
    elif act == "close_incident":
        _run(
            eng,
            t,
            _agent("dispatch"),
            "close_incident",
            {"incident": a["incident"], "note": a["note"]},
        )
    elif act == "rtb_incident":
        for d in list(eng.drones.values()):
            if d.state == "airborne" and d.incident == a["incident"] and d.mission != "returning":
                _run(
                    eng,
                    t,
                    _agent("fleet"),
                    "rtb",
                    {"drone": d.id, "why": f"{a['incident']} closed"},
                )
        eng.tracker_id = None
    else:
        eng.violation(t, f"unknown golden action {act}")


# ---------------------------------------------------------------- run
@dataclass
class GoldenRun:
    name: str
    seed: str | None
    lines: list[str]
    kpi: dict
    violations: list[str]
    frames: list[dict]
    event_hash: str
    scorecard: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    title: str = ""
    compare: list[dict] = field(default_factory=list)
    baseline: dict = field(default_factory=dict)
    fleet_messages: int = 0

    def text(self) -> str:
        head = f"=== {self.title.upper()} — golden managed run (sim) ==="
        out = [head, *self.lines, "", SCORECARD_HEAD, *self.scorecard, ""]
        if self.violations:
            out.append("VIOLATIONS:")
            out += [f"   {v}" for v in self.violations]
        else:
            out.append("OK — 0 violations")
        return "\n".join(out) + "\n"

    def jsonl(self) -> str:
        rows = [json.dumps(f, ensure_ascii=False) for f in self.frames]
        log = [[round(e["t"], 2), e["text"]] for e in self.events if e["timeline"]]
        rows.append(json.dumps({"log": log}, ensure_ascii=False))
        return "\n".join(rows) + "\n"


SCORECARD_HEAD = "=== Scorecard (managed vs baseline) ==="


def fleet_messages(eng) -> int:
    """Fleet narration from events: one 🔄 per rotation + one 🔋 per status period (T0 included)."""
    rotations = sum(1 for e in eng.log.items if e.type == "rotation")
    every = eng.world.policies["fleet_status_every_min"]
    return rotations + int((eng.T1 - eng.T0) // every) + 1


def run(name: str = "FRIDAY-RUSH", seed: str | None = None) -> GoldenRun:
    from sim import baseline
    from sim.engine import Engine
    from sim.kpi import compare

    eng = Engine.load(name, seed=seed, mode="headless", golden=True, record_frames=True)
    eng.run()
    base = baseline.run(eng.world, eng.scenario)
    eng.baseline_result = base
    return GoldenRun(
        compare=compare(eng, base),
        baseline=base.summary(),
        fleet_messages=fleet_messages(eng),
        name=eng.name,
        seed=eng.seed,
        lines=eng.log.timeline_lines(),
        kpi=eng.kpi.summary(eng),
        violations=list(eng.violations),
        frames=eng.frames,
        event_hash=eng.log.hash(),
        scorecard=eng.kpi.scorecard_lines(eng, base),
        events=eng.events,
        title=eng.title,
    )


# ---------------------------------------------------------------- timeline parse / diff
_LINE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?  (.*)$")
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)%|(\d{1,2}:\d{2}(?::\d{2})?)")


def _hms(h, m, s) -> float:
    return int(h) * 60 + int(m) + (int(s) / 60 if s else 0.0)


def parse_timeline(text: str) -> list[tuple[float, str]]:
    """Event section of a checker-format timeline -> [(minutes, msg)]; stops at '=== Scorecard'."""
    out = []
    for raw in text.splitlines():
        if raw.startswith("=== Scorecard"):
            break
        m = _LINE.match(raw.rstrip("\r\n"))
        if m:
            out.append((_hms(*m.groups()[:3]), m.group(4).rstrip()))
    return out


def _skeleton(msg: str):
    nums = []

    def sub(m):
        if m.group(1) is not None:
            nums.append(("pct", float(m.group(1))))
            return "\x00%"
        h, mm, *s = m.group(2).split(":")
        nums.append(("time", _hms(h, mm, s[0] if s else None)))
        return "\x00T"

    return _TOKEN.sub(sub, msg), nums


def diff(
    actual_lines: list[str], expected_text: str, t_tol: float = 0.1, bat_tol: float = 1.0
) -> list[str]:
    """[] means match: same messages in order; times within t_tol min, '<n>%' within bat_tol."""
    act = parse_timeline("\n".join(actual_lines))
    exp = parse_timeline(expected_text)
    out = []
    for i in range(max(len(act), len(exp))):
        if i >= len(act):
            out.append(f"line {i + 1}: missing   {fmt(exp[i][0]):>8}  {exp[i][1]}")
            continue
        if i >= len(exp):
            out.append(f"line {i + 1}: extra     {fmt(act[i][0]):>8}  {act[i][1]}")
            continue
        (ta, ma), (te, me) = act[i], exp[i]
        problems = []
        if abs(ta - te) > t_tol + 1e-9:
            problems.append(f"time {fmt(ta)} vs {fmt(te)}")
        ska, na = _skeleton(ma)
        ske, ne = _skeleton(me)
        if ska != ske or [k for k, _ in na] != [k for k, _ in ne]:
            problems.append("text differs")
        else:
            for (kind, va), (_, ve) in zip(na, ne, strict=True):
                tol = bat_tol if kind == "pct" else t_tol
                if abs(va - ve) > tol + 1e-9:
                    problems.append(f"{kind} {va:g} vs {ve:g}")
        if problems:
            out.append(
                f"line {i + 1}: {'; '.join(problems)}\n"
                f"    actual   {fmt(ta):>8}  {ma}\n    expected {fmt(te):>8}  {me}"
            )
    return out


def scorecard_block(text: str) -> list[str]:
    """From the '=== Scorecard' line to the end (trailing blank lines dropped)."""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, raw in enumerate(lines):
        if raw.startswith("=== Scorecard"):
            block = [x.rstrip() for x in lines[i:]]
            while block and not block[-1]:
                block.pop()
            return block
    return []


def diff_scorecard(actual_text: str, expected_text: str) -> list[str]:
    """[] means the scorecard blocks are identical line for line (exact text)."""
    act, exp = scorecard_block(actual_text), scorecard_block(expected_text)
    out = []
    for i in range(max(len(act), len(exp))):
        a = act[i] if i < len(act) else "<missing>"
        e = exp[i] if i < len(exp) else "<missing>"
        if a != e:
            out.append(f"scorecard line {i + 1}:\n    actual   {a}\n    expected {e}")
    return out


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim.golden")
    ap.add_argument("scenario", nargs="?", default="FRIDAY-RUSH")
    ap.add_argument("--out", default=str(ROOT / "runs"))
    ap.add_argument("--seed", default=None)
    ap.add_argument("--expected", default=None)
    args = ap.parse_args(argv)

    g = run(args.scenario, seed=args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base, n = f"golden-{stamp}", 1
    while (out / f"{base}.txt").exists() or (out / f"{base}.jsonl").exists():
        n += 1
        base = f"golden-{stamp}-{n}"
    txt, jl = out / f"{base}.txt", out / f"{base}.jsonl"
    txt.write_text(g.text(), encoding="utf-8", newline="\n")
    jl.write_text(g.jsonl(), encoding="utf-8", newline="\n")

    expected = Path(args.expected) if args.expected else EXPECTED.get(g.name)
    diffs = sdiffs = None
    if expected:
        exp_text = expected.read_text(encoding="utf-8")
        diffs = diff(g.lines, exp_text)
        sdiffs = diff_scorecard(g.text(), exp_text)
    print(f"wrote {txt} and {jl}")
    for d in (diffs or []) + (sdiffs or []):
        print(d)
    for v in g.violations:
        print("VIOLATION", v)
    vs = f"{len(g.violations)} violations"
    if diffs is None:
        print(f"{g.name}: {len(g.lines)} lines (no expected timeline), {vs}")
    else:
        print(
            f"{g.name}: {len(g.lines)} lines, {len(diffs)} diffs vs {expected.name}, "
            f"scorecard {len(scorecard_block(g.text()))} lines, {len(sdiffs)} diffs, {vs}"
        )
    print(
        f"coverage {g.kpi['coverage_pct']}% · flights {g.kpi['flights']} · "
        f"lowest landing {g.kpi['lowest_landing_pct']}% · event_hash {g.event_hash[:16]}"
    )
    return 1 if (diffs or sdiffs or g.violations) else 0


if __name__ == "__main__":
    sys.exit(main())
