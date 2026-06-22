"""SQLite persistence for Steward.

Plain stdlib ``sqlite3`` (no ORM) for full control over the time-series schema,
retention, and rollups, and to keep the dependency surface tiny. The connection
uses WAL mode and ``check_same_thread=False`` guarded by a lock, so it is safe
to call from FastAPI's threadpool and (via ``asyncio.to_thread``) the collector.

Tables
------
metrics     time-series rows (one per entity per poll)
checks      check definitions (the full JSON plus a few queryable columns)
events      rule-engine firings
actions     the audit trail: proposed / approved / executed / rejected actions
app_state   tiny key/value store for runtime flags (paused, dry_run override)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Iterable, Optional

from steward.checks.schema import Check
from steward.models import (
    ActionRecord,
    ActionStatus,
    ClusterSnapshot,
    Event,
    now_ts,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,          -- node | vm | storage
    entity    TEXT NOT NULL,          -- node name | vmid | storage:node
    node      TEXT,
    cpu_pct   REAL,
    mem_pct   REAL,
    extra     TEXT                    -- JSON: remaining fields
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_kind_entity_ts ON metrics(kind, entity, ts);

CREATE TABLE IF NOT EXISTS checks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'manual',
    updated_ts  REAL NOT NULL,
    doc         TEXT NOT NULL          -- JSON of the full Check
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    check_id    TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    target      TEXT,
    message     TEXT,
    value       REAL,
    context     TEXT                   -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_check ON events(check_id);

CREATE TABLE IF NOT EXISTS actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    type          TEXT NOT NULL,
    params        TEXT,                -- JSON
    reason        TEXT,
    source        TEXT,
    check_id      TEXT,
    status        TEXT NOT NULL,
    dry_run       INTEGER NOT NULL,
    outcome       TEXT,
    before        TEXT,                -- JSON
    after         TEXT,                -- JSON
    reversibility TEXT,
    resolved_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def insert_snapshot(self, snap: ClusterSnapshot) -> None:
        rows: list[tuple] = []
        for n in snap.nodes:
            rows.append(
                (snap.ts, "node", n.node, n.node, round(n.cpu_pct, 3), round(n.mem_pct, 3),
                 json.dumps({"disk_pct": n.disk_pct, "status": n.status.value,
                             "mem_used_mb": n.mem_used_mb, "mem_total_mb": n.mem_total_mb}))
            )
        for v in snap.vms:
            rows.append(
                (snap.ts, "vm", str(v.vmid), v.node, round(v.cpu_pct, 3), round(v.mem_pct, 3),
                 json.dumps({"name": v.name, "status": v.status.value, "kind": v.kind.value,
                             "cores": v.cores}))
            )
        for s in snap.storage:
            rows.append(
                (snap.ts, "storage", f"{s.storage}:{s.node}", s.node, None, round(s.used_pct, 3),
                 json.dumps({"storage": s.storage, "shared": s.shared,
                             "used_gb": s.used_gb, "total_gb": s.total_gb}))
            )
        with self._lock:
            self._conn.executemany(
                "INSERT INTO metrics(ts,kind,entity,node,cpu_pct,mem_pct,extra) "
                "VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()

    def metric_series(
        self, kind: str, entity: str, *, since: Optional[float] = None, limit: int = 2000
    ) -> list[dict[str, Any]]:
        q = "SELECT ts, cpu_pct, mem_pct, extra FROM metrics WHERE kind=? AND entity=?"
        args: list[Any] = [kind, entity]
        if since is not None:
            q += " AND ts >= ?"
            args.append(since)
        q += " ORDER BY ts ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            cur = self._conn.execute(q, args)
            out = []
            for r in cur.fetchall():
                row = {"ts": r["ts"], "cpu_pct": r["cpu_pct"], "mem_pct": r["mem_pct"]}
                if r["extra"]:
                    row.update(json.loads(r["extra"]))
                out.append(row)
            return out

    def prune_metrics(self, older_than_ts: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM metrics WHERE ts < ?", (older_than_ts,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ #
    # Checks
    # ------------------------------------------------------------------ #
    def upsert_check(self, check: Check) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO checks(id,name,enabled,source,updated_ts,doc) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, enabled=excluded.enabled, "
                "source=excluded.source, updated_ts=excluded.updated_ts, doc=excluded.doc",
                (check.id, check.name, int(check.enabled), check.source, now_ts(),
                 check.model_dump_json()),
            )
            self._conn.commit()

    def get_check(self, check_id: str) -> Optional[Check]:
        with self._lock:
            r = self._conn.execute("SELECT doc FROM checks WHERE id=?", (check_id,)).fetchone()
        return Check.model_validate_json(r["doc"]) if r else None

    def list_checks(self) -> list[Check]:
        with self._lock:
            rows = self._conn.execute("SELECT doc FROM checks ORDER BY name").fetchall()
        return [Check.model_validate_json(r["doc"]) for r in rows]

    def delete_check(self, check_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM checks WHERE id=?", (check_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def insert_event(self, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(ts,check_id,check_name,severity,target,message,value,context)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (event.ts, event.check_id, event.check_name, event.severity.value,
                 event.target, event.message, event.value, json.dumps(event.context)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_events(
        self,
        *,
        severity: Optional[str] = None,
        check_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 200,
    ) -> list[Event]:
        q = "SELECT * FROM events WHERE 1=1"
        args: list[Any] = []
        if severity:
            q += " AND severity=?"
            args.append(severity)
        if check_id:
            q += " AND check_id=?"
            args.append(check_id)
        if since is not None:
            q += " AND ts >= ?"
            args.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [_event_from_row(r) for r in rows]

    def prune_events(self, older_than_ts: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (older_than_ts,))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------ #
    # Actions (audit)
    # ------------------------------------------------------------------ #
    def insert_action(self, rec: ActionRecord) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO actions(ts,type,params,reason,source,check_id,status,dry_run,"
                "outcome,before,after,reversibility,resolved_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec.ts, rec.type.value, json.dumps(rec.params), rec.reason, rec.source,
                 rec.check_id, rec.status.value, int(rec.dry_run), rec.outcome,
                 json.dumps(rec.before), json.dumps(rec.after), rec.reversibility,
                 rec.resolved_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_action(self, rec: ActionRecord) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE actions SET status=?, dry_run=?, outcome=?, before=?, after=?, "
                "reversibility=?, resolved_at=? WHERE id=?",
                (rec.status.value, int(rec.dry_run), rec.outcome, json.dumps(rec.before),
                 json.dumps(rec.after), rec.reversibility, rec.resolved_at, rec.id),
            )
            self._conn.commit()

    def get_action(self, action_id: int) -> Optional[ActionRecord]:
        with self._lock:
            r = self._conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return _action_from_row(r) if r else None

    def list_actions(
        self, *, status: Optional[str] = None, limit: int = 200
    ) -> list[ActionRecord]:
        q = "SELECT * FROM actions WHERE 1=1"
        args: list[Any] = []
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [_action_from_row(r) for r in rows]

    def recent_actions_of_type(self, action_type: str, since_ts: float) -> list[ActionRecord]:
        """Executed actions of a type since a timestamp — for cooldown/rate limits."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM actions WHERE type=? AND status=? AND resolved_at >= ? "
                "ORDER BY resolved_at DESC",
                (action_type, ActionStatus.executed.value, since_ts),
            ).fetchall()
        return [_action_from_row(r) for r in rows]

    # ------------------------------------------------------------------ #
    # App state (KV)
    # ------------------------------------------------------------------ #
    def get_state(self, key: str) -> Optional[str]:
        with self._lock:
            r = self._conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_state(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO app_state(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()


# --------------------------------------------------------------------------- #
# Row → model helpers
# --------------------------------------------------------------------------- #
def _event_from_row(r: sqlite3.Row) -> Event:
    return Event(
        id=r["id"],
        ts=r["ts"],
        check_id=r["check_id"],
        check_name=r["check_name"],
        severity=r["severity"],
        target=r["target"] or "",
        message=r["message"] or "",
        value=r["value"],
        context=json.loads(r["context"]) if r["context"] else {},
    )


def _action_from_row(r: sqlite3.Row) -> ActionRecord:
    return ActionRecord(
        id=r["id"],
        ts=r["ts"],
        type=r["type"],
        params=json.loads(r["params"]) if r["params"] else {},
        reason=r["reason"] or "",
        source=r["source"] or "manual",
        check_id=r["check_id"],
        status=r["status"],
        dry_run=bool(r["dry_run"]),
        outcome=r["outcome"] or "",
        before=json.loads(r["before"]) if r["before"] else {},
        after=json.loads(r["after"]) if r["after"] else {},
        reversibility=r["reversibility"] or "",
        resolved_at=r["resolved_at"],
    )


def _ensure_json(value: Any) -> str:  # small helper kept for symmetry
    return json.dumps(value)
