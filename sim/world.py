"""Load and validate world/airgrid.yaml (canonical; never mutated)."""

import copy
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORLD_PATH = ROOT / "world" / "airgrid.yaml"

REQUIRED = (
    "district",
    "zones",
    "docks",
    "drone_model",
    "fleet",
    "patrol_loops",
    "pilots",
    "policies",
    "city",
)
POLICY_KEYS = (
    "auto_launch_triage_min",
    "suspect_view_km",
    "auto_launch_kinds",
    "thermal_penalty_km",
    "relay_lookahead_min",
    "relay_handoff_km",
    "safety_rtb_margin_pct",
    "slot_log_radius_km",
    "geofence_km",
    "separation_km",
    "ready_floor",
    "fleet_status_every_min",
    "intrusion_hold_min",
    "search_pair_offset_km",
    "incident_priority",
    "game_catch_km",
    "game_fly_sim_min_per_s",
    "game_score",
    "game_clock",
)


class WorldError(ValueError):
    pass


@dataclass
class World:
    raw: dict

    @property
    def model(self) -> dict:
        return self.raw["drone_model"]

    @property
    def landmarks(self) -> dict:
        return self.raw["district"]["landmarks"]

    @property
    def policies(self) -> dict:
        return self.raw["policies"]

    def point(self, p):
        """Landmark name or [x, y] -> [x, y]."""
        return self.landmarks[p] if isinstance(p, str) else p

    def geometry(self) -> dict:
        """Static geometry only (served by GET /world). No scenario data."""
        d = self.raw["district"]
        return copy.deepcopy(
            {
                "simulated": True,
                "district": {"name": d["name"], "size_km": d["size_km"]},
                "sectors": d["sectors"],
                "junctions": d["junctions"],
                "landmarks": d["landmarks"],
                "zones": self.raw["zones"],
                "docks": {
                    k: {"kind": v["kind"], "at": v["at"], "slots": v["slots"]}
                    for k, v in self.raw["docks"].items()
                },
                "loops": self.raw["patrol_loops"],
            }
        )


def validate(raw: dict) -> None:
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise WorldError(f"world missing keys: {missing}")
    for did, f in raw["fleet"].items():
        if f["home"] not in raw["docks"]:
            raise WorldError(f"{did} home {f['home']} is not a dock")
    missing_pol = [k for k in POLICY_KEYS if k not in (raw["policies"] or {})]
    if missing_pol:
        raise WorldError(f"policies missing keys: {missing_pol}")
    for L, loop in raw["patrol_loops"].items():
        for p in loop.get("pilot_prefer", []):
            if p not in raw["pilots"]:
                raise WorldError(f"loop {L} pilot_prefer {p} is not a pilot")
        for s in loop.get("sectors", []):
            if s not in raw["district"]["sectors"]:
                raise WorldError(f"loop {L} sector {s} unknown")
    if sum(1 for z in raw["zones"] if z["kind"] == "red") != 1:
        raise WorldError("exactly one red zone expected")
    for p in raw["pilots"].values():
        if p.get("kind") not in ("human", "simulated"):
            raise WorldError("pilot kind must be human or simulated (no auto pool)")


def load_world(path: Path | str = WORLD_PATH) -> World:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    validate(raw)
    return World(raw)
