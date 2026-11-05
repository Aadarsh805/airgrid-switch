"""Baseline: "the same Friday, no network" — patrol cars + ONE van drone team (L24, L35).

Ports the checker's baseline arithmetic (tools/fleet_check.py, "baseline" block) exactly; its
literals live in world.baseline. Generalised over the scenario's CAD alerts in order:
  first call:  depart = alert + pack_up (the van is flying the central loop)
  later calls: depart = max(alert, van free)
  eyes-on    = depart + Manhattan(van, scene) / road speed + van_setup + launch delay
  van free   = eyes + van_on_scene[kind] + pack_up   (kinds without on-scene time: van free at eyes)
  suspect calls: LOST if the suspect is farther than search_km from the alert point at eyes-on
  coverage   = max(0, shift − Σ(van free − alert) − swaps × swap_min − weather) / shift / loops
Ground units: the same unit model as the managed run (sim/ground.py); per call, the nearest
available unit of each kind in ground_units.dispatch[kind], tasked at the alert, no early release.

Everything here reads the whole scenario (future included), so it is internal: the API serves
only `summary()` numbers, filtered to incidents already alerted in the managed run.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from sim.geo import DT, EPS, dist, tmin
from sim.ground import GroundUnits, manhattan
from sim.suspect import SuspectPath


@dataclass
class Baseline:
    scenario: str
    alerts: list[dict] = field(default_factory=list)  # [{incident, kind, t}]
    eyes_t: dict[str, float] = field(default_factory=dict)
    eyes_min: dict[str, float] = field(default_factory=dict)
    suspect_lost: dict[str, bool] = field(default_factory=dict)
    coverage_frac: float = 0.0
    first_unit_t: dict[str, float | None] = field(default_factory=dict)
    released_early: int = 0
    unit_minutes_saved: float = 0.0
    search_km: float = 0.5

    def summary(self, visible: set[str] | None = None, final: bool = True) -> dict:
        """Final summary numbers only. `visible`: incidents that may be shown (None = all).
        `final=False` (shift still running) withholds whatever depends on the future."""

        def keep(iid):
            return visible is None or iid in visible

        return {
            "simulated": True,
            "model": "patrol cars + one van drone team (no network)",
            "eyes_on_min": {k: round(v, 1) for k, v in self.eyes_min.items() if keep(k)},
            "suspect_lost": (
                {k: v for k, v in self.suspect_lost.items() if keep(k)} if final else {}
            ),
            "first_unit_on_scene_min": {
                a["incident"]: (
                    round(self.first_unit_t[a["incident"]] - a["t"], 1)
                    if self.first_unit_t.get(a["incident"]) is not None
                    else None
                )
                for a in self.alerts
                if keep(a["incident"])
            },
            "coverage_pct": round(self.coverage_frac * 100, 1) if final else None,
            "pending": not final,
            "released_early": self.released_early,
            "unit_minutes_saved": self.unit_minutes_saved,
            "reserve_breaches": None,  # safety checks are managed-only (L37k)
            "red_zone_entries": None,
        }


def _weather_min(scenario: dict) -> float:
    return sum(
        tmin(e["until"]) - tmin(e["t"])
        for e in scenario.get("events") or []
        if e["type"] == "weather"
    )


def run(world, scenario: dict) -> Baseline:
    raw = world.raw
    B, M = raw["baseline"], raw["drone_model"]
    rs = raw["ground_units"]["road_speed_km_per_min"]
    T0, T1 = tmin(scenario["start"]), tmin(scenario["end"])
    shift = T1 - T0
    alerts = sorted(
        (tmin(e["t"]), i, e)
        for i, e in enumerate(scenario.get("events") or [])
        if e["type"] == "cad_alert"
    )
    suspect = SuspectPath(scenario.get("suspect_path") or [])
    out = Baseline(scenario["name"], search_km=B["search_km"])

    # ---- the van (checker arithmetic) ----
    van_at = list(B["van_park"])
    van_free = None  # None = still flying the central loop (must pack up first)
    lost_min = 0.0
    for t, _i, e in alerts:
        iid, kind = e["incident"], e["kind"]
        scene = world.point(e["at"])
        out.alerts.append({"incident": iid, "kind": kind, "t": t})
        depart = t + B["pack_up_min"] if van_free is None else max(t, van_free)
        eyes = depart + manhattan(van_at, scene) / rs + B["van_setup_min"] + M["launch_delay_min"]
        out.eyes_t[iid] = eyes
        out.eyes_min[iid] = eyes - t
        on_scene = B["van_on_scene_min"].get(kind, 0)
        van_free = eyes + on_scene + (B["pack_up_min"] if on_scene else 0)
        lost_min += van_free - t
        van_at = list(scene)
        if kind in B["suspect_kinds"] and suspect:
            out.suspect_lost[iid] = dist(suspect.pos(eyes), scene) > B["search_km"]
    weather = _weather_min(scenario) if B["weather_min"] == "scenario" else B["weather_min"]
    swaps = int(shift // B["swap_every_min"])
    loops = len(raw["patrol_loops"])
    out.coverage_frac = max(0.0, shift - lost_min - swaps * B["swap_min"] - weather) / shift / loops

    # ---- ground units: same model, per call, nearest, no early release ----
    g = GroundUnits(raw)
    dispatch = raw["ground_units"]["dispatch"]
    queue = [(t, e) for t, _i, e in alerts]
    k, tick = 0, T0
    while tick <= T1 + EPS:
        while k < len(queue) and queue[k][0] <= tick + EPS:
            _t, e = queue[k]
            scene = world.point(e["at"])
            used: list[str] = []
            for ukind in dispatch.get(e["kind"], dispatch["default"]):
                u = g.nearest_available(ukind, scene, exclude=used)
                if u is not None:
                    g.task(tick, u.id, e["incident"], scene, e["kind"])
                    used.append(u.id)
            k += 1
        g.tick(tick)
        tick = round(tick + DT, 4)
    out.first_unit_t = {a["incident"]: g.arrivals.get(a["incident"]) for a in out.alerts}
    return out


# ---------------------------------------------------------------- CLI (scripts/run_baseline.sh)
def main(argv: list[str] | None = None) -> int:
    from sim import golden

    ap = argparse.ArgumentParser(prog="python -m sim.baseline")
    ap.add_argument("scenario", nargs="?", default="FRIDAY-RUSH")
    args = ap.parse_args(argv)
    g = golden.run(args.scenario)
    print(f"=== {g.title.upper()} — managed vs baseline (simulated) ===")
    for line in g.scorecard:
        print(line)
    print()
    counted = [r for r in g.compare if r["counted"]]
    for r in g.compare:
        if r["counted"]:
            tag = "WIN " if r["win"] else "LOSS"
        else:
            tag = {"ok": "ok  ", "fail": "FAIL"}.get(r.get("status"), "info")
        print(f"[{tag}] {r['label']}: managed {r['managed']}  vs baseline {r['baseline']}")
    wins = sum(1 for r in counted if r["win"])
    print(f"\nmanaged wins {wins}/{len(counted)} counted lines · {len(g.violations)} violations")
    safe = not any(r.get("status") == "fail" for r in g.compare)
    ok = counted and wins == len(counted) and safe and not g.violations
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
