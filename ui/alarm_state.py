"""Per-client alarm edges and durable, explicitly acknowledged event records."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import uuid


@dataclass
class AlarmEvent:
    event_id: int
    key: str
    started_at: str
    cleared_at: str | None = None
    event_uid: str = field(default_factory=lambda: str(uuid.uuid4()))


class AlarmState:
    """Server clears update live state but never acknowledge a local event."""

    def __init__(self, storage_path: Path | None = None, *, history_store=None):
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self.active: dict[str, bool] = {}
        self.active_event_ids: dict[str, int] = {}
        self._active_events: dict[str, AlarmEvent] = {}
        self.history_store = history_store
        self.pending: list[AlarmEvent] = []
        self.next_event_id = 1
        self.storage_error: str | None = None
        self._storage_blocked = False
        self._load()
        for event in self.pending:
            self._record_history(
                "alarm_started", event, event.started_at, source="pending_restore"
            )
            if event.cleared_at is not None:
                self._record_history(
                    "alarm_cleared", event, event.cleared_at, source="pending_restore"
                )

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def update(self, key, active, timestamp=None):
        key = str(key)
        active = bool(active)
        timestamp = timestamp or self._now()
        previous = self.active.get(key)
        self.active[key] = active
        if not active:
            self.active_event_ids.pop(key, None)
            active_event = self._active_events.pop(key, None)
            changed = False
            recorded = set()
            for event in self.pending:
                if event.key == key and event.cleared_at is None:
                    event.cleared_at = timestamp
                    self._record_history("alarm_cleared", event, timestamp)
                    recorded.add(event.event_uid)
                    changed = True
            # A local acknowledgement must not prevent a later server clear
            # from completing the historical occurrence.
            if active_event is not None and active_event.event_uid not in recorded:
                active_event.cleared_at = timestamp
                self._record_history("alarm_cleared", active_event, timestamp)
            if changed or active_event is not None:
                self._save()
            return None
        if previous is True:
            return None
        # A restored event without an observed clear is still the same event.
        # Fresh live snapshots can confirm it without duplicating its record.
        if previous is None:
            existing = next(
                (
                    event
                    for event in reversed(self.pending)
                    if event.key == key and event.cleared_at is None
                ),
                None,
            )
            if existing is None:
                existing = self._active_events.get(key)
            if existing is not None:
                self.active_event_ids[key] = existing.event_id
                self._active_events[key] = existing
                return None
        event = AlarmEvent(self.next_event_id, key, timestamp)
        self.next_event_id += 1
        self.active_event_ids[key] = event.event_id
        self._active_events[key] = event
        self.pending.append(event)
        self._save()
        self._record_history("alarm_started", event, timestamp)
        return event

    def event_uid_for(self, key):
        key = str(key)
        active = self._active_events.get(key)
        if active is not None:
            return active.event_uid
        return next(
            (
                e.event_uid
                for e in reversed(self.pending)
                if e.key == key and e.cleared_at is None
            ),
            None,
        )

    def event_id_for(self, key):
        key = str(key)
        return self.active_event_ids.get(key) or next(
            (
                event.event_id
                for event in reversed(self.pending)
                if event.key == key and event.cleared_at is None
            ),
            None,
        )

    def acknowledge(self):
        acknowledged = list(self.pending)
        timestamp = self._now()
        for event in acknowledged:
            self._record_history("alarm_acknowledged", event, timestamp)
        self.pending.clear()
        self._save()
        return acknowledged

    def _record_history(self, action, event, timestamp, *, source="live"):
        if self.history_store is None:
            return
        try:
            if not self.history_store.record_event(
                action,
                event_uid=event.event_uid,
                room=event.key,
                event_id=event.event_id,
                timestamp=timestamp,
                started_at=event.started_at,
                source=source,
            ):
                logging.warning(
                    "警報歷史寫入失敗，待確認提醒仍保留：%s",
                    getattr(self.history_store, "last_error", "unknown"),
                )
        except Exception:
            logging.exception("警報歷史無法寫入，待確認提醒仍保留")

    def _load(self):
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                raise ValueError("unsupported pending-alarm schema")
            pending = []
            ids = set()
            event_uids = payload.get("event_uids", {})
            if not isinstance(event_uids, dict):
                raise ValueError("invalid event UID map")
            for row in payload["pending"]:
                if (
                    type(row.get("event_id")) is not int
                    or row["event_id"] <= 0
                    or row["event_id"] in ids
                    or not isinstance(row.get("key"), str)
                    or not isinstance(row.get("started_at"), str)
                    or not (
                        row.get("cleared_at") is None
                        or isinstance(row["cleared_at"], str)
                    )
                ):
                    raise ValueError("invalid pending-alarm record")
                ids.add(row["event_id"])
                values = dict(row)
                event_uid = event_uids.get(str(row["event_id"]))
                if event_uid is None:
                    # Deterministic identity for a v4.1 pending record, including
                    # repeated reads before it is next saved by this version.
                    event_uid = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f'{self.storage_path.resolve()}|{row["event_id"]}|{row["key"]}|{row["started_at"]}',
                        )
                    )
                if not isinstance(event_uid, str) or not event_uid:
                    raise ValueError("invalid event UID")
                values["event_uid"] = event_uid
                pending.append(AlarmEvent(**values))
            active_rows = payload.get("active_events", [])
            if not isinstance(active_rows, list):
                raise ValueError("invalid active occurrence records")
            active_events = {}
            active_ids = set()
            for row in active_rows:
                if (
                    not isinstance(row, dict)
                    or type(row.get("event_id")) is not int
                    or row["event_id"] <= 0
                    or row["event_id"] in active_ids
                    or not isinstance(row.get("key"), str)
                    or row["key"] in active_events
                    or not isinstance(row.get("started_at"), str)
                    or row.get("cleared_at") is not None
                ):
                    raise ValueError("invalid active occurrence record")
                same_pending = next(
                    (e for e in pending if e.event_id == row["event_id"]), None
                )
                if same_pending is not None:
                    if (
                        same_pending.key != row["key"]
                        or same_pending.started_at != row["started_at"]
                        or same_pending.cleared_at is not None
                    ):
                        raise ValueError("active and pending occurrences conflict")
                    event = same_pending
                else:
                    event_uid = event_uids.get(str(row["event_id"]))
                    if not isinstance(event_uid, str) or not event_uid:
                        raise ValueError("active occurrence lacks stable UID")
                    event = AlarmEvent(**row, event_uid=event_uid)
                active_events[event.key] = event
                active_ids.add(event.event_id)
            self.pending = pending
            # Restored references describe the last occurrence, not fresh live
            # values. Keep self.active empty until an OPC snapshot arrives.
            self._active_events = active_events
            self.active_event_ids = {
                key: e.event_id for key, e in active_events.items()
            }
            self.next_event_id = max(
                int(payload["next_event_id"]), max(ids | active_ids, default=0) + 1
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.storage_error = str(exc)
            # Preserve evidence before a new state is ever written.
            backup = self.storage_path.with_name(
                self.storage_path.name
                + ".invalid-"
                + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            )
            try:
                os.replace(self.storage_path, backup)
            except OSError:
                self._storage_blocked = True

    def _save(self):
        if self.storage_path is None or self._storage_blocked:
            return
        temporary = self.storage_path.with_suffix(".json.tmp")
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": 1,
                "next_event_id": self.next_event_id,
                # Keep v4.1's pending-row schema readable by the previous version.
                "pending": [
                    {k: v for k, v in asdict(event).items() if k != "event_uid"}
                    for event in self.pending
                ],
                "active_events": [
                    {k: v for k, v in asdict(event).items() if k != "event_uid"}
                    for event in self._active_events.values()
                ],
                "event_uids": {
                    str(e.event_id): e.event_uid
                    for e in [*self._active_events.values(), *self.pending]
                },
            }
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.storage_path)
            self.storage_error = None
        except OSError as exc:
            self.storage_error = str(exc)
