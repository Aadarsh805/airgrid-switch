"""Register the World Bot with Switch, once. Stores its id and key in config/switch.local.yaml.

    AIRGRID_SWITCH_REG_KEY=<registration key> uv run python scripts/register_worldbot.py \
        [--url http://localhost:56020] [--overwrite]

The registration key comes only from the environment. Neither key is ever printed. --overwrite
issues a new API key, and the old one stops working.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import httpx
import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "switch.local.yaml"
DEFAULT_URL = "http://localhost:56020"
NAME = "airgrid-worldbot"
# Addressing is not the bot's authority: approvals authorise on the mapped sender id. `anyone`
# only stops Switch posting a refusal when a teammate @mentions the bot.
ADDRESSING = "anyone"
TIMEOUT_S = 120


def die(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def probe(client: httpx.Client, url: str) -> str | None:
    """None when `url` is the bridge; otherwise why it isn't."""
    try:
        r = client.get(f"{url}/health")
    except httpx.HTTPError as e:
        return f"nothing answered at {url}: {type(e).__name__}"
    if r.status_code == 401:
        return f"{url} is the right host but the wrong path; find the bridge base URL"
    if r.status_code != 200:
        return f"{url}/health answered HTTP {r.status_code}; that is not the bridge"
    return None


def clean(text: str, *secrets: str) -> str:
    for s in secrets:
        text = text.replace(s, "***")
    return text[:300]


def write_config(path: Path, doc: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def register(url: str, reg_key: str, overwrite: bool, path: Path) -> int:
    with httpx.Client(timeout=TIMEOUT_S) as client:
        problem = probe(client, url)
        if problem:
            return die(problem)
        body = {
            "name": NAME,
            "description": "AirGrid World Bot: plain code, no LLM. Voices the simulator.",
            "connector_type": "custom",
            "integration_profile": {
                "connection_model": "always_on",
                "message_exchange": True,
                "pre_invocation_mediation": [],
                "post_invocation_mediation": [],
                "event_reporting": [],
            },
        }
        if overwrite:
            body["overwrite"] = True
        try:
            r = client.post(
                f"{url}/agents", json=body, headers={"Authorization": f"Bearer {reg_key}"}
            )
        except httpx.HTTPError as e:
            return die(f"POST /agents failed: {type(e).__name__}")
        if r.status_code == 409:
            return die(
                f"{NAME} already exists. Its key can't be read again: --overwrite issues "
                "a new one (a name another person owns stays refused)"
            )
        if r.status_code in (401, 403):
            return die(
                f"Switch refused the key (HTTP {r.status_code}). It must be an API key of "
                "type registration from the Gateway's API Keys page"
            )
        if r.status_code >= 300:
            return die(f"POST /agents: HTTP {r.status_code}: {clean(r.text, reg_key)}")
        out = r.json()
        agent_id, api_key = out["id"], out["api_key"]
        # Stored before anything else can fail: the key is shown only once.
        write_config(
            path,
            {
                "base_url": url,
                "agent_id": agent_id,
                "api_key": api_key,
                "connection_id": str(uuid.uuid4()),
                "name": NAME,
            },
        )
        print(f"registered {NAME} as {agent_id}; stored in {path.name}")
        try:
            r = client.post(
                f"{url}/agents/{agent_id}/ops/update_agent_detail",
                json={"agent_id": agent_id, "addressing": ADDRESSING},
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as e:
            return die(
                f"registered, but addressing not set: {type(e).__name__}. Rerun with "
                "--overwrite, or set it in the agent's settings"
            )
        if r.status_code != 200:
            detail = clean(r.text, reg_key, api_key)
            return die(f"registered, but addressing not set: HTTP {r.status_code}: {detail}")
        print(f"addressing: {ADDRESSING}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--url", default=DEFAULT_URL, help=f"bridge base URL (default {DEFAULT_URL})")
    ap.add_argument("--overwrite", action="store_true", help="issue a new API key")
    args = ap.parse_args(argv)
    reg_key = os.environ.get("AIRGRID_SWITCH_REG_KEY", "")
    if not reg_key:
        return die("set AIRGRID_SWITCH_REG_KEY to the registration key")
    path = Path(os.environ.get("AIRGRID_SWITCH_CONFIG") or CONFIG)
    if path.exists() and not args.overwrite:
        return die(f"{path.name} exists; this bot is registered. --overwrite issues a new key")
    return register(args.url.rstrip("/"), reg_key, args.overwrite, path)


if __name__ == "__main__":
    raise SystemExit(main())
