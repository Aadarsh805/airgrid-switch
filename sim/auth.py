"""Bearer tokens and human roles.

Tokens: AIRGRID_TOKENS (default config/tokens.local.yaml, gitignored)
    agents: {dispatch: <tok>, ...}
    humans: {kazum: <tok>}
Humans: AIRGRID_HUMANS (default config/humans.yaml, falling back to config/humans.example.yaml)

    python -m sim.auth --init [--out PATH] [--force]
"""

from __future__ import annotations

import argparse
import hmac
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
EXAMPLE_HUMANS = CONFIG / "humans.example.yaml"


@dataclass(frozen=True)
class Identity:
    kind: str  # "agent" | "human"
    name: str  # agent role name, or the human's discord name
    roles: tuple[str, ...]
    pilot: str | None = None  # world pilot id, when the display name isn't one (game mode)

    @property
    def is_human(self) -> bool:
        return self.kind == "human"

    @classmethod
    def agent(cls, role: str) -> Identity:
        return cls("agent", role, (role,))

    @classmethod
    def human(cls, name: str, roles) -> Identity:
        return cls("human", name, tuple(roles))


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def tokens_path() -> Path:
    return Path(os.environ.get("AIRGRID_TOKENS", str(CONFIG / "tokens.local.yaml")))


def humans_path() -> Path:
    env = os.environ.get("AIRGRID_HUMANS")
    if env:
        return Path(env)
    real = CONFIG / "humans.yaml"
    return real if real.exists() else EXAMPLE_HUMANS


def roles_config() -> dict:
    return _read_yaml(CONFIG / "roles.yaml")


def load_humans(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    data = _read_yaml(path or humans_path())
    return {h["discord"]: tuple(h.get("roles", [])) for h in data.get("humans") or []}


def load_identities(tokens: Path | None = None, humans: Path | None = None) -> dict:
    path = tokens or tokens_path()
    if not path.exists():
        return {}
    data = _read_yaml(path)
    roles = load_humans(humans)
    out = {}
    for role, tok in (data.get("agents") or {}).items():
        if tok:
            out[str(tok)] = Identity.agent(role)
    for name, tok in (data.get("humans") or {}).items():
        if tok:
            out[str(tok)] = Identity.human(name, roles.get(name, ()))
    return out


def identify(token: str | None, identities: dict | None = None) -> Identity | None:
    if not token:
        return None
    ids = load_identities() if identities is None else identities
    for tok, ident in ids.items():
        if hmac.compare_digest(tok, token):
            return ident
    return None


def bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def init_tokens(path: Path | None = None, force: bool = False) -> Path:
    path = path or tokens_path()
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists (use --force to overwrite)")
    agents = [a for a in roles_config()["agents"] if a != "any"]
    data = {
        "agents": {a: secrets.token_urlsafe(24) for a in agents},
        "humans": {h: secrets.token_urlsafe(24) for h in load_humans()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# AirGrid bearer tokens (generated; keep out of git)\n")
        yaml.safe_dump(data, fh, sort_keys=False)
    return path


def add_missing_tokens(path: Path | None = None) -> list[str]:
    """Give new roles a token and leave every existing one alone. Returns the roles added."""
    path = path or tokens_path()
    data = _read_yaml(path)
    agents = data.setdefault("agents", {})
    added = [a for a in roles_config()["agents"] if a != "any" and not agents.get(a)]
    agents.update({a: secrets.token_urlsafe(24) for a in added})
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# AirGrid bearer tokens (generated; keep out of git)\n")
        yaml.safe_dump(data, fh, sort_keys=False)
    return added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim.auth")
    ap.add_argument("--init", action="store_true", help="write a tokens file with random tokens")
    ap.add_argument("--out", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--missing", action="store_true", help="add tokens for new roles only")
    args = ap.parse_args(argv)
    if args.missing:
        added = add_missing_tokens(Path(args.out) if args.out else None)
        print(f"added tokens for: {', '.join(added) or 'nothing, all present'}")
        return 0
    if not args.init:
        ap.print_help()
        return 1
    try:
        p = init_tokens(Path(args.out) if args.out else None, force=args.force)
    except FileExistsError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
