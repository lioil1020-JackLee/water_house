"""Actual SQLite + AlarmState lifecycle checks, without GUI or field I/O."""

import json
from pathlib import Path
import tempfile
import unittest

from ui.alarm_history import AlarmHistoryStore
from ui.alarm_state import AlarmState


class AlarmStateHistoryIntegrationTests(unittest.TestCase):
    def test_active_history_identity_survives_acknowledged_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            store = AlarmHistoryStore(directory / "history.sqlite3", session_id="1")
            path = directory / "pending.json"
            first = AlarmState(path, history_store=store)
            event = first.update(201, True)
            first.acknowledge()
            restored = AlarmState(path, history_store=store)
            self.assertIsNone(restored.update(201, True))
            self.assertEqual(len(store.query()), 1)
            self.assertEqual(store.query()[0]["event_uid"], event.event_uid)
            restored.update(201, False)
            row = store.query()[0]
            self.assertEqual(len(store.query()), 1)
            self.assertIsNotNone(row["acknowledged_at"])
            self.assertIsNotNone(row["cleared_at"])
            restored.update(201, True)
            self.assertEqual(len(store.query()), 2)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.store = AlarmHistoryStore(self.path / "history.sqlite3", session_id="1")

    def tearDown(self):
        self.directory.cleanup()

    def test_start_acknowledge_then_clear_finishes_the_same_history_row(self):
        state = AlarmState(self.path / "pending.json", history_store=self.store)
        event = state.update(201, True, "2026-11-03T00:00:00Z")
        state.acknowledge()
        self.assertEqual(state.pending, [])
        self.assertTrue(state.active["201"])
        state.update(201, False, "2026-11-03T00:02:00Z")
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_uid"], event.event_uid)
        self.assertEqual(rows[0]["alarm_status"], "cleared")
        self.assertEqual(rows[0]["acknowledgement_status"], "acknowledged")

    def test_pending_reload_seeds_history_idempotently_and_preserves_uid(self):
        pending = self.path / "pending.json"
        original = AlarmState(pending, history_store=self.store)
        event = original.update(201, True, "2026-11-03T00:00:00Z")
        for _ in range(2):
            restored = AlarmState(pending, history_store=self.store)
            self.assertEqual(restored.pending[0].event_uid, event.event_uid)
            self.assertIsNone(restored.update(201, True))
        self.assertEqual(len(self.store.query()), 1)

    def test_old_pending_seed_and_close_jsonl_time_are_not_duplicated(self):
        pending = self.path / "pending.json"
        pending.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "next_event_id": 2,
                    "pending": [
                        {
                            "event_id": 1,
                            "key": "201",
                            "started_at": "2026-11-03T00:00:00Z",
                            "cleared_at": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        state = AlarmState(pending, history_store=self.store)
        uid = state.pending[0].event_uid
        log = [
            {
                "event": "alarm_started",
                "room": "201",
                "event_id": 1,
                "pid": 55,
                "timestamp": "2026-11-03T00:00:00.250Z",
            },
            {
                "event": "alarm_cleared",
                "room": "201",
                "event_id": 1,
                "pid": 55,
                "timestamp": "2026-11-03T00:01:00Z",
            },
        ]
        (self.path / "alarm_events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in log),
            encoding="utf-8",
        )
        self.store.import_jsonl(self.path)
        self.assertEqual(len(self.store.query()), 1)
        self.assertEqual(self.store.query()[0]["event_uid"], uid)
        self.assertEqual(self.store.query()[0]["alarm_status"], "cleared")

    def test_shared_history_does_not_acknowledge_another_desktop_queue(self):
        other_store = AlarmHistoryStore(self.store.db_path, session_id="2")
        first = AlarmState(self.path / "session1.json", history_store=self.store)
        second = AlarmState(self.path / "session2.json", history_store=other_store)
        first_event = first.update(201, True, "2026-11-03T00:00:00Z")
        second_event = second.update(201, True, "2026-11-03T00:00:00Z")
        self.assertNotEqual(first_event.event_uid, second_event.event_uid)
        first.acknowledge()
        self.assertEqual(len(second.pending), 1)
        rows = {row["session_id"]: row for row in self.store.query()}
        self.assertEqual(rows["1"]["acknowledgement_status"], "acknowledged")
        self.assertEqual(rows["2"]["acknowledgement_status"], "pending")
        self.assertEqual(len(other_store.query()), 2)

    def test_history_failure_never_suppresses_pending_alarm(self):
        parent_file = self.path / "not-a-directory"
        parent_file.write_text("blocked", encoding="utf-8")
        broken_store = AlarmHistoryStore(parent_file / "history.sqlite3")
        state = AlarmState(self.path / "pending.json", history_store=broken_store)
        event = state.update(201, True, "2026-11-03T00:00:00Z")
        self.assertEqual(state.pending, [event])
        self.assertIsNotNone(broken_store.last_error)
        state.acknowledge()
        self.assertEqual(state.pending, [])
        self.assertTrue(state.active["201"])


if __name__ == "__main__":
    unittest.main()
