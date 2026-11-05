"""SQLite (WAL) write-through persistence, schema per 04-CONTRACTS §4.6 (Phase 02 tables).

The engine is authoritative in-process. Server modes append events and upsert state each tick
(headless: each sim-minute) and snapshot KPIs each sim-minute. Golden runs skip the DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS drones(id TEXT PRIMARY KEY, home TEXT, thermal INTEGER, state TEXT,
  battery REAL, x REAL, y REAL, mission TEXT, incident TEXT, loop TEXT, pilot TEXT, slot TEXT,
  booked_slot TEXT, flights INTEGER);
CREATE TABLE IF NOT EXISTS docks(id TEXT PRIMARY KEY, x REAL, y REAL, slots INTEGER,
  online INTEGER);
CREATE TABLE IF NOT EXISTS slot_state(dock TEXT, slot INTEGER, drone TEXT, status TEXT,
  PRIMARY KEY(dock, slot));
CREATE TABLE IF NOT EXISTS pilots(id TEXT PRIMARY KEY, name TEXT, kind TEXT, max_drones INTEGER);
CREATE TABLE IF NOT EXISTS zones(id TEXT PRIMARY KEY, kind TEXT, rect_json TEXT, active INTEGER,
  until TEXT);
CREATE TABLE IF NOT EXISTS weather(sector TEXT PRIMARY KEY, wind_ms REAL, grounded INTEGER,
  until REAL);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, sim_min REAL, type TEXT,
  payload TEXT);
CREATE TABLE IF NOT EXISTS kpi_snapshots(sim_min REAL PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, scenario TEXT, seed TEXT,
  mode TEXT, final_kpi TEXT);
CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, kind TEXT, x REAL, y REAL,
  priority INTEGER, status TEXT, geofence_r REAL, opened_min REAL, closed_min REAL);
CREATE TABLE IF NOT EXISTS feed_facts(incident TEXT, sim_min REAL, fact_json TEXT,
  PRIMARY KEY(incident, sim_min, fact_json));
CREATE TABLE IF NOT EXISTS units(id TEXT PRIMARY KEY, kind TEXT, x REAL, y REAL, status TEXT,
  incident TEXT, eta_min REAL);
CREATE TABLE IF NOT EXISTS authorisations(id TEXT PRIMARY KEY, incident TEXT, kind TEXT,
  scope TEXT, approved_by TEXT, approved_min REAL, expires_min REAL, status TEXT);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, type TEXT, created_by TEXT,
  payload TEXT, status TEXT, hold_clock INTEGER, decided_by TEXT, reason TEXT,
  created_min REAL, decided_min REAL);
-- append-only logs keep every run (run_id); privacy_log can never be updated or deleted
CREATE TABLE IF NOT EXISTS privacy_log(run_id INTEGER, seq INTEGER, sim_min REAL, drone TEXT,
  incident TEXT, kind TEXT, detail TEXT, actor TEXT, PRIMARY KEY(run_id, seq));
CREATE TRIGGER IF NOT EXISTS privacy_log_no_update BEFORE UPDATE ON privacy_log
  BEGIN SELECT RAISE(ABORT, 'privacy_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS privacy_log_no_delete BEFORE DELETE ON privacy_log
  BEGIN SELECT RAISE(ABORT, 'privacy_log is append-only'); END;
CREATE TABLE IF NOT EXISTS decisions_log(run_id INTEGER, seq INTEGER, sim_min REAL, actor TEXT,
  action TEXT, approval_id TEXT, code TEXT, rationale TEXT, kpi_before TEXT, kpi_after TEXT,
  switch_entry_id TEXT, PRIMARY KEY(run_id, seq));
"""


class DB:
    def __init__(self, path: str | Path = ":memory:"):
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.run_id: int | None = None
        self._marks = {"events": 0, "privacy": 0, "decisions": 0}

    def close(self) -> None:
        self.conn.close()

    # --- run lifecycle ---
    def start_run(self, eng) -> None:
        c = self.conn
        with c:
            for t in (
                "drones",
                "docks",
                "slot_state",
                "pilots",
                "zones",
                "weather",
                "events",
                "kpi_snapshots",
                "incidents",
                "feed_facts",
                "units",
                "authorisations",
                "approvals",
            ):
                c.execute(f"DELETE FROM {t}")
            cur = c.execute(
                "INSERT INTO runs(scenario, seed, mode) VALUES (?,?,?)",
                (eng.name, eng.seed, eng.mode),
            )
            self.run_id = cur.lastrowid
            self._marks = {"events": 0, "privacy": 0, "decisions": 0}
            c.executemany(
                "INSERT INTO pilots VALUES (?,?,?,?)",
                [(p.id, p.name, p.kind, p.max_drones) for p in eng.pilots.values()],
            )
            c.executemany(
                "INSERT INTO zones VALUES (?,?,?,?,?)",
                [
                    (
                        z["id"],
                        z["kind"],
                        json.dumps(z["rect"]),
                        int(bool(z.get("active", True))),
                        z.get("until"),
                    )
                    for z in eng.airspace.zones
                ],
            )
            self._upsert_state(eng)

    def finish_run(self, eng) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET final_kpi=? WHERE id=?",
                (json.dumps(eng.kpi.summary(eng)), self.run_id),
            )

    # --- per tick ---
    def write_tick(self, eng, _new_events=None) -> None:
        minute = eng.ticks % 10 == 0
        with self.conn:
            self._sync_logs(eng)
            if eng.mode not in ("headless", "game", "city") or minute:
                self._upsert_state(eng)
            if minute:
                self.conn.execute(
                    "INSERT OR REPLACE INTO kpi_snapshots VALUES (?,?)",
                    (round(eng.now, 4), json.dumps(eng.kpi.strip(eng))),
                )

    def sync(self, eng) -> None:
        """Persist writes made between ticks (actions, approvals)."""
        with self.conn:
            self._sync_logs(eng)
            self._upsert_state(eng)

    def _sync_logs(self, eng) -> None:
        c = self.conn
        new_events = eng.log.items[self._marks["events"] :]
        self._marks["events"] = len(eng.log.items)
        if new_events:
            c.executemany(
                "INSERT OR REPLACE INTO events VALUES (?,?,?,?)",
                [
                    (
                        e.seq,
                        round(e.t, 4),
                        e.type,
                        json.dumps(
                            {"text": e.text, "payload": e.payload, "timeline": e.timeline},
                            ensure_ascii=False,
                        ),
                    )
                    for e in new_events
                ],
            )
        rows = eng.privacy.rows()[self._marks["privacy"] :]
        self._marks["privacy"] += len(rows)
        c.executemany(
            "INSERT INTO privacy_log VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    self.run_id,
                    r["seq"],
                    r["sim_min"],
                    r["drone"],
                    r["incident"],
                    r["kind"],
                    r["detail"],
                    r["actor"],
                )
                for r in rows
            ],
        )
        rows = eng.decisions[self._marks["decisions"] :]
        self._marks["decisions"] += len(rows)
        c.executemany(
            "INSERT INTO decisions_log VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    self.run_id,
                    r["seq"],
                    r["sim_min"],
                    r["actor"],
                    r["action"],
                    r["approval_id"],
                    r["code"],
                    r["rationale"],
                    json.dumps(r["kpi_before"]),
                    json.dumps(r["kpi_after"]),
                    r["switch_entry_id"],
                )
                for r in rows
            ],
        )
        c.executemany(
            "INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    a.id,
                    a.type,
                    a.created_by,
                    json.dumps(a.args),
                    a.status,
                    int(a.hold_clock),
                    a.decided_by,
                    a.reason,
                    a.created_min,
                    a.decided_min,
                )
                for a in eng.approvals.items.values()
            ],
        )
        c.executemany(
            "INSERT OR REPLACE INTO authorisations VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    a.id,
                    a.incident,
                    a.kind,
                    a.scope,
                    a.approved_by,
                    a.approved_min,
                    a.expires_min,
                    a.status,
                )
                for a in eng.approvals.auths
            ],
        )
        c.executemany(  # revealed facts only: the unrevealed feed is never persisted
            "INSERT OR REPLACE INTO feed_facts VALUES (?,?,?)",
            [
                (f["incident"], f["t"], json.dumps(f["fact"], ensure_ascii=False))
                for f in eng._feed
                if f.get("revealed") is not None
            ],
        )
        gf = eng.world.policies["geofence_km"]
        c.executemany(
            "INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    k,
                    v["kind"],
                    *eng.world.point(v["at"]),
                    v.get("priority"),
                    v["status"],
                    gf,
                    v["t"],
                    v.get("closed_t"),
                )
                for k, v in eng.incidents.items()
            ],
        )

    def _upsert_state(self, eng) -> None:
        c = self.conn
        c.executemany(
            "INSERT OR REPLACE INTO drones VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    d.id,
                    d.home,
                    int(d.thermal),
                    d.state,
                    round(d.bat, 3),
                    d.pos[0],
                    d.pos[1],
                    d.mission,
                    d.incident,
                    d.loop,
                    d.pilot,
                    d.slot,
                    d.target if d.mission == "returning" else None,
                    d.flights,
                )
                for d in eng.drones.values()
            ],
        )
        c.executemany(
            "INSERT OR REPLACE INTO units VALUES (?,?,?,?,?,?,?)",
            [
                (u["id"], u["kind"], *u["pos"], u["status"], u["incident"], u["eta_min"])
                for u in eng.ground.view(eng.now)
            ],
        )
        c.executemany(
            "INSERT OR REPLACE INTO docks VALUES (?,?,?,?,?)",
            [(k.id, k.at[0], k.at[1], k.slots, int(k.online)) for k in eng.docks.values()],
        )
        c.executemany(
            "INSERT OR REPLACE INTO slot_state VALUES (?,?,?,?)",
            [
                (k.id, s["slot"], s["drone"], s["status"])
                for k in eng.docks.values()
                for s in k.slot_states()
            ],
        )
        c.executemany(
            "INSERT OR REPLACE INTO weather VALUES (?,?,?,?)",
            [
                (s, v["wind_ms"], int(v["grounded"]), v["until_min"])
                for s, v in eng.weather.view().items()
            ],
        )

    # --- reads (tests / tools) ---
    def count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
