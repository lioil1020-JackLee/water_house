"""Durable alarm occurrences, independent of the local acknowledgement queue.

Queries use a read-only SQLite connection. Old JSONL records are imported without
changing pending alarms or PLC values. Records without a stable occurrence UID
are linked only within a proven process identity, never guessed across PIDs.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
from uuid import NAMESPACE_URL, uuid5

LOGGER = logging.getLogger(__name__)
LIFECYCLE_EVENTS = {"alarm_started", "alarm_cleared", "alarm_acknowledged"}


def _timestamp(value=None):
    if value is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(value, (float, int)):
        if not math.isfinite(value):
            raise ValueError("non-finite alarm timestamp")
        moment = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError("alarm timestamp has no recorded timezone")
        moment = moment.astimezone(timezone.utc)
    else:
        raise ValueError("unsupported alarm timestamp")
    return moment.isoformat(timespec="microseconds"), moment.timestamp()


class AlarmHistoryStore:
    """Local-account occurrence storage; failures are reported without raising.

    Callers should supply a persisted UUID for all new lifecycle events. The
    optional session_id identifies this writer, not a query/import filter.
    Desktop sessions share history while retaining their own acknowledgement
    queues. Older logs lack a session and remain marked as legacy evidence.
    """

    def __init__(self, db_path: Path, session_id=None):
        self.db_path = Path(db_path)
        self.session_id = None if session_id is None else str(session_id)
        self.last_error = None
        self.import_stats = {}
        self._initialized = False
        self._initialization_lock = threading.Lock()
        self._ensure_schema()

    @contextmanager
    def _connect(self, *, readonly=False):
        if readonly:
            connection = sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25
            )
        else:
            connection = sqlite3.connect(self.db_path, timeout=0.25)
        try:
            yield connection
            if not readonly:
                connection.commit()
        except BaseException:
            if not readonly:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _failure(self, operation, exc):
        self.last_error = f"{operation}: {exc}"
        LOGGER.warning("Alarm history %s failed: %s", operation, exc)

    def _ensure_schema(self):
        if self._initialized:
            return True
        with self._initialization_lock:
            if self._initialized:
                return True
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript("""
                        CREATE TABLE IF NOT EXISTS alarms (
                            event_uid TEXT PRIMARY KEY,
                            event_id INTEGER,
                            room TEXT NOT NULL,
                            session_id TEXT,
                            started_at TEXT,
                            started_epoch REAL,
                            cleared_at TEXT,
                            acknowledged_at TEXT,
                            observed_at TEXT NOT NULL,
                            observed_epoch REAL NOT NULL,
                            source TEXT NOT NULL,
                            source_pid TEXT,
                            identity_confidence TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS alarms_recent
                            ON alarms(started_epoch DESC, observed_epoch DESC);
                        CREATE INDEX IF NOT EXISTS alarms_room ON alarms(room);
                        CREATE TABLE IF NOT EXISTS imported_events (
                            fingerprint TEXT PRIMARY KEY
                        );
                        CREATE TABLE IF NOT EXISTS legacy_aliases (
                            legacy_uid TEXT PRIMARY KEY,
                            event_uid TEXT NOT NULL,
                            source_pid TEXT,
                            event_id INTEGER,
                            room TEXT NOT NULL,
                            session_id TEXT,
                            started_epoch REAL NOT NULL
                        );
                    """)
                self._initialized = True
                self.last_error = None
                return True
            except (OSError, sqlite3.Error) as exc:
                self._failure("initialize", exc)
                return False

    @staticmethod
    def _put(
        connection,
        event,
        *,
        event_uid,
        room,
        event_id,
        timestamp,
        started_at=None,
        session_id=None,
        pid=None,
        source="live",
        confidence="uid",
    ):
        observed, observed_epoch = _timestamp(timestamp)
        start = start_epoch = None
        if started_at is not None or event == "alarm_started":
            start, start_epoch = _timestamp(
                started_at if started_at is not None else timestamp
            )
        existing = connection.execute(
            "SELECT room FROM alarms WHERE event_uid = ?", (event_uid,)
        ).fetchone()
        if existing is not None and existing[0] != room:
            raise ValueError("occurrence UID belongs to a different room")
        connection.execute(
            """
            INSERT INTO alarms (
                event_uid, event_id, room, session_id, started_at, started_epoch,
                cleared_at, acknowledged_at, observed_at, observed_epoch,
                source, source_pid, identity_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_uid) DO UPDATE SET
                started_at = COALESCE(alarms.started_at, excluded.started_at),
                started_epoch = COALESCE(alarms.started_epoch, excluded.started_epoch),
                cleared_at = COALESCE(alarms.cleared_at, excluded.cleared_at),
                acknowledged_at = COALESCE(alarms.acknowledged_at, excluded.acknowledged_at)
        """,
            (
                event_uid,
                event_id,
                room,
                session_id,
                start,
                start_epoch,
                observed if event == "alarm_cleared" else None,
                observed if event == "alarm_acknowledged" else None,
                observed,
                observed_epoch,
                source,
                None if pid is None else str(pid),
                confidence,
            ),
        )

    def record_event(
        self,
        event,
        *,
        event_uid=None,
        room=None,
        key=None,
        event_id=None,
        timestamp=None,
        started_at=None,
        session_id=None,
        pid=None,
        source="live",
        **_metadata,
    ):
        if event not in LIFECYCLE_EVENTS:
            return False
        if not self._ensure_schema():
            return False
        try:
            room = str(room if room is not None else key)
            if room == "None":
                raise ValueError("alarm room is required")
            session = self.session_id if session_id is None else str(session_id)
            identifier = None if event_id is None else int(event_id)
            timestamp = _timestamp(timestamp)[0]
            if event_uid is None:
                if event != "alarm_started" and started_at is None:
                    raise ValueError(
                        "lifecycle update requires a stable occurrence UID"
                    )
                start = _timestamp(started_at or timestamp)[0]
                event_uid = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"waterhouse:{session}:{room}:{identifier}:{start}",
                    )
                )
            event_uid = str(event_uid)
            if not event_uid:
                raise ValueError("empty occurrence UID")
            with self._connect() as connection:
                self._put(
                    connection,
                    event,
                    event_uid=event_uid,
                    room=room,
                    event_id=identifier,
                    timestamp=timestamp,
                    started_at=started_at,
                    session_id=session,
                    pid=os.getpid() if pid is None else pid,
                    source=source,
                    confidence="restored" if source == "pending_restore" else "uid",
                )
            self.last_error = None
            return True
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            self._failure("record", exc)
            return False

    def _legacy_uid(self, connection, payload, timestamp, epoch, fingerprint):
        """Resolve old records only using their own process identity or a seed."""
        room = str(payload["room"])
        event_id = int(payload["event_id"])
        pid = None if payload.get("pid") is None else str(payload["pid"])
        session = (
            None if payload.get("session_id") is None else str(payload["session_id"])
        )
        event = payload["event"]
        if event == "alarm_started":
            legacy_uid = str(
                uuid5(
                    NAMESPACE_URL,
                    f"legacy:{session}:{pid}:{room}:{event_id}:{timestamp}",
                )
            )
            alias = connection.execute(
                "SELECT event_uid FROM legacy_aliases WHERE legacy_uid = ?",
                (legacy_uid,),
            ).fetchone()
            if alias:
                return alias[0], "pid"
            # Pending snapshots are trusted existing occurrences. A unique,
            # near-identical timestamp can connect their pre-UID JSONL start.
            seed_sql = """
                SELECT event_uid FROM alarms WHERE source = 'pending_restore'
                AND room = ? AND event_id = ? AND ABS(started_epoch - ?) <= 1.0
            """
            seed_params = [room, event_id, epoch]
            if session is not None:
                seed_sql += " AND session_id = ?"
                seed_params.append(session)
            seeds = connection.execute(seed_sql, seed_params).fetchall()
            uid = seeds[0][0] if len(seeds) == 1 else legacy_uid
            connection.execute(
                """
                INSERT INTO legacy_aliases VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (legacy_uid, uid, pid, event_id, room, session, epoch),
            )
            return uid, "pid"
        if pid is not None:
            candidates = connection.execute(
                """
                SELECT event_uid FROM legacy_aliases
                WHERE source_pid = ? AND event_id = ? AND room = ?
                AND session_id IS ? AND started_epoch <= ?
                ORDER BY started_epoch DESC LIMIT 1
            """,
                (pid, event_id, room, session, epoch),
            ).fetchone()
            if candidates:
                return candidates[0], "pid"
        # A clear/ack from another process is kept as a separate fragment. It
        # must not falsely mark a known alarm as restored or acknowledged.
        return str(uuid5(NAMESPACE_URL, "legacy-fragment:" + fingerprint)), "unmatched"

    def import_jsonl(self, log_dir: Path | None):
        stats = {
            "imported": 0,
            "duplicates": 0,
            "ignored": 0,
            "malformed": 0,
            "unmatched": 0,
            "foreign_session": 0,
            "warnings": [],
        }
        if log_dir is None:
            self.import_stats = stats
            return stats
        if not self._ensure_schema():
            stats["warnings"].append(self.last_error)
            return stats
        directory = Path(log_dir)
        records = []
        # Read oldest rotation first; sort relevant events by timestamp below.
        paths = [directory / f"alarm_events.jsonl.{i}" for i in range(10, 0, -1)]
        paths.append(directory / "alarm_events.jsonl")
        try:
            for path in paths:
                if not path.is_file():
                    continue
                with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                    for line in handle:
                        try:
                            payload = json.loads(line)
                            if not isinstance(payload, dict):
                                raise ValueError("JSONL entry is not an object")
                            if payload.get("event") not in LIFECYCLE_EVENTS:
                                stats["ignored"] += 1
                                continue
                            if (
                                payload.get("room") is None
                                or payload.get("event_id") is None
                            ):
                                raise ValueError(
                                    "lifecycle entry lacks room or event ID"
                                )
                            if payload.get("timestamp") is None:
                                raise ValueError(
                                    "lifecycle entry lacks a recorded timestamp"
                                )
                            int(payload["event_id"])
                            session = payload.get("session_id")
                            if (
                                session is not None
                                and self.session_id is not None
                                and str(session) != self.session_id
                            ):
                                # Historical records from other desktop sessions
                                # remain visible; this counter is informational.
                                stats["foreign_session"] += 1
                            timestamp, epoch = _timestamp(payload.get("timestamp"))
                            canonical = json.dumps(
                                payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            )
                            fingerprint = hashlib.sha256(
                                canonical.encode("utf-8")
                            ).hexdigest()
                            records.append((epoch, payload, timestamp, fingerprint))
                        except (
                            json.JSONDecodeError,
                            ValueError,
                            TypeError,
                            OverflowError,
                        ):
                            # A writer may still be appending the final line.
                            # It is not marked imported and can succeed later.
                            stats["malformed"] += 1
            records.sort(key=lambda row: row[0])
            with self._connect() as connection:
                for epoch, payload, timestamp, fingerprint in records:
                    # Each event has its own short transaction. The GUI writer
                    # never waits behind the entire historical import.
                    with connection:
                        if connection.execute(
                            "SELECT 1 FROM imported_events WHERE fingerprint = ?",
                            (fingerprint,),
                        ).fetchone():
                            stats["duplicates"] += 1
                            continue
                        uid = payload.get("event_uid")
                        if uid:
                            confidence = "uid"
                        else:
                            uid, confidence = self._legacy_uid(
                                connection, payload, timestamp, epoch, fingerprint
                            )
                        self._put(
                            connection,
                            payload["event"],
                            event_uid=str(uid),
                            room=str(payload["room"]),
                            event_id=int(payload["event_id"]),
                            timestamp=timestamp,
                            started_at=payload.get("started_at"),
                            session_id=(
                                None
                                if payload.get("session_id") is None
                                else str(payload["session_id"])
                            ),
                            pid=payload.get("pid"),
                            source="legacy_jsonl",
                            confidence=confidence,
                        )
                        connection.execute(
                            "INSERT INTO imported_events VALUES (?)", (fingerprint,)
                        )
                        stats["imported"] += 1
                        stats["unmatched"] += confidence == "unmatched"
            self.last_error = None
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            self._failure("import", exc)
            stats["warnings"].append(self.last_error)
        if stats["unmatched"]:
            stats["warnings"].append(
                "部分舊紀錄缺少同程序的發生資料，未推測跨程序復歸或確認關係。"
            )
        self.import_stats = stats
        return stats

    def query(self, limit=500, text="", pending_only=False, room=None, status="all"):
        if not self._initialized:
            return []
        try:
            limit = max(1, min(int(limit), 10000))
            clauses, params = [], []
            if room is not None:
                clauses.append("room = ?")
                params.append(str(room))
            if pending_only or status == "pending":
                clauses.append("acknowledged_at IS NULL AND started_at IS NOT NULL")
            if status == "acknowledged":
                clauses.append("acknowledged_at IS NOT NULL")
            elif status == "active":
                clauses.append("started_at IS NOT NULL AND cleared_at IS NULL")
            elif status == "cleared":
                clauses.append("cleared_at IS NOT NULL")
            elif status == "unknown":
                clauses.append("started_at IS NULL AND cleared_at IS NULL")
            elif status not in ("all", "pending"):
                raise ValueError("unsupported history status filter")
            if text:
                escaped = (
                    str(text)
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                clauses.append(
                    "(room LIKE ? ESCAPE '\\' OR CAST(event_id AS TEXT) LIKE ? ESCAPE '\\')"
                )
                params.extend(("%" + escaped + "%", "%" + escaped + "%"))
            sql = "SELECT * FROM alarms"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY COALESCE(started_epoch, observed_epoch) DESC, event_uid DESC LIMIT ?"
            params.append(limit)
            with self._connect(readonly=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = [dict(row) for row in connection.execute(sql, params)]
            for row in rows:
                row["key"] = row["room"]
                row["alarm_status"] = (
                    "cleared"
                    if row["cleared_at"]
                    else ("active" if row["started_at"] else "unknown")
                )
                row["acknowledgement_status"] = (
                    "acknowledged"
                    if row["acknowledged_at"]
                    else ("pending" if row["started_at"] else "unknown")
                )
            self.last_error = None
            return rows
        except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
            self._failure("query", exc)
            return []
