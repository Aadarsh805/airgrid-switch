"""Scenario loading (scenarios/*.yaml, canonical). Lookup by `name` (FRIDAY-RUSH) or file stem."""

from pathlib import Path

import yaml

from sim.geo import tmin

ROOT = Path(__file__).resolve().parent.parent
SCEN_DIR = ROOT / "scenarios"
EVENT_TYPES = {
    "shift_brief",
    "cad_alert",
    "dock_fault",
    "dock_restored",
    "weather",
    "unit_radio",
    "trigger",
}


class ScenarioNotFound(KeyError):
    pass


def _read(p: Path) -> dict:
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def available() -> dict[str, Path]:
    out = {}
    for p in sorted(SCEN_DIR.glob("*.yaml")):
        data = _read(p)
        if isinstance(data, dict) and "name" in data and "initial" in data:
            out[data["name"]] = p
    return out


def load_scenario(name: str) -> dict:
    scen = available()
    if name in scen:
        path = scen[name]
    else:
        key = name.lower().replace("-", "_")
        path = SCEN_DIR / f"{key}.yaml"
        if not path.exists() or path.stem not in {p.stem for p in scen.values()}:
            raise ScenarioNotFound(name)
    data = _read(path)
    validate(data)
    return data


def load_triggers() -> dict:
    """Live trigger menu (scenarios/triggers.yaml)."""
    return _read(SCEN_DIR / "triggers.yaml")


def validate(s: dict) -> None:
    for k in ("name", "start", "end", "initial"):
        if k not in s:
            raise ValueError(f"scenario missing {k}")
    for e in s.get("events") or []:
        if e["type"] not in EVENT_TYPES:
            raise ValueError(f"unknown event type {e['type']}")
        tmin(e["t"])
    for a in s.get("golden_actions") or []:
        tmin(a["t"])
