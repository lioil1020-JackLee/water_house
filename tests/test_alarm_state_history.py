import json
from pathlib import Path
import tempfile
import unittest

from ui.alarm_state import AlarmState


class RecordingStore:
    def __init__(self):
        self.events = []

    def record_event(self, action, **fields):
        self.events.append((action, fields))
        return True


class AlarmStateHistoryTests(unittest.TestCase):
    def test_acknowledged_alarm_can_later_receive_server_clear(self):
        store = RecordingStore()
        state = AlarmState(history_store=store)
        event = state.update(201, True)
        state.acknowledge()
        state.update(201, False)
        self.assertEqual(
            [a for a, _ in store.events],
            ["alarm_started", "alarm_acknowledged", "alarm_cleared"],
        )
        self.assertEqual({f["event_uid"] for _, f in store.events}, {event.event_uid})
        self.assertEqual(state.pending, [])

    def test_uids_survive_restart_and_keep_legacy_pending_rows_compatible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pending.json"
            store = RecordingStore()
            first = AlarmState(path, history_store=store).update(201, True)
            payload = json.loads(path.read_text("utf-8"))
            self.assertNotIn("event_uid", payload["pending"][0])
            restored = AlarmState(path, history_store=store)
            self.assertEqual(restored.pending[0].event_uid, first.event_uid)
            self.assertEqual(store.events[-1][1]["source"], "pending_restore")

    def test_legacy_record_migration_identity_is_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pending.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "next_event_id": 2,
                        "pending": [
                            {
                                "event_id": 1,
                                "key": "201",
                                "started_at": "2026-11-03T00:00:00+00:00",
                                "cleared_at": None,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                AlarmState(path).pending[0].event_uid,
                AlarmState(path).pending[0].event_uid,
            )

    def test_history_failure_does_not_suppress_alarm_or_acknowledgement(self):
        class FailingStore:
            def record_event(self, *args, **kwargs):
                raise OSError("synthetic history storage failure")

        state = AlarmState(history_store=FailingStore())
        with self.assertLogs(level="ERROR"):
            state.update(201, True)
        self.assertEqual(len(state.pending), 1)
        with self.assertLogs(level="ERROR"):
            state.acknowledge()
        self.assertEqual(state.pending, [])

    def test_acknowledged_active_occurrence_is_preserved_for_post_restart_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pending.json"
            store = RecordingStore()
            state = AlarmState(path, history_store=store)
            original = state.update(201, True)
            state.acknowledge()
            restored = AlarmState(path, history_store=store)
            self.assertEqual(restored.active, {})
            self.assertEqual(restored.event_uid_for(201), original.event_uid)
            restored.update(201, False)
            self.assertEqual(store.events[-1][0], "alarm_cleared")
            self.assertEqual(store.events[-1][1]["event_uid"], original.event_uid)
            self.assertIsNone(AlarmState(path).event_uid_for(201))

    def test_restart_does_not_invent_new_occurrence_for_same_acknowledged_alarm(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pending.json"
            state = AlarmState(path)
            original = state.update(201, True)
            state.acknowledge()
            restored = AlarmState(path)
            self.assertIsNone(restored.update(201, True))
            self.assertEqual(restored.event_uid_for(201), original.event_uid)
            self.assertEqual(restored.pending, [])
            restored.update(201, False)
            new = restored.update(201, True)
            self.assertIsNotNone(new)
            self.assertNotEqual(new.event_uid, original.event_uid)


if __name__ == "__main__":
    unittest.main()
