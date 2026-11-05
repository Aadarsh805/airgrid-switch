"""/demo-run: FRIDAY-RUSH dry run, end to end, without Discord.

    uv run python scripts/demo_run.py [--out runs/] [--quiet]

A real uvicorn sim in demo mode with temp tokens and a temp DB, the World Bot on FakeRoom over the
real SSE stream, kazum typing in chat, and the seven agents as stubs calling the `airgrid` CLI
with their own tokens. The conductor below replays the golden actions from outside the sim; nothing
is applied inside the engine. Saves runs/demo-dryrun-<ts>.md. Exit 1 on any difference from the
golden run.

Time: `/control/skip {stop: "actions"}` stops at a tick's actions slot, so each write lands where
the golden run applies it. The sim decides which cards hold the clock; the report checks each.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim import golden  # noqa: E402
from sim.auth import init_tokens  # noqa: E402
from sim.geo import fmt, tmin  # noqa: E402
from sim.scenario import load_scenario  # noqa: E402
from worldbot.bot import WorldBot, load_human_tokens  # noqa: E402
from worldbot.decisions_sink import FakeSink  # noqa: E402
from worldbot.fake_room import FakeRoom  # noqa: E402

SCENARIO = "FRIDAY-RUSH"
SETTLE_S = 20
BROADCAST = "Lane 2 closed, keep left"
BOT_LINE = re.compile(r"^📣 (\w+) · (.*) · (#[\w-]+) · @(\w+)$", re.DOTALL)
# Narration stubs: what the owning agent would say, in the §4.4 heading (Phase 06 replaces them)
STUB_HEADS = {
    "CAD_ALERT": "🚨 INCIDENT",
    "LAUNCH": "🚀 LAUNCH",
    "EYES_ON": "👁 EYES-ON",
    "ROTATION": "🔄 ROTATION",
    "SLOT_REROUTE": "🅿 SLOT",
    "WEATHER_GROUNDED": "🌬 WEATHER",
    "HOLD": "⛔ ROUTE DENIED",
    "HANDOFF": "🤝 HANDOFF",
    "REACQUIRE": "🎯 TRACK",
    "DIVERT": "🎯 TRACK",
    "FEED_FACT": "📋 SITREP",
    "UNIT_TASKED": "🚓 UNIT",
    "UNIT_RELEASED": "✅ RELEASED",
    "APPROVED": "🔐 AUTH",
    "REFUSAL": "⛔ REFUSAL",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_sim(port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sim.app:app", "--port", str(port)]
        + ["--log-level", "warning"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def wait_for(check, what: str, timeout: float = SETTLE_S) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise TimeoutError(f"timed out waiting for {what}")
        await asyncio.sleep(0.002)


class Conductor:
    """Plays kazum (chat) and the agent stubs (the `airgrid` CLI, as a real agent would, with its
    own role and token). Direct HTTP here is operator or harness only: the clock and checks."""

    def __init__(
        self, api: httpx.AsyncClient, room: FakeRoom, bot: WorldBot, tokens: dict, url: str
    ):
        self.api, self.room, self.bot, self.tokens, self.url = api, room, bot, tokens, url
        self.approvals: list[dict] = []
        self.calls: list[str] = []  # stub calls that were refused, as expected, with their code

    # --- plumbing ---
    async def get(self, path: str, **params) -> dict:
        r = await self.api.get(path, params=params)
        r.raise_for_status()
        return r.json()

    async def read(self, agent: str, *argv: str, expect: str | None = None) -> dict:
        """One `airgrid` call in its own process. Exit 0 ok, 2 + code as expected, else raise."""
        env = os.environ | {
            "AIRGRID_API_URL": self.url,
            "AIRGRID_ROLE": agent,
            "AIRGRID_TOKEN": self.tokens[agent],
        }
        p = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cli.airgrid",
            *argv,
            cwd=ROOT,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await p.communicate()
        text = out.decode("utf-8", "replace")
        body = json.loads(text) if text.strip().startswith("{") else {}
        got = None if p.returncode == 0 else body.get("code")
        if got != expect or p.returncode not in (0, 2):
            raise RuntimeError(f"{agent}: airgrid {argv} exit {p.returncode}: {text[:300]}")
        if expect:
            self.calls.append(f"{agent} {argv[0]} → {expect}")
        return body

    async def act(self, agent: str, *argv: str, expect: str | None = None) -> dict:
        body = await self.read(agent, *argv, expect=expect)
        await self.settle()
        return body

    async def settle(self) -> None:
        """The bot has seen every event so far and answered every room message."""
        events = (await self.get("/events"))["events"]
        seq = events[-1]["seq"] if events else 0
        await wait_for(
            lambda: self.bot.last_seq >= seq and self.bot.handled >= self.room.said, "the bot"
        )

    async def to(self, t: float) -> None:
        r = await self.api.post("/control/skip", json={"to": fmt(t), "stop": "actions"})
        r.raise_for_status()
        await self.settle()

    async def chat(self, author: str, room: str, text: str) -> None:
        self.room.say(author, room, text, echo=True)
        await self.settle()

    async def card(self, agent: str, argv: tuple, room: str, by: str = "kazum") -> str:
        """An agent proposes, the sim decides whether the clock holds, a human answers in chat."""
        aid = (await self.read(agent, "propose", *argv))["result"]["id"]
        await wait_for(lambda: aid in self.bot.cards, f"card {aid}")
        status = await self.get("/status")
        held = f"approval:{aid}" in status["clock_held"]
        self.approvals.append({"id": aid, "type": argv[0], "clock": status["clock"], "held": held})
        if argv[0] == "broadcast":  # the two who may not: both stopped by the bot, no sim call
            await self.chat("dispatch", room, f"approve {aid}")
            await self.chat("visitor42", room, f"approve {aid}")
        await self.chat(by, room, f"approve {aid}")
        return aid

    async def tracker(self, agent: str) -> dict:
        fleet = (await self.read(agent, "fleet"))["drones"]
        return next(d for d in fleet if d["mission"] == "track")

    async def refuse(self, a: dict) -> None:
        code, request, by = a["code"], a["request"], a.get("by")
        await self.chat("kazum", "#incident-2", f"FO: {request}")
        if code == "PRIVACY_POLICY":
            agent = "privacy"
            await self.act(agent, "privacy", "check", "--request", request, expect=code)
        elif code == "NO_FLY_ZONE":
            agent = "airspace"
            d = await self.tracker(agent)
            zones = (await self.read(agent, "airspace"))["zones"]
            r = next(z for z in zones if z["kind"] == "red")["rect"]
            centre = f"{(r['x'][0] + r['x'][1]) / 2},{(r['y'][0] + r['y'][1]) / 2}"
            argv = ("--drone", d["id"], "--incident", d["incident"], "--to", centre)
            await self.act(agent, "corridor", *argv, "--why", request, expect=code)
        elif code == "MISSION_EXPIRED":
            agent = "dispatch"
            incidents = (await self.read(agent, "incidents"))["incidents"]
            closed = [i for i in incidents if i["status"] == "closed"][-1]
            drone = re.search(r"D-\d+", request).group(0)
            argv = ("--drone", drone, "--mission", "perimeter", "--incident", closed["id"])
            await self.act(
                agent, "launch", *argv, "--to", closed["at"], "--why", request, expect=code
            )
        else:
            raise RuntimeError(f"no stub for scripted refusal {code}")
        await self.act(agent, "log-refusal", "--about", request, "--code", code, "--by", by)

    async def quote_fact(self, a: dict) -> tuple[str, str]:
        """(incident, the full revealed fact value the scripted note paraphrases), as sim.golden."""
        units = (await self.read("comms", "units"))["units"]
        iid = next(u["incident"] for u in units if u["id"] == a["unit"])
        words = set(re.findall(r"[a-z]{4,}", a["note"].lower()))
        inc = (await self.read("comms", "incidents", "--id", iid))["incident"]
        for f in inc["facts"]:
            for v in f["fact"].values():
                if isinstance(v, str) and words & set(re.findall(r"[a-z]{4,}", v.lower())):
                    return iid, v
        return iid, a["note"]

    async def launch(self, t: float, a: dict) -> None:
        if a.get("basis") == "auto_policy" and a.get("incident"):
            for e in (await self.get("/events"))["events"]:
                p = e["payload"]
                if e["type"] == "launch" and p.get("basis") == "auto_policy":
                    if p["drone"] == a["drone"] and abs(e["t"] - t) <= 0.1 + 1e-9:
                        return  # the sim's own auto-launch already did it (L29b)
        to = a["to"] if isinstance(a["to"], str) else ",".join(str(v) for v in a["to"])
        argv = ["--drone", a["drone"], "--mission", a["mission"]]
        argv += ["--incident", a["incident"], "--to", to]
        for flag, key in (("--basis", "basis"), ("--why", "note")):
            if a.get(key):
                argv += [flag, a[key]]
        if a.get("expect") == "PILOT_SATURATED":
            code = "PILOT_SATURATED"
            await self.act("dispatch", "launch", *argv, "--pilot", a["pilot"], expect=code)
            await self.act(
                "dispatch",
                "log-refusal",
                *("--about", f"launch {a['drone']}", "--code", code, "--drone", a["drone"]),
                *("--pilot", a["pilot"], "--fallback-pilot", a["fallback_pilot"]),
            )
            await self.act("dispatch", "launch", *argv, "--pilot", a["fallback_pilot"])
            return
        await self.act("dispatch", "launch", *argv, "--pilot", a["pilot"])

    async def play(self, t: float, a: dict) -> None:
        act = a["act"]
        if act == "standing_policy":
            scope = "standing auto-launch policy for accident/fire/flood"
            await self.card("dispatch", ("standing-policy", "--scope", scope), "#dispatch")
        elif act == "launch":
            await self.launch(t, a)
        elif act == "approve":
            type_ = golden.APPROVE_TYPE.get(a["what"], a["what"])
            if type_ == "track":
                argv = ("track", "--incident", a["incident"], "--scope", a["scope"])
            else:
                argv = (type_, "--drone", a["drone"], "--text", BROADCAST)
            await self.card("dispatch", argv, "#dispatch", a["by"])
        elif act == "divert":
            argv = ("--drone", a["drone"], "--incident", a["incident"], "--mission", a["mission"])
            why = ("--why", a["note"]) if a.get("note") else ()
            await self.act("dispatch", "divert", *argv, "--pilot", a["pilot"], *why)
        elif act == "release_unit":
            iid, evidence = await self.quote_fact(a)
            argv = ("--unit", a["unit"], "--incident", iid, "--evidence", evidence)
            await self.act("comms", "unit", "release", *argv, "--note", a["note"])
        elif act == "task_unit":
            argv = ("--unit", a["unit"], "--incident", a["incident"])
            await self.act("comms", "unit", "task", *argv)
        elif act == "rtb":
            why = a.get("note", "incident task done")
            await self.act("fleet", "rtb", "--drone", a["drone"], "--why", why)
        elif act == "refuse":
            await self.refuse(a)
        elif act == "hold":
            drone = a["drone"]
            if drone == "TRACKER":
                drone = (await self.tracker("airspace"))["id"]
            argv = ("--drone", drone, "--at-boundary", a["at_boundary"])
            await self.act("airspace", "hold", *argv)
        elif act == "reacquire":
            d = (await self.read("dispatch", "fleet", "--id", a["drone"]))["drone"]
            inc = d["incident"] or (await self.tracker("dispatch"))["incident"]
            await self.act("dispatch", "track", "assign", "--drone", d["id"], "--incident", inc)
        elif act == "take_control":
            await self.chat(a["pilot"], "#fleet-hangar", f"RP: take control {a['drone']}")
        elif act == "close_incident":
            argv = ("--id", a["incident"], "--note", a["note"])
            await self.act("dispatch", "incident", "close", *argv)
        elif act == "rtb_incident":
            for d in (await self.read("fleet", "fleet"))["drones"]:
                on_it = d["state"] == "airborne" and d["incident"] == a["incident"]
                if on_it and d["mission"] != "returning":
                    why = f"{a['incident']} closed"
                    await self.act("fleet", "rtb", "--drone", d["id"], "--why", why)
        else:
            raise RuntimeError(f"unknown golden action {act}")

    async def run(self, scenario: dict) -> None:
        start, actions = tmin(scenario["start"]), scenario.get("golden_actions") or []
        plan = [(start, -1, {"act": "standing_policy"})]
        plan += [(tmin(a["t"]), i, a) for i, a in enumerate(actions)]
        n = len(actions)
        plan += [(tmin(a["t"]), n + j, a) for j, a in enumerate(scenario.get("units_golden") or [])]
        for t, _i, a in sorted(plan, key=lambda x: x[:2]):
            await self.to(t)
            await self.play(t, a)
        (await self.api.post("/control/skip", json={"to": scenario["end"]})).raise_for_status()
        await self.settle()
        (await self.api.post("/baseline/run")).raise_for_status()


def narrate(room: FakeRoom):
    """Agent stubs: the owner of a beat answers in its §4.4 heading, so holds release."""

    def on_post(_room: str, text: str) -> None:
        m = BOT_LINE.match(text)
        if m and m.group(1) in STUB_HEADS:
            kind, what, where, agent = m.groups()
            room.say(agent, where, f"{STUB_HEADS[kind]} | {what} | (stub narration)", echo=True)

    return on_post


def event_hash(events: list[dict]) -> str:
    text = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timeline(events: list[dict]) -> list[str]:
    return [f"{fmt(e['t']):>8}  {e['text']}" for e in events if e["timeline"]]


def report(data: dict) -> str:
    """The saved Markdown report."""
    d, out = data, []
    add = out.append
    add(f"# Demo dry run · {SCENARIO} (simulated)\n")
    add(
        "World Bot on FakeRoom; kazum through chat; runtime agents are stubs. Fictional district.\n"
    )
    add("## Summary\n")
    for k, v in d["summary"].items():
        add(f"- {k}: {v}")
    add("\n## Approvals and decisions\n")
    add("| id | type | proposed by | card at | clock held | decision | by | at |")
    add("|---|---|---|---|---|---|---|---|")
    for a in d["approvals"]:
        add(
            f"| {a['id']} | {a['type']} | {a['created_by']} | {a['created']} | "
            f"{'yes' if a['held'] else 'NO'} | {a['status']} | {a['decided_by']} | {a['decided']} |"
        )
    add("\n## Refusals\n")
    add("Sim refusals (decision log):\n")
    for r in d["refusals"]:
        add(f"- {r['clock']} {r['actor']} `{r['action']}` → **{r['code']}**: {r['rationale']}")
    add("\nStopped by the World Bot before the sim (no sim call):\n")
    for r in d["bot_refusals"]:
        add(f"- {r['actor']} `{r['action']} {r['target']}` → **{r['code']}**: {r['detail']}")
    add("\n## Simulated lines\n")
    add("\n".join(f"- `{room}` {text}" for room, text in d["simulated"]))
    add("\n## Final KPIs vs baseline\n")
    add("| line | managed | baseline | win |\n|---|---|---|---|")
    for r in d["compare"]:
        add(f"| {r['label']} | {r['managed']} | {r['baseline']} | {r.get('win', '')} |")
    add("\n## Differences from docs/golden-timeline.txt\n")
    add("\n".join(f"    {x}" for x in d["diffs"]) if d["diffs"] else "None.")
    add("\n## Event timeline\n\n```")
    add("\n".join(d["timeline"]))
    add("```")
    return "\n".join(out) + "\n"


async def conduct(api_url: str, tokens_file: Path, sink: FakeSink, scenario: dict, out) -> dict:
    doc = yaml.safe_load(tokens_file.read_text(encoding="utf-8"))
    room = FakeRoom(interactive=False, out=out)
    room._on_post = narrate(room)
    api = httpx.AsyncClient(base_url=api_url, timeout=30)
    r = await api.post("/scenario/load", json={"name": SCENARIO, "mode": "demo"})
    r.raise_for_status()
    bot = WorldBot(room, api_url, tokens=load_human_tokens(tokens_file), sink=sink)
    runner = asyncio.create_task(bot.run())
    con = Conductor(api, room, bot, doc["agents"], api_url)
    try:
        await wait_for(lambda: bot.pilots is not None or bot.last_seq == 0, "the bot")
        await con.run(scenario)
        get = con.get
        return {
            "events": (await get("/events"))["events"],
            "kpi": await get("/kpi"),
            "approvals": (await get("/approvals"))["approvals"],
            "decisions": (await get("/decisions"))["decisions"],
            "privacy": (await get("/privacy-log"))["privacy_log"],
            "held": {a["id"]: a["held"] for a in con.approvals},
            "room": list(room.posted),
        }
    finally:
        bot.stop()
        await runner
        await bot.aclose()
        await api.aclose()


def summarise(run: dict, ref: golden.GoldenRun, sink: FakeSink, seconds: float) -> dict:
    events, kpi = run["events"], run["kpi"]
    lines = timeline(events)
    expected = golden.EXPECTED[SCENARIO].read_text(encoding="utf-8")
    diffs = golden.diff(lines, expected)
    got_hash = event_hash(events)
    mismatch = {
        k: (kpi["managed"].get(k), ref.kpi[k])
        for k in ref.kpi
        if kpi["managed"].get(k) != ref.kpi[k]
    }
    approvals = [a | {"held": run["held"].get(a["id"], False)} for a in run["approvals"]]
    refusals = [r for r in run["decisions"] if r["code"]]
    bot_refusals = [e for e in sink.entries if e["kind"] == "refusal"]
    summary = {
        "timeline lines": f"{len(lines)} ({len(diffs)} diffs vs docs/golden-timeline.txt)",
        "event_hash": f"{got_hash[:16]} (golden {ref.event_hash[:16]})",
        "KPI mismatches vs golden": mismatch or 0,
        "approvals through chat": sum(1 for a in approvals if a["status"] == "approved"),
        "cards without a clock hold": sum(1 for a in approvals if not a["held"]),
        "sim refusals": len(refusals),
        "bot refusals (no sim call)": len(bot_refusals),
        "coverage": f"{kpi['managed']['coverage_pct']}%",
        "flights": kpi["managed"]["flights"],
        "lowest landing": f"{kpi['managed']['lowest_landing_pct']}%",
        "wins": kpi["wins"],
        "room messages": len(run["room"]),
        "wall time": f"{seconds:.1f} s",
    }
    simulated = [(r, t) for r, t in run["room"] if t.startswith(("🛩", "📻"))]
    return {
        "summary": summary,
        "approvals": approvals,
        "refusals": refusals,
        "bot_refusals": bot_refusals,
        "simulated": simulated,
        "compare": ref.compare,
        "diffs": diffs + [f"KPI {k}: {v}" for k, v in mismatch.items()],
        "timeline": lines,
        "ok": not diffs
        and not mismatch
        and got_hash == ref.event_hash
        and all(a["held"] for a in approvals),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scripts/demo_run.py")
    ap.add_argument("--out", default=str(ROOT / "runs"))
    ap.add_argument("--quiet", action="store_true", help="don't print the room transcript")
    args = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="airgrid-demo-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        env = os.environ | {
            "AIRGRID_TOKENS": str(tmp / "tokens.yaml"),
            "AIRGRID_HUMANS": str(ROOT / "config" / "humans.example.yaml"),
            "AIRGRID_DB": str(tmp / "demo.db"),
        }
        os.environ.update(env)  # init_tokens and the bot read the same files
        init_tokens(tmp / "tokens.yaml")
        port = free_port()
        sim = start_sim(port, env)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(600):
                try:
                    if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.05)
            sink = FakeSink(out / f"demo-dryrun-{stamp}-decisions.jsonl")
            scenario = load_scenario(SCENARIO)
            room_out = open(os.devnull, "w", encoding="utf-8") if args.quiet else None
            run = asyncio.run(conduct(url, tmp / "tokens.yaml", sink, scenario, room_out))
        finally:
            sim.terminate()
            sim.wait(timeout=10)
    data = summarise(run, golden.run(SCENARIO), sink, time.monotonic() - t0)
    path = out / f"demo-dryrun-{stamp}.md"
    path.write_text(report(data), encoding="utf-8", newline="\n")
    print(f"\nwrote {path}")
    for k, v in data["summary"].items():
        print(f"  {k}: {v}")
    for d in data["diffs"]:
        print(d)
    print("RESULT:", "golden outcome reproduced" if data["ok"] else "DIFFERS from golden")
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
