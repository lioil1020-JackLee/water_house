"""Room reset acknowledgements retain history and later alarm occurrences."""

from pathlib import Path
import tempfile
import unittest

from ui.alarm_history import AlarmHistoryStore
from ui.alarm_state import AlarmState


class SelectiveAcknowledgementHistoryTests(unittest.TestCase):
    def test_snapshot_confirmation_survives_restart_without_clearing_later_events(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            pending_path = directory / "pending.json"
            history = AlarmHistoryStore(directory / "history.sqlite3", session_id="1")
            state = AlarmState(pending_path, history_store=history)
            first = state.update(201, True, "2026-11-08T00:00:00Z")
            captured = {first.event_uid}
            state.update(201, False, "2026-11-08T00:00:01Z")
            recurrence = state.update(201, True, "2026-11-08T00:00:02Z")
            other = state.update(202, True, "2026-11-08T00:00:03Z")
            system = state.update("__connection__", True, "2026-11-08T00:00:04Z")

            self.assertEqual(state.acknowledge(201, event_uids=captured), [first])
            rows = {row["event_uid"]: row for row in history.query()}
            self.assertIsNotNone(rows[first.event_uid]["acknowledged_at"])
            self.assertIsNotNone(rows[first.event_uid]["cleared_at"])
            for event in (recurrence, other, system):
                self.assertIsNone(rows[event.event_uid]["acknowledged_at"])
            restored = AlarmState(pending_path, history_store=history)
            self.assertEqual(
                [event.event_uid for event in restored.pending],
                [recurrence.event_uid, other.event_uid, system.event_uid],
            )
            self.assertIsNone(restored.update(201, True))
            self.assertEqual(restored.event_uid_for(201), recurrence.event_uid)
            self.assertEqual(len(history.query()), 4)

    def test_acknowledged_live_room_still_receives_its_server_clear_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            pending_path = directory / "pending.json"
            history = AlarmHistoryStore(directory / "history.sqlite3", session_id="1")
            state = AlarmState(pending_path, history_store=history)
            event = state.update(201, True, "2026-11-08T00:00:00Z")
            other = state.update(202, True, "2026-11-08T00:00:01Z")
            state.acknowledge(201, event_uids={event.event_uid})
            self.assertTrue(state.active["201"])
            self.assertEqual(state.event_uid_for(201), event.event_uid)

            restored = AlarmState(pending_path, history_store=history)
            restored.update(201, False, "2026-11-08T00:00:02Z")
            rows = {row["event_uid"]: row for row in history.query()}
            self.assertIsNotNone(rows[event.event_uid]["acknowledged_at"])
            self.assertIsNotNone(rows[event.event_uid]["cleared_at"])
            self.assertIsNone(rows[other.event_uid]["acknowledged_at"])
            self.assertEqual([item.event_uid for item in restored.pending], [other.event_uid])


if __name__ == "__main__":
    unittest.main()
