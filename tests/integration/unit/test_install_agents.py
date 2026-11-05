"""scripts/install_agents.sh and the agents/bin/airgrid wrapper: isolation from the repo (L11, L44).

Templates are a fixture, so this does not depend on the real agents/<name>/CLAUDE.md. The wrapper
runs as an agent runs it: real bash, cwd = the agent's directory, no AIRGRID_* in the environment.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.integration.harness import base_env, dead_url
from tests.sim.helpers import ROOT, TOKENS_DOC

AGENTS = tuple(TOKENS_DOC["agents"])
INSTALL = ROOT / "scripts" / "install_agents.sh"
ALL_TOKENS = [*TOKENS_DOC["agents"].values(), *TOKENS_DOC["humans"].values()]
TIMEOUT_S = 180


def bash() -> str:
    """Git Bash on Windows (a bare `bash` can be the WSL launcher in System32)."""
    git = shutil.which("git")
    if git:
        for up in Path(git).resolve().parents[:3]:
            candidate = up / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    found = shutil.which("bash")
    if not found:
        pytest.skip("bash not found")
    return found


def run(cmd, cwd, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env={**base_env(), **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
    )


def install(env: dict, cwd=ROOT) -> subprocess.CompletedProcess:
    return run([bash(), str(INSTALL)], cwd, env)


def write_templates(root: Path) -> Path:
    for name in AGENTS:
        d = root / name
        d.mkdir(parents=True)
        (d / "CLAUDE.md").write_text(f"# {name} fixture\n", encoding="utf-8")
        (d / ".env.example").write_text("AIRGRID_TOKEN=\n", encoding="utf-8")
        (d / "notes.txt").write_text("must not be copied\n", encoding="utf-8")
    return root


def write_tokens(path: Path) -> Path:
    path.write_text(yaml.safe_dump(TOKENS_DOC), encoding="utf-8")
    return path


@pytest.fixture
def setup(tmp_path) -> dict:
    return {
        "AIRGRID_AGENTS_HOME": str(tmp_path / "home"),
        "AIRGRID_AGENT_TEMPLATES": str(write_templates(tmp_path / "templates")),
        "AIRGRID_TOKENS": str(write_tokens(tmp_path / "tokens.yaml")),
    }


@pytest.fixture(scope="module")
def installed(tmp_path_factory, sim) -> Path:
    tmp = tmp_path_factory.mktemp("installed")
    r = install(
        {
            "AIRGRID_AGENTS_HOME": str(tmp / "home"),
            "AIRGRID_AGENT_TEMPLATES": str(write_templates(tmp / "templates")),
            "AIRGRID_TOKENS": str(write_tokens(tmp / "tokens.yaml")),
            "AIRGRID_API_URL": sim.url,
        }
    )
    assert r.returncode == 0, r.stderr
    return tmp / "home"


def wrapper(home: Path, agent: str, *args, env=None) -> subprocess.CompletedProcess:
    return run([bash(), str(home / "_bin" / "airgrid"), *args], home / agent, env)


def files_under(home: Path) -> set[str]:
    return {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- what lands on disk
def test_tree_is_exactly_the_instructions_env_and_bin(setup):
    r = install(setup)
    assert r.returncode == 0, r.stderr
    want = {"_bin/airgrid", "_bin/airgrid.cmd", "_bin/airgrid.py"}
    want |= {f"{a}/{f}" for a in AGENTS for f in ("CLAUDE.md", ".env")}
    assert files_under(Path(setup["AIRGRID_AGENTS_HOME"])) == want


def test_nothing_from_the_repo_data_or_code_is_copied(setup):
    assert install(setup).returncode == 0
    home = Path(setup["AIRGRID_AGENTS_HOME"])
    forbidden = {"world", "scenarios", "tools", "docs", "sim", "worldbot", "config"}
    assert not forbidden & {p.name for p in home.rglob("*") if p.is_dir()}

    repo_files = {}
    for folder in ("world", "scenarios", "tools", "docs", "sim"):
        for p in (ROOT / folder).rglob("*"):
            if p.is_file() and p.stat().st_size:
                repo_files[p.read_bytes()] = p
    for p in home.rglob("*"):
        if p.is_file():
            assert p.read_bytes() not in repo_files, (
                f"{p} is a copy of {repo_files[p.read_bytes()]}"
            )


def test_standalone_cli_imports_nothing_from_the_repo(setup):
    assert install(setup).returncode == 0
    src = (Path(setup["AIRGRID_AGENTS_HOME"]) / "_bin" / "airgrid.py").read_text(encoding="utf-8")
    assert src == (ROOT / "cli" / "airgrid.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if ln.split()[1].split(".")[0] in ("sim", "cli", "worldbot")]


def test_each_env_has_its_own_role_and_token_and_nothing_else(setup):
    assert install({**setup, "AIRGRID_API_URL": "http://10.0.0.5:8740"}).returncode == 0
    home = Path(setup["AIRGRID_AGENTS_HOME"])
    for a in AGENTS:
        assert (home / a / ".env").read_bytes().decode().splitlines() == [
            "AIRGRID_API_URL=http://10.0.0.5:8740",
            f"AIRGRID_ROLE={a}",
            f"AIRGRID_TOKEN={TOKENS_DOC['agents'][a]}",
        ]


def test_tokens_land_only_in_env_files_and_are_never_printed(setup):
    r = install(setup)
    assert r.returncode == 0
    for t in ALL_TOKENS:
        assert t not in r.stdout + r.stderr
    home = Path(setup["AIRGRID_AGENTS_HOME"])
    for rel in files_under(home):
        if rel.endswith("/.env"):
            continue
        text = (home / rel).read_text(encoding="utf-8")
        assert not [t for t in ALL_TOKENS if t in text], rel
    for t in TOKENS_DOC["humans"].values():
        for a in AGENTS:
            assert t not in (home / a / ".env").read_text(encoding="utf-8")


def test_reinstall_picks_up_a_rotated_token(setup):
    assert install(setup).returncode == 0
    doc = json.loads(json.dumps(TOKENS_DOC))
    doc["agents"]["fleet"] = "rotated-token"
    Path(setup["AIRGRID_TOKENS"]).write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert install(setup).returncode == 0
    env = (Path(setup["AIRGRID_AGENTS_HOME"]) / "fleet" / ".env").read_text(encoding="utf-8")
    assert "AIRGRID_TOKEN=rotated-token\n" in env


# ---------------------------------------------------------------- refusals
def test_missing_claude_md_names_the_agent_and_writes_nothing(setup):
    os.remove(Path(setup["AIRGRID_AGENT_TEMPLATES"]) / "relay" / "CLAUDE.md")
    r = install(setup)
    assert r.returncode == 1
    assert "relay/CLAUDE.md" in r.stderr and "dispatch" not in r.stderr
    assert not Path(setup["AIRGRID_AGENTS_HOME"]).exists()


def test_missing_tokens_file_points_at_make_tokens(setup, tmp_path):
    r = install({**setup, "AIRGRID_TOKENS": str(tmp_path / "nope.yaml")})
    assert r.returncode == 1
    assert "make_tokens.sh" in r.stderr
    assert not Path(setup["AIRGRID_AGENTS_HOME"]).exists()


def test_token_missing_for_one_role_names_only_the_role(setup):
    doc = json.loads(json.dumps(TOKENS_DOC))
    del doc["agents"]["intel"]
    Path(setup["AIRGRID_TOKENS"]).write_text(yaml.safe_dump(doc), encoding="utf-8")
    r = install(setup)
    assert r.returncode == 1 and "intel" in r.stderr
    assert not [t for t in ALL_TOKENS if t in r.stdout + r.stderr]
    assert not Path(setup["AIRGRID_AGENTS_HOME"]).exists()


def test_unparsable_tokens_file_does_not_echo_its_content(setup):
    secret = "s3cret-value-from-a-broken-file"
    Path(setup["AIRGRID_TOKENS"]).write_text(f"agents: [{secret}\n  : :\n", encoding="utf-8")
    r = install(setup)
    assert r.returncode == 1 and "could not be parsed" in r.stderr
    assert secret not in r.stdout + r.stderr


def test_home_inside_the_repo_is_refused(setup):
    home = ROOT / "runs" / "agents-home-must-not-exist"
    r = install({**setup, "AIRGRID_AGENTS_HOME": str(home)})
    assert r.returncode == 1 and "inside the repo" in r.stderr
    assert not home.exists()


# ---------------------------------------------------------------- the wrapper
def test_status_through_the_wrapper_against_a_live_sim(installed):
    r = wrapper(installed, "dispatch", "status")
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""
    body = json.loads(r.stdout)
    assert body["ok"] is True and "clock" in body


def test_wrapper_exit_code_2_and_identity_from_env(installed):
    r = wrapper(installed, "relay", "rtb", "--drone", "D-01")
    assert r.returncode == 2, r.stdout + r.stderr
    assert json.loads(r.stdout)["code"] == "FORBIDDEN_ROLE"


def test_env_file_beats_an_inherited_identity(installed):
    env = {"AIRGRID_ROLE": "fleet", "AIRGRID_TOKEN": TOKENS_DOC["agents"]["fleet"]}
    r = wrapper(installed, "relay", "rtb", "--drone", "D-01", env=env)
    assert r.returncode == 2
    assert json.loads(r.stdout)["code"] == "FORBIDDEN_ROLE"


def test_wrapper_exit_code_1_never_shows_the_token(installed, tmp_path):
    home = tmp_path / "home"
    shutil.copytree(installed, home)
    env_file = home / "dispatch" / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines()
    lines[0] = f"AIRGRID_API_URL={dead_url()}"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = wrapper(home, "dispatch", "status")
    assert r.returncode == 1
    assert not [t for t in ALL_TOKENS if t in r.stdout + r.stderr]


def test_repo_is_not_importable_even_from_a_hostile_environment(installed, tmp_path):
    """The real wrapper runs a probe in the CLI's place, with the repo pushed at it."""
    home = tmp_path / "home"
    shutil.copytree(installed, home)
    (home / "_bin" / "airgrid.py").write_text(
        "import importlib.util, json, sys\n"
        "print(json.dumps({\n"
        "    'found': [m for m in ('cli', 'sim', 'worldbot', 'tools')\n"
        "              if importlib.util.find_spec(m)],\n"
        "    'path': sys.path, 'argv': sys.argv[1:], 'version': sys.version_info[:2],\n"
        "}))\n",
        encoding="utf-8",
    )
    hostile = {"PYTHONPATH": str(ROOT), "VIRTUAL_ENV": str(ROOT / ".venv")}
    r = wrapper(home, "dispatch", "--why", "/a/b", env=hostile)
    assert r.returncode == 0, r.stderr
    probe = json.loads(r.stdout)
    assert probe["found"] == []
    assert not [p for p in probe["path"] if Path(p).is_relative_to(ROOT)]
    assert probe["argv"] == ["--why", "/a/b"], "Git Bash rewrote an agent's argument"
    assert probe["version"] == [3, 12]


def test_wrapper_log_for_the_harness(installed, tmp_path):
    log = tmp_path / "calls.log"
    r = wrapper(installed, "relay", "rtb", "--drone", "D-01", env={"AIRGRID_CLI_LOG": str(log)})
    assert r.returncode == 2
    text = log.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == ">>> airgrid rtb --drone D-01"
    assert json.loads(lines[1])["code"] == "FORBIDDEN_ROLE"
    assert lines[2] == "<<< exit 2"
    assert not [t for t in ALL_TOKENS if t in text]


def test_wrapper_without_a_cli_copy_says_so(tmp_path):
    shutil.copy(ROOT / "agents" / "bin" / "airgrid", tmp_path / "airgrid")
    r = run([bash(), str(tmp_path / "airgrid"), "status"], tmp_path)
    assert r.returncode == 1 and "install_agents.sh" in r.stderr
