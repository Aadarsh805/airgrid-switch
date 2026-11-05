"""Local Switch only: make a registration key and register the World Bot. kazum runs this.

A local server Switch Console runs natively has no Gateway web page, so API Keys has no screen
(L50). This reads the admin login from the server's own env file on this machine, signs in,
creates a key labelled `airgrid-worldbot`, and runs scripts/register_worldbot.py with it in the
environment. Nothing secret is printed, stored or passed on a command line.

    uv run python scripts/bootstrap_worldbot.py [--overwrite]

--overwrite re-issues the bot's key if a first run lost it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx

URL = "http://localhost:56020"
ENV_RELATIVE = "switchdash/local-switch-server/native/run/switch-core.env"
REGISTER = Path(__file__).resolve().parent / "register_worldbot.py"
LABEL = "airgrid-worldbot"


def read_env(path: Path) -> dict[str, str]:
    pairs = (ln.split("=", 1) for ln in path.read_text(encoding="utf-8").splitlines() if "=" in ln)
    return {k.strip(): v.strip().strip("'\"") for k, v in pairs}


def main() -> int:
    appdata = os.environ.get("APPDATA")
    env_file = Path(appdata) / ENV_RELATIVE if appdata else None
    if env_file is None or not env_file.exists():
        print("error: no local Switch env file; is this the machine running Switch?")
        return 1
    env = read_env(env_file)
    login = {"email": env.get("GATEWAY_ADMIN_EMAIL"), "password": env.get("GATEWAY_ADMIN_PASSWORD")}
    if not all(login.values()):
        print("error: the env file has no admin login")
        return 1
    with httpx.Client(base_url=URL, timeout=15) as c:
        r = c.post("/gateway/auth/login", json=login)
        if r.status_code != 200:
            print(f"error: sign-in failed, HTTP {r.status_code}")
            return 1
        r = c.post("/gateway/api-keys", json={"label": LABEL})
        if r.status_code != 200:
            print(f"error: creating the key failed, HTTP {r.status_code}")
            return 1
        key = r.json()["key"]
    child = {**os.environ, "AIRGRID_SWITCH_REG_KEY": key}
    cmd = [sys.executable, str(REGISTER), "--url", URL, *sys.argv[1:]]
    return subprocess.run(cmd, env=child).returncode


if __name__ == "__main__":
    raise SystemExit(main())
