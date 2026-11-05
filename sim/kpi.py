"""KPIs and scorecard (managed side). Per-tick accumulation mirrors the checker."""

from sim.geo import DT, fmt, hhmm


class KPI:
    def __init__(self, loops: list[str]):
        self.eyes_on: dict[str, float] = {}
        self.released_early = 0
        self.pilot_saturated = 0
        self.cover_minutes = {L: 0.0 for L in loops}
        self.ready_min: tuple[int, float | None] = (99, None)
        self.red_zone_entries = 0
        self.reserve_breaches = 0
        self.safety_rtbs = 0  # L36: counted, not a violation
        self._in_red: set[str] = set()
        self._below: set[str] = set()

    # --- hooks called by the engine ---
    def red_zone(self, did: str, inside: bool) -> None:
        if inside and did not in self._in_red:
            self.red_zone_entries += 1
        (self._in_red.add if inside else self._in_red.discard)(did)

    def below_reserve_airborne(self, did: str) -> None:
        if did not in self._below:
            self._below.add(did)
            self.reserve_breaches += 1

    def landed_below_reserve(self, did: str) -> None:
        if did not in self._below:
            self.reserve_breaches += 1
        self._below.discard(did)

    def tick(self, eng, t: float) -> None:
        nready = eng.ready_count()
        if nready < self.ready_min[0]:
            self.ready_min = (nready, t)
        for L in self.cover_minutes:
            if any(
                d.state == "airborne" and d.loop == L and d.mission == "patrol" and d.on_station
                for d in eng.drones.values()
            ):
                self.cover_minutes[L] += DT

    # --- reports ---
    def coverage_pct(self, eng) -> float:
        elapsed = max(0.0, eng.now - eng.T0) if eng.ticks else 0.0
        span = elapsed + DT if eng.ticks else 0.0
        if not span:
            return 0.0
        return sum(self.cover_minutes.values()) / (len(self.cover_minutes) * span) * 100

    def summary(self, eng) -> dict:
        first = eng.first_alert_incident
        eyes = None
        if first in eng.incidents and first in self.eyes_on:
            eyes = int(f"{(self.eyes_on[first] - eng.incidents[first]['t']) * 60:.0f}")
        landings = [d.min_landing for d in eng.drones.values() if d.min_landing <= 100]
        return {
            "eyes_on_inc1_s": eyes,
            "eyes_on_s": {
                k: int(f"{(v - eng.incidents[k]['t']) * 60:.0f}") for k, v in self.eyes_on.items()
            },
            "coverage_pct": round(self.coverage_pct(eng), 1),
            "coverage_by_loop_min": {k: int(f"{v:.0f}") for k, v in self.cover_minutes.items()},
            "flights": sum(d.flights for d in eng.drones.values()),
            "drones_flown": sum(1 for d in eng.drones.values() if d.flights),
            "lowest_landing_pct": round(min(landings), 1) if landings else None,
            "ready_floor_min": self.ready_min[0] if self.ready_min[1] is not None else None,
            "ready_floor_at": hhmm(self.ready_min[1]) if self.ready_min[1] is not None else None,
            "reserve_breaches": self.reserve_breaches,
            "red_zone_entries": self.red_zone_entries,
            "refusals": sorted(code for _t, code, _r in eng.refusals),
            "released_early": self.released_early,
            "unit_minutes_saved": round(eng.ground.saved_min, 1),
            "first_unit_on_scene_min": {
                k: round(v - eng.incidents[k]["t"], 1)
                for k, v in eng.ground.arrivals.items()
                if k in eng.incidents
            },
            "safety_rtbs": self.safety_rtbs,
        }

    def strip(self, eng) -> dict:
        """Small live KPI strip for /status (past-only data)."""
        airborne = sum(1 for d in eng.drones.values() if d.state == "airborne")
        ready = eng.ready_count()
        free = sum(k.free_slots() for k in eng.docks.values())
        total = sum(k.slots for k in eng.docks.values())
        s = self.summary(eng)
        return {
            "airborne": airborne,
            "ready": ready,
            "charging": sum(1 for d in eng.drones.values() if d.state == "docked") - ready,
            "slots_free": f"{free}/{total}",
            "coverage_pct": s["coverage_pct"],
            "eyes_on_s": s["eyes_on_s"],
            "flights": s["flights"],
            "reserve_breaches": s["reserve_breaches"],
            "red_zone_entries": s["red_zone_entries"],
            "refusals": len(eng.refusals),
            "released_early": s["released_early"],
            "unit_minutes_saved": s["unit_minutes_saved"],
            "safety_rtbs": s["safety_rtbs"],
            "ready_floor_min": s["ready_floor_min"],
        }

    def scorecard_lines(self, eng, base=None) -> list[str]:
        """The checker's scorecard block (managed vs baseline), line for line."""
        s = self.summary(eng)
        out = []
        for r in compare(eng, base) if base is not None else []:
            if r.get("line"):
                out.append(r["line"])
        if base is None:
            for inc, secs in s["eyes_on_s"].items():
                out.append(f"eyes-on {inc} ({eng.incidents[inc]['kind']}): {secs} s")
            out.append(f"ground units released early: {s['released_early']}")
            out.append(
                f"patrol junction coverage {fmt(eng.T0)}–{fmt(eng.T1)}: {s['coverage_pct']:.1f}%"
            )
        out.append(
            "coverage by loop (min): "
            + ", ".join(f"{k} {v:.0f}" for k, v in self.cover_minutes.items())
        )
        codes = sorted({c for _, c, _ in eng.refusals})
        out.append(f"refusals enforced: {len(eng.refusals)} → " + ", ".join(codes))
        low = s["lowest_landing_pct"]
        out.append(
            f"fleet: {s['drones_flown']}/{len(eng.drones)} drones flew, {s['flights']} flights, "
            f"lowest landing battery {f'{low:.1f}' if low is not None else '-'}% "
            f"(reserve {eng.M['reserve_pct']}%)"
        )
        if self.ready_min[1] is not None:
            n, at = self.ready_min
            out.append(f"ready floor: minimum {n} READY drones (at {fmt(at)})")
        return out


def _managed_outcome(eng, iid: str) -> tuple[str, bool, bool]:
    """(text, tracked, detained) for a suspect incident in the managed run."""
    inc = eng.incidents.get(iid)
    tracked = eng.approvals.auth(iid, "track") is not None
    if inc and tracked and inc.get("closed_t") is not None:
        return f"tracked, detained {fmt(inc['closed_t'])}", True, True
    return ("tracked" if tracked else "not tracked"), tracked, False


def compare(eng, base) -> list[dict]:
    """Managed vs baseline rows. `line` = the scorecard line (checker wording) if the row has
    one; `counted` rows decide "managed wins every line"; info rows are reported only (L35).
    Rows for incidents not alerted yet in the managed run are left out (no future leaks)."""
    s = eng.kpi.summary(eng)
    B = eng.world.raw["baseline"]
    rows: list[dict] = []
    seen = [a for a in base.alerts if a["incident"] in eng.incidents]
    first_eyes = True
    for a in seen:
        iid, kind = a["incident"], a["kind"]
        if kind in B["suspect_kinds"] and iid in base.suspect_lost:
            text, tracked, detained = _managed_outcome(eng, iid)
            lost = base.suspect_lost[iid]
            b_text = f"van eyes at {fmt(base.eyes_t[iid])} → suspect " + (
                f"LOST (outside {base.search_km:g} km search)" if lost else "found"
            )
            rows.append(
                {
                    "key": kind,
                    "label": f"{kind} outcome",
                    "managed": text,
                    "baseline": b_text,
                    "counted": True,
                    "win": detained or (tracked and lost),
                    "needs_final": True,  # depends on suspect positions after the alert
                    "line": f"{kind}: {text}  vs baseline {b_text}",
                }
            )
            continue
        secs = s["eyes_on_s"].get(iid)
        b_min = base.eyes_min[iid]
        rows.append(
            {
                "key": "eyes_on" if first_eyes else f"eyes_on:{iid}",
                "label": f"eyes-on {kind}",
                "managed": f"{secs} s" if secs is not None else "none",
                "baseline": f"{b_min:.1f} min",
                "counted": True,
                "win": secs is not None and secs < b_min * 60,
                "incident": iid,
                "line": f"eyes-on {kind}: {secs if secs is not None else '-'} s  "
                f"vs baseline van {b_min:.1f} min",
            }
        )
        first_eyes = False
    if seen:
        rows.append(
            {
                "key": "released_early",
                "label": "ground units released early",
                "managed": s["released_early"],
                "baseline": base.released_early,
                "counted": True,
                "win": s["released_early"] > base.released_early,
                "line": f"ground units released early: {s['released_early']} vs "
                f"{base.released_early}",
            }
        )
        rows.append(
            {
                "key": "unit_minutes_saved",
                "label": "unit-minutes saved by early release",
                "managed": s["unit_minutes_saved"],
                "baseline": base.unit_minutes_saved,
                "counted": True,
                "win": s["unit_minutes_saved"] > base.unit_minutes_saved,
            }
        )
    rows.append(
        {
            "key": "coverage",
            "label": "patrol junction coverage",
            "managed": f"{s['coverage_pct']:.1f}%",
            "baseline": f"~{base.coverage_frac * 100:.0f}%",
            "counted": True,
            "win": s["coverage_pct"] > base.coverage_frac * 100,
            "needs_final": True,  # depends on later alerts and weather
            "line": f"patrol junction coverage {fmt(eng.T0)}–{fmt(eng.T1)}: "
            f"{s['coverage_pct']:.1f}%  vs baseline ~{base.coverage_frac * 100:.0f}%",
        }
    )
    for key, label in (
        ("reserve_breaches", "reserve breaches"),
        ("red_zone_entries", "red-zone entries"),
    ):  # L37k: safety checks, not wins (managed must be 0, the van model has no such number)
        rows.append(
            {
                "key": key,
                "label": label,
                "managed": s[key],
                "baseline": "n/a",
                "counted": False,
                "win": None,
                "status": "ok" if s[key] == 0 else "fail",
            }
        )
    for a in seen[:1]:
        iid = a["incident"]
        m = eng.ground.arrivals.get(iid)
        b = base.first_unit_t.get(iid)

        def show(x, t0=a["t"]):
            return f"{fmt(x)} (+{x - t0:.1f} min)" if x is not None else "none"

        rows.append(
            {
                "key": "first_ground_unit",
                "label": f"first ground unit on scene, {a['kind']} (info: same unit model)",
                "managed": show(m),
                "baseline": show(b),
                "counted": False,
                "win": None,
            }
        )
    # scorecard order = the checker's: eyes-on, robbery, released, coverage, then the rest
    order = {"released_early": 2, "coverage": 3}
    rows.sort(key=lambda r: order.get(r["key"], 1 if r.get("line") else 4))
    return rows


def closed_incidents(eng) -> set[str]:
    return {k for k, v in eng.incidents.items() if v.get("status") == "closed"}


def kpi_view(eng) -> dict:
    """GET /kpi. Mid-shift the baseline serves nothing that depends on the future: incident
    figures only once that incident closes, the rest (`pending: true`) after the shift ends."""
    base = eng.baseline_result
    rows = compare(eng, base) if base is not None else []
    out = {
        "managed": eng.kpi.summary(eng),
        "baseline": None,
        "wins": {},
        "safety": {
            "reserve_breaches": "ok" if eng.kpi.reserve_breaches == 0 else "fail",
            "red_zone_entries": "ok" if eng.kpi.red_zone_entries == 0 else "fail",
        },
    }
    if base is None:
        return out
    closed = closed_incidents(eng)
    out["baseline"] = base.summary(visible=None if eng.done else closed, final=eng.done)
    for r in rows:
        if not r["counted"]:
            continue
        hidden = not eng.done and (
            r.get("needs_final") or ("incident" in r and r["incident"] not in closed)
        )
        out["wins"][r["key"]] = None if hidden else r["win"]
    return out
