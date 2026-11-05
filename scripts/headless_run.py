"""Full headless FRIDAY-RUSH through FakeRoom with the real runtime agents.

    uv run python scripts/headless_run.py [--out runs/] [--no-llm] [--budget-usd 5]

The golden conductor plays kazum and the stubs; real agents answer only the owned beats below (15
turns). On three refusal beats the agent does the work and the stub only fills in if the sim shows
no such refusal. Writes runs/headless-<ts>.md. Refusal lines are agent-worded, so the golden check
is: every other timeline line matches sim.golden.diff, and refusals match as a set of codes.
Exit 0 only with 0 non-refusal diffs, every golden code present, all 15 turns passing their checks,
no failed turn, no stub used, and managed beating baseline on every line.

COSTS TOKENS unless --no-llm (every turn answers NO_REPLY, no `claude` call: a plumbing check).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.harness import formats as f  # noqa: E402
from agents.harness.run_agent import ClaudeError, Result, golden_plan, stage  # noqa: E402
from scripts.demo_run import Conductor, timeline  # noqa: E402
from sim import golden  # noqa: E402
from sim.geo import fmt, tmin  # noqa: E402

SCENARIO = "FRIDAY-RUSH"
ROOM_LINES = 20
REFUSAL_LINE = re.compile(r"  (?:⛔ [A-Z_]+:|❌ REJECTED )")


@dataclass(frozen=True)
class Beat:
    t: str
    agent: str
    room: str
    format: str | tuple  # §4.4 names that must all appear; "" for a plain handoff line
    ask: str = ""  # a scripted kazum line, only where no World Bot line reaches the agent
    mention: str = ""  # a handoff the reply must contain


# No ask where the World Bot's line is enough: the agent works from what a real room would show.
BEATS = [
    Beat("17:09", "fleet", "#fleet-hangar", "ROTATION"),
    Beat("17:12", "dispatch", "#dispatch", "INCIDENT"),
    Beat("17:14", "dispatch", "#dispatch", "EYES_ON"),
    Beat("17:15", "intel", "#incident-1", "SITREP"),
    Beat("17:22", "comms", "#field-units", "RELEASED"),
    Beat("17:36", "fleet", "#fleet-hangar", "SLOT"),
    Beat("17:40", "dispatch", "#dispatch", "INCIDENT"),
    Beat("17:41:30", "dispatch", "#dispatch", "LAUNCH", mention="@relay"),
    Beat("17:43", "privacy", "#privacy-log", "AUTH"),
    Beat("17:43", "relay", "#incident-2", "TRACK"),  # after the 17:42 feed fact names the vehicle
    Beat("17:49", "relay", "#incident-2", "HANDOFF"),
    Beat("17:54", "relay", "#incident-2", "", mention="@airspace"),
    Beat("18:02", "relay", "#incident-2", "TRACK"),
    Beat("18:05", "dispatch", "#dispatch", ("REJECTED", "LAUNCH")),
    Beat("18:21", "airspace", "#airspace", "WEATHER"),
]
# Refusal beats, the agent acting: code → (agent, format)
OWNED = {
    "PRIVACY_POLICY": ("privacy", "PRIVACY_REFUSAL"),
    "NO_FLY_ZONE": ("airspace", "ROUTE_DENIED"),
    "MISSION_EXPIRED": ("dispatch", "REJECTED"),
}
REPORT = Beat("20:00", "comms", "#shift-report", "SHIFT_REPORT", "@comms post the shift report.")
ALLOWED_EXTRAS = {"AUTH_REQUIRED"}  # 17:40: Dispatch logs the track it may not start (05-AGENTS)


TURNS = len(BEATS) + len(OWNED) + 1


class Shift:
    """The agents' side of the run: one turn at a beat, checks recorded, never stops the run."""

    def __init__(self, s, room, no_llm: bool, budget: float):
        self.s, self.room, self.no_llm, self.budget = s, room, no_llm, budget
        self.seen: dict[str, int] = {}
        self.turns: list[dict] = []
        self.spent = 0.0
        self.failed: list[str] = []
        self.asks: list[str] = []

    def lines_for(self, agent: str, room: str, extra: str) -> str:
        start = self.seen.get(agent, 0)
        self.seen[agent] = len(self.room.posted)
        fresh = self.room.posted[start:]
        new = [f"[{r}] {t}" for r, t in fresh if r == room or f"@{agent}" in t]
        extra = extra or ("" if new else f"@{agent} post your line for this beat.")
        if extra:
            self.asks.append(f"{agent} in {room}: {extra}")
        return "\n".join([*new[-ROOM_LINES:], *([f"[{room}] kazum: {extra}"] if extra else [])])

    def mark_seen(self, agent: str) -> None:
        """What the agent's own writes echoed into the rooms is not news on its next turn."""
        self.seen[agent] = len(self.room.posted)

    async def turn(self, beat: Beat, clock: str) -> Result | None:
        prompt = self.lines_for(beat.agent, beat.room, beat.ask)
        if self.spent >= self.budget:
            self.failed.append(f"{clock} {beat.agent}: skipped, budget {self.budget} USD spent")
            return None
        try:
            if self.no_llm:
                r = Result("NO_REPLY", [], [], 0.0, beat.agent, prompt, clock)
            else:
                r = await asyncio.to_thread(self.s.ask, beat.agent, prompt, clock)
        except ClaudeError as e:
            self.failed.append(f"{clock} {beat.agent}: {e}")
            return None
        self.spent += r.cost
        self.record(beat, r)
        if not f.is_quiet(r.reply):
            self.room.say(beat.agent, beat.room, r.reply, echo=True)
        return r

    @staticmethod
    def format_ok(beat: Beat, reply: str) -> bool:
        """Every wanted format present, no off-format line, the handoff really made."""
        names = (beat.format,) if isinstance(beat.format, str) else beat.format
        return (
            not f.is_quiet(reply)
            and all(f.lines_matching(n, reply) for n in names if n)
            and not f.malformed_lines(reply)
            and beat.mention in reply
        )

    def record(self, beat: Beat, r: Result) -> None:
        self.turns.append(
            {
                "clock": r.at,
                "agent": r.agent,
                "room": beat.room,
                "format": beat.format or beat.mention,
                "format_ok": self.format_ok(beat, r.reply),
                "quiet": f.is_quiet(r.reply),
                "forbidden": f.forbidden_calls(r.agent, [c.argv for c in r.cli_calls]),
                "unproven": f.unproven_numbers(r.reply, r.sources),
                "calls": len(r.cli_calls),
                "cost": r.cost,
                "reply": r.reply,
            }
        )


class HeadlessConductor(Conductor):
    """Golden kazum. Skips a stub when the real agent already did that step."""

    shift: Shift
    notes: list[str]  # information only
    stubs: list[str]  # a step the agents should have done: fails the run
    performed: list[str]  # every golden step the conductor did itself

    async def settle(self) -> None:
        """The bot marks an event seen before it posts it (it reports the beat first), so a seen
        event may not be in the room yet. A turn must not start until the room has gone still."""
        await super().settle()
        posted = -1
        while posted != len(self.room.posted):
            posted = len(self.room.posted)
            await asyncio.sleep(0.05)

    async def card(self, agent: str, argv: tuple, room: str, by: str = "kazum") -> str:
        pending = (await self.get("/approvals", status="pending"))["approvals"]
        mine = [a for a in pending if a["type"] == argv[0]]
        if not mine:
            return await super().card(agent, argv, room, by)
        await self.chat(by, room, f"approve {mine[0]['id']}")
        return mine[0]["id"]

    async def refuse(self, a: dict) -> None:
        code = a["code"]
        agent, fmt_name = OWNED[code]
        before = (await self.get("/kpi"))["managed"]["refusals"].count(code)
        room = "#incident-2"
        clock = (await self.get("/status"))["clock"]
        ask = f"FO: @{agent} {a['request']}"
        await self.shift.turn(Beat(clock, agent, room, fmt_name, ask), clock)
        await self.settle()
        self.shift.mark_seen(agent)
        # the sim's own count: a real call it refused, not a log-refusal or a simulate
        if (await self.get("/kpi"))["managed"]["refusals"].count(code) <= before:
            self.stubs.append(f"{clock} {agent}: the sim refused no {code}; stub filled in")
            await super().refuse(a)

    async def play(self, t: float, a: dict) -> None:
        if a["act"] == "hold":  # Airspace already held on its refusal beat: that is this step
            fleet = (await self.get("/fleet"))["drones"]
            if any(d["mission"] == "hold" for d in fleet):
                return
        await super().play(t, a)

    async def safe_play(self, t: float, a: dict) -> None:
        said = a.get("what") or a.get("code") or a.get("unit") or a.get("drone") or ""
        self.performed.append(f"{fmt(t)} {a['act']} {said}".rstrip())
        try:
            await self.play(t, a)
        except RuntimeError as e:  # an agent got there first or moved the state
            self.stubs.append(f"{fmt(t)} golden {a['act']} not played: {str(e)[:200]}")


async def run_shift(s, args, out) -> dict:
    async with s.live(out=out) as (con, room):
        con.__class__ = HeadlessConductor
        shift = Shift(s, room, args.no_llm, args.budget_usd)
        con.shift, con.notes, con.stubs, con.performed = shift, [], [], []
        plan = [(tmin(b.t), -2, b) for b in BEATS]
        plan += golden_plan(s.scenario)
        for t, _i, step in sorted(plan, key=lambda x: x[:2]):
            await con.to(t)
            if isinstance(step, Beat):
                await shift.turn(step, step.t)
                await con.settle()
                shift.mark_seen(step.agent)
            else:
                await con.safe_play(t, step)
        (await con.api.post("/control/skip", json={"to": s.scenario["end"]})).raise_for_status()
        await con.settle()
        (await con.api.post("/baseline/run")).raise_for_status()
        await shift.turn(REPORT, REPORT.t)
        await con.settle()
        get = con.get
        return {
            "shift": shift,
            "notes": con.notes,
            "stubs": con.stubs,
            "performed": con.performed,
            "asks": shift.asks,
            "events": (await get("/events"))["events"],
            "kpi": await get("/kpi"),
            "room": list(room.posted),
        }


def scorecard_diffs(kpi: dict, expected: list[str]) -> list[str]:
    """golden.diff_scorecard on a block rebuilt from /kpi (the API serves numbers, not this text).
    The tail lines are rendered from the served numbers. The win rows need the engine, so they
    are the golden rows only when every managed and baseline number equals the golden run's."""
    ref = golden.run(SCENARIO)
    m = kpi["managed"]
    off = [k for k in ref.kpi if m.get(k) != ref.kpi[k]]
    off += [f"baseline.{k}" for k in ref.baseline if kpi["baseline"].get(k) != ref.baseline[k]]
    n_tail = 4
    rows = ref.scorecard[:-n_tail] if not off else [f"<numbers differ: {off}>"]
    loops = ", ".join(f"{k} {v:.0f}" for k, v in m["coverage_by_loop_min"].items())
    codes = m["refusals"]
    clean = all(v == "ok" for v in kpi["safety"].values())
    safe = "OK — 0 violations" if clean else "<safety violation>"
    tail = [
        f"coverage by loop (min): {loops}",
        f"refusals enforced: {len(codes)} → " + ", ".join(sorted(set(codes))),
        f"fleet: {m['drones_flown']}/14 drones flew, {m['flights']} flights, "
        f"lowest landing battery {m['lowest_landing_pct']:.1f}% (reserve 25%)",
        f"ready floor: minimum {m['ready_floor_min']} READY drones (at {m['ready_floor_at']})",
    ]
    text = "\n".join([golden.SCORECARD_HEAD, *rows, *tail, "", safe])
    return golden.diff_scorecard(text, "\n".join(expected))


def verdict(run: dict) -> dict:
    lines = timeline(run["events"])
    expected = golden.EXPECTED[SCENARIO].read_text(encoding="utf-8").splitlines()
    wins, shift = run["kpi"]["wins"], run["shift"]
    lost = [k for k, won in wins.items() if won is not True]
    plain = [ln for ln in lines if not REFUSAL_LINE.search(ln)]
    keep = "\n".join(ln for ln in expected if not REFUSAL_LINE.search(ln))
    diffs = golden.diff(plain, keep)
    want = Counter(c for ln in expected if REFUSAL_LINE.search(ln) for c in f.codes_in(ln))
    got = Counter(c for ln in lines if REFUSAL_LINE.search(ln) for c in f.codes_in(ln))
    missing = sorted(set(want) - set(got))
    over = sorted((got - want).items())
    extras = [f"extra refusal: {c} x{n}" for c, n in over if c in ALLOWED_EXTRAS]
    bad_extras = [f"extra refusal: {c} x{n}" for c, n in over if c not in ALLOWED_EXTRAS]
    score = scorecard_diffs(run["kpi"], expected)
    bad_turns = [
        f"{t['clock']} {t['agent']}"
        for t in shift.turns
        if not t["format_ok"] or t["forbidden"] or t["unproven"]
    ]
    if len(shift.turns) != TURNS:
        bad_turns.append(f"{len(shift.turns)} turns, want {TURNS}")
    return {
        "lines": lines,
        "diffs": diffs,
        "codes": sorted(got),
        "missing": missing,
        "extras": extras,
        "bad_extras": bad_extras,
        "score": score,
        "bad_turns": bad_turns,
        "wins": wins,
        "lost": lost,
        "safety": run["kpi"]["safety"],
        "ok": bool(wins)
        and not lost
        and not diffs
        and not missing
        and not bad_extras
        and not score
        and not bad_turns
        and not shift.failed
        and not run["stubs"],
    }


def report(run: dict, v: dict, seconds: float) -> str:
    shift, out = run["shift"], []
    add = out.append
    add(f"# Headless shift · {SCENARIO} (simulated)\n")
    add("Real agents on the beats below, golden kazum, World Bot on FakeRoom. Fictional.\n")
    add("## Summary\n")
    add(f"- agent turns: {len(shift.turns)} · cost: {shift.spent:.2f} USD · wall: {seconds:.0f} s")
    beat = "NO" if v["lost"] or not v["wins"] else "yes"
    add(f"- managed beats baseline on every line: {beat}")
    add(f"- non-refusal golden differences: {len(v['diffs'])}")
    add(f"- refusal codes: {v['codes']} · missing: {v['missing'] or 'none'}")
    add(f"- turns failing a check: {v['bad_turns'] or 'none'}")
    add(f"- scorecard differences: {len(v['score'])} · disallowed extras: {v['bad_extras']}")
    add(f"- stubs that filled in for an agent: {len(run['stubs'])}")
    add(f"- scripted kazum asks: {len(run['asks'])} (listed below)")
    add(f"- golden steps performed by the conductor: {len(run['performed'])} (listed below)")
    add(f"- RESULT: {'PASS' if v['ok'] else 'NOT PASSED'}")
    add("\n## Scorecard\n")
    add("| line | managed wins |\n|---|---|")
    add("\n".join(f"| {k} | {won} |" for k, won in v["wins"].items()))
    add(f"\nSafety: {v['safety']}")
    add(f"\nManaged: {run['kpi']['managed']}")
    add(f"\nBaseline: {run['kpi']['baseline']}")
    add("\n## Agent turns\n")
    add("| at | agent | format | ok | calls | forbidden | unproven | USD |")
    add("|---|---|---|---|---|---|---|---|")
    for t in shift.turns:
        ok = "quiet" if t["quiet"] else ("yes" if t["format_ok"] else "NO")
        add(
            f"| {t['clock']} | {t['agent']} | {t['format']} | {ok} | {t['calls']} | "
            f"{t['forbidden'] or ''} | {t['unproven'] or ''} | {t['cost']:.3f} |"
        )
    add("\n## Failed turns and stubs that filled in\n")
    add("\n".join(f"- {x}" for x in [*shift.failed, *run["stubs"]]) or "None.")
    add("\n## Scripted kazum asks (not from the World Bot)\n")
    add("\n".join(f"- {x}" for x in run["asks"]) or "None.")
    add("\n## Golden steps the conductor performed, not an agent\n")
    add("\n".join(f"- {x}" for x in run["performed"]))
    add("\n## Scorecard differences\n")
    add("\n".join(f"    {x}" for x in v["score"]) or "None.")
    add("\n## Notes (not failures)\n")
    add("\n".join(f"- {x}" for x in [*run["notes"], *v["extras"]]) or "None.")
    add("\n## Differences from docs/golden-timeline.txt\n")
    add("\n".join(f"    {x}" for x in v["diffs"]) or "None.")
    add("\n## Room transcript\n\n```")
    add("\n".join(f"[{r}] {t}" for r, t in run["room"]))
    add("```\n\n## Event timeline\n\n```")
    add("\n".join(v["lines"]))
    add("```")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/headless_run.py")
    ap.add_argument("--out", default=str(ROOT / "runs"))
    ap.add_argument("--no-llm", action="store_true", help="plumbing check: no claude, no cost")
    ap.add_argument("--budget-usd", type=float, default=5.0, help="stop asking agents past this")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with open(os.devnull, "w", encoding="utf-8") as quiet, stage(SCENARIO) as s:
        run = asyncio.run(run_shift(s, args, quiet))
    v = verdict(run)
    path = out / f"headless-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    path.write_text(report(run, v, time.monotonic() - t0), encoding="utf-8", newline="\n")
    print(f"wrote {path}")
    print(f"turns {len(run['shift'].turns)}, cost {run['shift'].spent:.2f} USD, wins {v['wins']}")
    print(f"non-refusal diffs {len(v['diffs'])}, codes {v['codes']}, missing {v['missing']}")
    print(f"bad turns {v['bad_turns']}, stubs {len(run['stubs'])}, extras {v['extras']}")
    print(f"bad extras {v['bad_extras']}, scorecard diffs {len(v['score'])}")
    print(f"scripted asks {len(run['asks'])}, conductor steps {len(run['performed'])}")
    for x in run["shift"].failed:
        print("FAILED:", x)
    print("RESULT:", "PASS" if v["ok"] else "NOT PASSED")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
