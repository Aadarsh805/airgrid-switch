"""Build the dashboard's replay fixtures (simulated data only).

    bash scripts/run_golden.sh FRIDAY-RUSH
    uv run python dashboard/tools/make_fixture.py

Writes to dashboard/fixtures/:
  golden.jsonl       verbatim copy of the newest runs/golden-*.jsonl (lean frames + log)
  golden.wire.jsonl  the same golden run recorded as the API would serve it, one frame per line
  world.json         GET /world snapshot

The lean file has no incidents, KPIs or privacy rows, so the page prefers the wire recording and
falls back to the lean frames. Frames are the /events/stream shapes plus the polled endpoints.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sim import baseline  # noqa: E402
from sim.app import state_diff  # noqa: E402
from sim.engine import Engine  # noqa: E402
from sim.kpi import kpi_view  # noqa: E402

OUT = ROOT / "dashboard" / "fixtures"
FRAME_EVERY = 5  # tenths of a sim-min (matches the lean frames); ticks with events get one too
KPI_EVERY = 100  # tenths of a sim-min


def dump(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def record(path: Path) -> dict[float, dict]:
    eng = Engine.load("FRIDAY-RUSH", mode="headless", golden=True)
    eng.baseline_result = baseline.run(eng.world, eng.scenario)
    prev = eng.state_view()
    rows = [
        {"f": "snapshot", "t": prev["t"], "d": prev},
        {"f": "kpi", "t": prev["t"], "d": kpi_view(eng)},
    ]
    seq = privacy_seq = 0
    approvals, landing = "", ""
    while not eng.done:
        eng.step()
        tenths = round(eng.now * 10)
        new_events = eng.log.since(seq=seq)
        new_privacy = [r for r in eng.privacy.rows() if r["seq"] > privacy_seq]
        now_approvals = dump([a.view() for a in eng.approvals.items.values()])
        now_landing = dump({d.id: eng.landing(d) for d in eng.drones.values() if eng.landing(d)})
        if not (new_events or new_privacy or tenths % FRAME_EVERY == 0 or eng.done):
            continue
        cur = eng.state_view()
        t = cur["t"]
        rows.extend({"f": "event", "t": t, "d": e.to_dict()} for e in new_events)
        rows.extend({"f": "privacy", "t": t, "d": r} for r in new_privacy)
        if new_events:
            seq = new_events[-1].seq
        if new_privacy:
            privacy_seq = new_privacy[-1]["seq"]
        if now_approvals != approvals:
            approvals = now_approvals
            rows.append({"f": "approvals", "t": t, "d": json.loads(now_approvals)})
        if now_landing != landing:
            landing = now_landing
            rows.append({"f": "landing", "t": t, "d": json.loads(now_landing)})
        rows.append({"f": "tick", "t": t, "d": state_diff(prev, cur)})
        prev = cur
        if tenths % KPI_EVERY == 0 or eng.done:
            rows.append({"f": "kpi", "t": t, "d": kpi_view(eng)})
    path.write_text("\n".join(dump(r) for r in rows) + "\n", encoding="utf-8", newline="\n")
    assert not eng.violations, eng.violations
    return _replayed(rows)


def _replayed(rows: list[dict]) -> dict[float, dict]:
    drones: dict[str, dict] = {}
    out = {}
    for r in rows:
        if r["f"] == "snapshot":
            drones = dict(r["d"]["drones"])
        elif r["f"] == "tick":
            drones.update(r["d"].get("drones", {}))
            out[r["t"]] = {k: v["pos"] for k, v in drones.items()}
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    golden = sorted((ROOT / "runs").glob("golden-*.jsonl"))
    if not golden:
        print("no runs/golden-*.jsonl: run scripts/run_golden.sh FRIDAY-RUSH first")
        return 1
    shutil.copyfile(golden[-1], OUT / "golden.jsonl")
    positions = record(OUT / "golden.wire.jsonl")
    bad = compared = 0
    for line in (OUT / "golden.jsonl").read_text(encoding="utf-8").splitlines():
        f = json.loads(line)
        pos = positions.get(round(f.get("t", -1), 4))
        if pos and "drones" in f:
            compared += 1
            bad += sum(v["pos"] != pos[k] for k, v in f["drones"].items())
    world = Engine.load("FRIDAY-RUSH", mode="headless").world
    (OUT / "world.json").write_text(
        dump({"ok": True, **world.geometry()}) + "\n", encoding="utf-8", newline="\n"
    )
    size = (OUT / "golden.wire.jsonl").stat().st_size
    print(
        f"copied {golden[-1].name}; wire {size // 1024} KiB; "
        f"{compared} frames compared, {bad} position mismatches"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
