#!/usr/bin/env bash
# Usage: scripts/install_agents.sh
# Installs the ten runtime agents to AIRGRID_AGENTS_HOME (default ~/airgrid-agents):
#   <name>/CLAUDE.md, <name>/.env, and a shared _bin/ with the standalone CLI and its wrapper.
# Nothing else from the repo is copied (L11, L44). Validates everything before writing anything.
#
# Env: AIRGRID_AGENTS_HOME · AIRGRID_API_URL (default http://localhost:8740) ·
#      AIRGRID_TOKENS (default config/tokens.local.yaml) · AIRGRID_AGENT_TEMPLATES (default agents/)
# Tokens reach only the .env files and are never printed.
set -euo pipefail

if [ "$#" -ne 0 ]; then
    sed -n '2,8p' "$0" >&2
    exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv not found on PATH" >&2
    exit 1
fi

repo=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && { pwd -W 2>/dev/null || pwd -P; })
export AIRGRID_REPO="$repo"

read -r -d '' INSTALLER <<'PY' || true
import os
import shutil
import sys
from pathlib import Path

import yaml

AGENTS = (
    "dispatch", "fleet", "airspace", "intel", "relay", "privacy", "comms",
    "fire-station", "hospital", "police",
)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


repo = Path(os.environ["AIRGRID_REPO"]).resolve()
home_env = os.environ.get("AIRGRID_AGENTS_HOME")
home = Path(home_env).expanduser() if home_env else Path.home() / "airgrid-agents"
home = home.resolve()
templates = Path(os.environ.get("AIRGRID_AGENT_TEMPLATES") or repo / "agents")
tokens_file = Path(os.environ.get("AIRGRID_TOKENS") or repo / "config" / "tokens.local.yaml")
api_url = os.environ.get("AIRGRID_API_URL") or "http://localhost:8740"
cli = repo / "cli" / "airgrid.py"
wrapper = repo / "agents" / "bin" / "airgrid"

if home.is_relative_to(repo):
    die(f"agents home {home} is inside the repo; agents must not be able to read it (L11)")
if not api_url.isprintable() or " " in api_url:
    die("AIRGRID_API_URL is not a single URL")
for source in (cli, wrapper):
    if not source.is_file():
        die(f"missing {source}")

if not tokens_file.is_file():
    die(f"no tokens file at {tokens_file}; run scripts/make_tokens.sh or set AIRGRID_TOKENS")
try:
    doc = yaml.safe_load(tokens_file.read_text(encoding="utf-8"))
except (yaml.YAMLError, UnicodeDecodeError):
    # The parser's own message quotes the file, so it is not repeated here.
    die(f"tokens file {tokens_file} could not be parsed")
tokens = doc.get("agents") if isinstance(doc, dict) else None
tokens = tokens if isinstance(tokens, dict) else {}


def usable(value) -> bool:
    return isinstance(value, str) and value.isprintable() and value != "" and " " not in value


no_token = [a for a in AGENTS if not usable(tokens.get(a))]
if no_token:
    die(
        f"tokens file {tokens_file} has no usable agent token for: {', '.join(no_token)}; "
        "run scripts/make_tokens.sh --missing"
    )

no_template = [
    a
    for a in AGENTS
    if not (templates / a / "CLAUDE.md").is_file()
    or (templates / a / "CLAUDE.md").stat().st_size == 0
]
if no_template:
    wanted = ", ".join(f"{a}/CLAUDE.md" for a in no_template)
    die(f"missing or empty agent instructions in {templates}: {wanted}")

bin_dir = home / "_bin"
bin_dir.mkdir(parents=True, exist_ok=True)
shutil.copyfile(cli, bin_dir / "airgrid.py")
shutil.copyfile(wrapper, bin_dir / "airgrid")
shutil.copyfile(wrapper.with_suffix(".cmd"), bin_dir / "airgrid.cmd")
(bin_dir / "airgrid").chmod(0o755)

for name in AGENTS:
    target = home / name
    target.mkdir(exist_ok=True)
    shutil.copyfile(templates / name / "CLAUDE.md", target / "CLAUDE.md")
    env_file = target / ".env"
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"AIRGRID_API_URL={api_url}\n")
        fh.write(f"AIRGRID_ROLE={name}\n")
        fh.write(f"AIRGRID_TOKEN={tokens[name]}\n")
    print(f"installed {name}")

print(f"installed {len(AGENTS)} agents to {home}")
print(f"agents need {bin_dir} on PATH")
PY

exec uv run --quiet --no-project --python 3.12 --with pyyaml python -c "$INSTALLER"
