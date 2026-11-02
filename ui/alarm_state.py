"""Per-client alarm edges and durable, explicitly acknowledged event records."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path


@dataclass
class AlarmEvent:
    event_id: int
    key: str
    started_at: str
    cleared_at: str | None = None


class AlarmState:
    """Server clears update live state but never acknowledge a local event."""

    def __init__(self, storage_path: Path | None = None):
        self.storage_path = Path(storage_path) if storage_path is not None else None
        self.active: dict[str, bool] = {}
        self.active_event_ids: dict[str, int] = {}
        self.pending: list[AlarmEvent] = []
        self.next_event_id = 1
        self.storage_error: str | None = None
        self._storage_blocked = False
        self._load()

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
            changed = False
            for event in self.pending:
                if event.key == key and event.cleared_at is None:
                    event.cleared_at = timestamp
                    changed = True
            if changed:
                self._save()
            return None
        if previous is True:
            return None
        # A restored event without an observed clear is still the same event.
        # Fresh live snapshots can confirm it without duplicating its record.
        if previous is None:
            existing = next((event for event in reversed(self.pending)
                             if event.key == key and event.cleared_at is None), None)
            if existing is not None:
                self.active_event_ids[key] = existing.event_id
                return None
        event = AlarmEvent(self.next_event_id, key, timestamp)
        self.next_event_id += 1
        self.active_event_ids[key] = event.event_id
        self.pending.append(event)
        self._save()
        return event

    def event_id_for(self, key):
        key = str(key)
        return self.active_event_ids.get(key) or next(
            (event.event_id for event in reversed(self.pending)
             if event.key == key and event.cleared_at is None), None
        )

    def acknowledge(self):
        acknowledged = list(self.pending)
        self.pending.clear()
        self._save()
        return acknowledged

    def _load(self):
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1:
                raise ValueError("unsupported pending-alarm schema")
            pending = []
            ids = set()
            for row in payload["pending"]:
                if (
                    type(row.get("event_id")) is not int
                    or row["event_id"] <= 0
                    or row["event_id"] in ids
                    or not isinstance(row.get("key"), str)
                    or not isinstance(row.get("started_at"), str)
                    or not (row.get("cleared_at") is None or isinstance(row["cleared_at"], str))
                ):
                    raise ValueError("invalid pending-alarm record")
                ids.add(row["event_id"])
                pending.append(AlarmEvent(**row))
            self.pending = pending
            self.next_event_id = max(int(payload["next_event_id"]), max(ids, default=0) + 1)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.storage_error = str(exc)
            # Preserve evidence before a new state is ever written.
            backup = self.storage_path.with_name(
                self.storage_path.name + '.invalid-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
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
                "pending": [asdict(event) for event in self.pending],
            }
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.storage_path)
            self.storage_error = None
        except OSError as exc:
            self.storage_error = str(exc)
