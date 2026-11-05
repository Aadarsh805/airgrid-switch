"""Headless harness for the runtime agents (docs/phase06-interface.md).

    run_agent(agent, room_transcript, at, scenario="FRIDAY-RUSH", setup=None, after=None) -> Result

Starts a real sim on a free port with temp tokens and DB, loads the scenario (clock paused),
replays the golden actions before `at` (from outside the sim, as scripts/demo_run.py does), skips
to `at`, applies `setup`, installs the agents to a temp AIRGRID_AGENTS_HOME and runs `claude -p`
from that agent's directory with `_bin` first on PATH. The harness never sees a Claude credential:
`claude` uses whatever login the user already has. Agent tokens live only in the temp `.env` files.

Env: AIRGRID_AGENT_MODEL (default claude-sonnet-5-5) · AIRGRID_AGENT_BUDGET_USD (default 0.50, per
run) · AIRGRID_CLAUDE_BIN (default `claude` from PATH; a stub for no-LLM harness tests).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.demo_run import Conductor, free_port, start_sim, wait_for  # noqa: E402
from sim.geo import tmin  # noqa: E402
from sim.scenario import load_scenario  # noqa: E402
from worldbot.bot import WorldBot  # noqa: E402
from worldbot.decisions_sink import FakeSink  # noqa: E402
from worldbot.fake_room import FakeRoom  # noqa: E402

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_BUDGET_USD = 0.50
TURN_TIMEOUT_S = 300
HUMANS_FILE = ROOT / "config" / "humans.example.yaml"
HUMAN = "kazum"
AGENTS = (
    "dispatch",
    "fleet",
    "airspace",
    "intel",
    "relay",
    "privacy",
    "comms",
    "fire-station",
    "hospital",
    "police",
)
INSTALL = ROOT / "scripts" / "install_agents.sh"
# Set by an enclosing Claude Code session; a nested `claude -p` must not inherit them.
NESTED_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
ISOLATION_VARS = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")


class HarnessError(RuntimeError):
    """The harness could not set the stage (sim, installer). Not an agent failure."""


class ClaudeError(RuntimeError):
    """`claude -p` itself failed: login, budget, timeout. The text is claude's own."""


@dataclass
class CliCall:
    argv: list[str]
    output: str
    exit_code: int

    @property
    def command(self) -> str:
        from agents.harness.formats import command_of

        return command_of(self.argv)

    @property
    def code(self) -> str | None:
        """The §4.2 code of a rejected (or simulated-and-rejected) call."""
        body = self.json
        if self.exit_code == 2 and body.get("code"):
            return body["code"]
        would = (body.get("result") or {}).get("would") or body.get("would") or {}
        if isinstance(would, dict) and would.get("code"):  # `simulate` of a rejected write
            return would["code"]
        m = re.search(r"rejected (\w+):", self.output)  # --text form
        return m.group(1) if m else None

    @property
    def json(self) -> dict:
        with contextlib.suppress(ValueError):
            body = json.loads(self.output)
            if isinstance(body, dict):
                return body
        return {}


@dataclass
class Result:
    reply: str
    cli_calls: list[CliCall]
    exit_codes: list[int]
    cost: float
    agent: str = ""
    transcript: str = ""
    at: str = ""
    seconds: float = 0.0
    after: object = None
    fleet: list = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        """What a number in the reply may come from: this turn's CLI output and the transcript."""
        return [self.transcript, *(c.output for c in self.cli_calls)]

    @property
    def codes(self) -> list[str]:
        return [c.code for c in self.cli_calls if c.code]

    def calls(self, command: str) -> list[CliCall]:
        return [c for c in self.cli_calls if c.command == command]

    def dump(self) -> str:
        """For assertion messages."""
        calls = "\n".join(
            f"  airgrid {' '.join(c.argv)} -> exit {c.exit_code}: {c.output[:300].strip()}"
            for c in self.cli_calls
        )
        return (
            f"--- reply ({self.agent} at {self.at})\n{self.reply}\n--- calls\n{calls or '  none'}"
        )


def parse_cli_log(text: str) -> list[CliCall]:
    """The AIRGRID_CLI_LOG format of agents/bin/airgrid:
        >>> airgrid <args, shell-quoted>
        <stdout>
        <<< exit <code>
    A call the wrapper did not finish (killed turn) has exit code -1."""
    calls: list[CliCall] = []
    argv: list[str] | None = None
    out: list[str] = []

    def flush(code: int) -> None:
        nonlocal argv, out
        if argv is not None:
            calls.append(CliCall(argv, "\n".join(out).strip(), code))
        argv, out = None, []

    for line in text.splitlines():
        if line.startswith(">>> airgrid"):
            flush(-1)
            rest = line[len(">>> airgrid") :].strip()
            try:
                argv = [a for a in shlex.split(rest, posix=True) if a]
            except ValueError:  # $'...' forms of odd text: the command words are still first
                argv = rest.split()
        elif (m := re.fullmatch(r"<<< exit (-?\d+)", line)) and argv is not None:
            flush(int(m.group(1)))
        elif argv is not None:
            out.append(line)
    flush(-1)
    return calls


def find_bash() -> str:
    """Git Bash on Windows (a bare `bash` can be the WSL launcher in System32)."""
    git = shutil.which("git")
    if git:
        for up in Path(git).resolve().parents[:3]:
            candidate = up / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    if not found:
        raise HarnessError("bash not found on PATH")
    return found


def claude_argv() -> list[str]:
    binary = os.environ.get("AIRGRID_CLAUDE_BIN") or shutil.which("claude")
    if not binary:
        raise ClaudeError("claude not found on PATH")
    model = os.environ.get("AIRGRID_AGENT_MODEL") or DEFAULT_MODEL
    budget = os.environ.get("AIRGRID_AGENT_BUDGET_USD") or str(DEFAULT_BUDGET_USD)
    return [
        binary,
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "Bash",
        "--allowedTools",
        "Bash(airgrid:*)",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "project",  # the agent's own CLAUDE.md, nothing from the user's ~/.claude
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-budget-usd",
        budget,
    ]


def parse_claude_output(stdout: str, stderr: str, returncode: int) -> tuple[str, float]:
    """(reply, cost in USD). Anything but a clean result raises with claude's own words."""
    try:
        body = json.loads(stdout)
    except ValueError:
        tail = (stdout.strip() or stderr.strip())[-1000:]
        raise ClaudeError(f"claude -p exit {returncode}: {tail}") from None
    if not isinstance(body, dict):
        raise ClaudeError(f"claude -p exit {returncode}: unexpected output {stdout[-500:]}")
    if returncode != 0 or body.get("is_error") or body.get("subtype") != "success":
        said = body.get("result") or body.get("subtype") or stderr.strip()
        raise ClaudeError(f"claude -p exit {returncode}: {str(said)[-1000:]}")
    return str(body.get("result") or "").strip(), float(body.get("total_cost_usd") or 0.0)


def golden_plan(scenario: dict) -> list[tuple[float, int, dict]]:
    """The conductor's order (scripts/demo_run.py Conductor.run)."""
    actions = scenario.get("golden_actions") or []
    plan = [(tmin(scenario["start"]), -1, {"act": "standing_policy"})]
    plan += [(tmin(a["t"]), i, a) for i, a in enumerate(actions)]
    units = scenario.get("units_golden") or []
    plan += [(tmin(a["t"]), len(actions) + j, a) for j, a in enumerate(units)]
    return sorted(plan, key=lambda x: x[:2])


class Stage:
    """One sim, its tokens and a temp agent install, for one or more agent turns."""

    def __init__(self, scenario: str, work: Path):
        self.scenario_name = scenario
        self.scenario = load_scenario(scenario)
        self.work = work
        self.home = work / "agents-home"
        self.tokens_file = work / "tokens.yaml"
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.tokens: dict[str, str] = {}
        self.human_token = ""
        self.turns = 0
        self._sim: subprocess.Popen | None = None

    # --- lifecycle ---
    def __enter__(self) -> Stage:
        self.tokens = {a: secrets.token_urlsafe(24) for a in AGENTS}
        self.human_token = secrets.token_urlsafe(24)
        doc = {"agents": self.tokens, "humans": {HUMAN: self.human_token}}
        self.tokens_file.write_text(yaml.safe_dump(doc), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("AIRGRID_")}
        env |= {
            "AIRGRID_TOKENS": str(self.tokens_file),
            "AIRGRID_HUMANS": str(HUMANS_FILE),
            "AIRGRID_DB": str(self.work / "airgrid.db"),
        }
        self._sim = start_sim(self.port, env)
        try:
            self._wait_health()
            self._install()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_exc) -> None:
        if self._sim is not None:
            self._sim.terminate()
            self._sim.wait(timeout=20)
            self._sim = None

    def _wait_health(self) -> None:
        end = time.monotonic() + 60
        while time.monotonic() < end:
            if self._sim.poll() is not None:
                raise HarnessError(f"sim exited early with code {self._sim.returncode}")
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{self.url}/health", timeout=2).status_code == 200:
                    return
            time.sleep(0.1)
        raise HarnessError("sim did not answer /health within 60 s")

    def _install(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AIRGRID_")}
        env |= {
            "AIRGRID_AGENTS_HOME": str(self.home),
            "AIRGRID_API_URL": self.url,
            "AIRGRID_TOKENS": str(self.tokens_file),
        }
        if os.environ.get("AIRGRID_AGENT_TEMPLATES"):  # no-LLM harness tests use a fixture
            env["AIRGRID_AGENT_TEMPLATES"] = os.environ["AIRGRID_AGENT_TEMPLATES"]
        p = subprocess.run(
            [find_bash(), str(INSTALL)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        if p.returncode != 0:
            raise HarnessError(f"install_agents.sh exit {p.returncode}: {p.stderr.strip()[-800:]}")

    # --- the world before the turn ---
    @contextlib.asynccontextmanager
    async def live(self, on_post: Callable | None = None, out=None):
        """World Bot on FakeRoom over the real SSE stream, and the golden conductor."""
        sink_out = open(os.devnull, "w", encoding="utf-8") if out is None else None
        room = FakeRoom(interactive=False, out=out or sink_out)
        room._on_post = on_post
        api = httpx.AsyncClient(base_url=self.url, timeout=30)
        # demo mode, paused: mode "headless" would apply the golden actions inside the engine,
        # on top of the conductor. Nothing but /control/skip moves this clock.
        load = {"name": self.scenario_name, "mode": "demo"}
        (await api.post("/scenario/load", json=load)).raise_for_status()
        humans = yaml.safe_load(HUMANS_FILE.read_text(encoding="utf-8"))["humans"]
        roles = {h["discord"]: tuple(h["roles"]) for h in humans}
        bot = WorldBot(
            room,
            self.url,
            humans=roles,
            tokens={HUMAN: self.human_token},
            sink=FakeSink(self.work / "decisions.jsonl"),
        )
        runner = asyncio.create_task(bot.run())
        con = Conductor(api, room, bot, self.tokens, self.url)
        try:
            await wait_for(lambda: bot.pilots is not None or bot.last_seq == 0, "the bot")
            yield con, room
        finally:
            bot.stop()
            await runner
            await bot.aclose()
            await api.aclose()
            if sink_out:
                sink_out.close()

    async def play_until(self, con: Conductor, at: str) -> None:
        """Golden actions strictly before `at`, then stop at the actions slot of `at`."""
        target = tmin(at)
        for t, _i, a in golden_plan(self.scenario):
            if t >= target:
                break
            await con.to(t)
            await con.play(t, a)
        await con.to(target)

    def drive(self, at: str) -> None:
        async def go() -> None:
            async with self.live() as (con, _room):
                await self.play_until(con, at)

        asyncio.run(go())

    # --- harness-side calls (operator or forced checks; never part of an agent's turn) ---
    def cli(self, agent: str, *argv: str) -> CliCall:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AIRGRID_")}
        env |= {
            "AIRGRID_API_URL": self.url,
            "AIRGRID_ROLE": agent,
            "AIRGRID_TOKEN": self.tokens[agent],
        }
        p = subprocess.run(
            [sys.executable, "-m", "cli.airgrid", *argv],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=60,
        )
        return CliCall(list(argv), p.stdout.decode("utf-8", "replace").strip(), p.returncode)

    def get(self, path: str, **params) -> dict:
        r = httpx.get(f"{self.url}{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> httpx.Response:
        return httpx.post(f"{self.url}{path}", json=body, timeout=30)

    def post_human(self, path: str, body: dict) -> httpx.Response:
        head = {"Authorization": f"Bearer {self.human_token}"}
        return httpx.post(f"{self.url}{path}", json=body, headers=head, timeout=30)

    def skip(self, to: str) -> None:
        self.post("/control/skip", {"to": to, "stop": "actions"}).raise_for_status()

    # --- the turn ---
    def ask(self, agent: str, transcript: str, at: str = "") -> Result:
        """One `claude -p` turn from the agent's install dir. The prompt goes in on stdin: the
        transcript has emoji, pipes and newlines, which a .cmd shim would mangle as an argument."""
        self.turns += 1
        log = self.work / f"calls-{self.turns}-{agent}.log"
        log.touch()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AIRGRID_") and k not in NESTED_VARS + ISOLATION_VARS
        }
        env["PATH"] = str(self.home / "_bin") + os.pathsep + os.environ.get("PATH", "")
        env["AIRGRID_CLI_LOG"] = log.as_posix()
        started = time.monotonic()
        try:
            p = subprocess.run(
                claude_argv(),
                cwd=self.home / agent,
                env=env,
                input=transcript.encode("utf-8"),
                capture_output=True,
                timeout=TURN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"{agent}: claude -p timed out after {TURN_TIMEOUT_S} s") from None
        stdout = p.stdout.decode("utf-8", "replace")
        stderr = p.stderr.decode("utf-8", "replace")
        reply, cost = parse_claude_output(stdout, stderr, p.returncode)
        calls = parse_cli_log(log.read_text(encoding="utf-8", errors="replace"))
        return Result(
            reply=reply,
            cli_calls=calls,
            exit_codes=[c.exit_code for c in calls],
            cost=cost,
            agent=agent,
            transcript=transcript,
            at=at,
            seconds=time.monotonic() - started,
        )


@contextlib.contextmanager
def stage(scenario: str = "FRIDAY-RUSH"):
    with tempfile.TemporaryDirectory(prefix="airgrid-agent-", ignore_cleanup_errors=True) as tmp:
        with Stage(scenario, Path(tmp)) as s:
            yield s


def run_agent(
    agent: str,
    room_transcript: str | list[str],
    at: str,
    scenario: str = "FRIDAY-RUSH",
    setup: Callable[[Stage], object] | None = None,
    after: Callable[[Stage], object] | None = None,
) -> Result:
    """`setup(stage)` runs before the turn. `after(stage)` runs after it, while the sim is up, and
    its return value lands in `Result.after` (forced rejections, sim-side checks)."""
    if agent not in AGENTS:
        raise HarnessError(f"unknown agent {agent}")
    text = room_transcript if isinstance(room_transcript, str) else "\n".join(room_transcript)
    with stage(scenario) as s:
        s.drive(at)
        if setup:
            setup(s)
        result = s.ask(agent, text, at)
        if after:
            result.after = after(s)
    return result


__all__ = [
    "AGENTS",
    "ClaudeError",
    "CliCall",
    "HarnessError",
    "Result",
    "Stage",
    "parse_cli_log",
    "parse_claude_output",
    "run_agent",
    "stage",
]
