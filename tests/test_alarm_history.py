from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ui.alarm_history import AlarmHistoryStore


class AlarmHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.store = AlarmHistoryStore(self.path / "history.sqlite3", session_id="1")

    def tearDown(self):
        self.directory.cleanup()

    def event(self, name, **fields):
        payload = {
            "event_uid": "occurrence-a",
            "room": "201",
            "event_id": 1,
            "timestamp": "2026-11-03T00:00:00Z",
        }
        payload.update(fields)
        self.assertTrue(self.store.record_event(name, **payload), self.store.last_error)

    def write_log(self, records, suffix=""):
        path = self.path / ("alarm_events.jsonl" + suffix)
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
        )

    def test_acknowledgement_and_clear_are_independent_and_history_survives(self):
        self.event("alarm_started")
        self.event("alarm_acknowledged", timestamp="2026-11-03T00:01:00Z")
        row = self.store.query()[0]
        self.assertEqual(row["alarm_status"], "active")
        self.assertEqual(row["acknowledgement_status"], "acknowledged")
        self.assertEqual(self.store.query(pending_only=True), [])
        self.event("alarm_cleared", timestamp="2026-11-03T00:02:00Z")
        reopened = AlarmHistoryStore(self.store.db_path, session_id="1")
        self.assertEqual(len(reopened.query()), 1)
        self.assertEqual(reopened.query()[0]["alarm_status"], "cleared")
        self.assertIsNotNone(reopened.query()[0]["acknowledged_at"])

    def test_same_numeric_id_different_uid_never_merges_recurrence(self):
        self.event("alarm_started")
        self.event(
            "alarm_started", event_uid="occurrence-b", timestamp="2026-11-03T00:03:00Z"
        )
        self.assertEqual(len(self.store.query()), 2)
        self.event("alarm_cleared", event_uid="occurrence-b")
        self.assertIsNone(
            next(r for r in self.store.query() if r["event_uid"] == "occurrence-a")[
                "cleared_at"
            ]
        )

    def test_stable_uid_links_events_across_process_restart(self):
        self.event("alarm_started", pid=11)
        self.event("alarm_cleared", pid=22)
        self.event("alarm_acknowledged", pid=22)
        self.assertEqual(len(self.store.query()), 1)
        self.assertEqual(
            self.store.query()[0]["acknowledgement_status"], "acknowledged"
        )

    def test_conflicting_room_does_not_reuse_an_occurrence_uid(self):
        self.event("alarm_started")
        self.assertFalse(
            self.store.record_event(
                "alarm_cleared", event_uid="occurrence-a", room="202"
            )
        )
        self.assertIsNone(self.store.query()[0]["cleared_at"])

    def test_read_only_query_does_not_change_database_or_pending_rows(self):
        self.event("alarm_started")
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            before = connection.execute("SELECT * FROM alarms").fetchall()
        self.assertEqual(len(self.store.query()), 1)
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            self.assertEqual(
                connection.execute("SELECT * FROM alarms").fetchall(), before
            )

    def test_query_limits_filters_and_literal_search(self):
        self.event("alarm_started", room="201_100%")
        self.event(
            "alarm_started",
            event_uid="b",
            room="202",
            event_id=2,
            timestamp="2026-11-03T00:01:00Z",
        )
        self.assertEqual(self.store.query(limit=1)[0]["room"], "202")
        self.assertEqual(len(self.store.query(text="100%")), 1)
        self.assertEqual(len(self.store.query(room="202")), 1)
        self.assertEqual(len(self.store.query(status="active")), 2)
        self.assertEqual(self.store.query(status="cleared"), [])

    def test_rotated_legacy_import_is_idempotent(self):
        base = {"room": "201", "event_id": 3, "pid": 10}
        self.write_log([{**base, "event": "alarm_started", "timestamp": 1000}], ".10")
        self.write_log(
            [
                {**base, "event": "alarm_cleared", "timestamp": 1001},
                {**base, "event": "alarm_acknowledged", "timestamp": 1002},
            ]
        )
        self.assertEqual(self.store.import_jsonl(self.path)["imported"], 3)
        row = self.store.query()[0]
        self.assertEqual(row["alarm_status"], "cleared")
        self.assertEqual(row["acknowledgement_status"], "acknowledged")
        self.assertEqual(self.store.import_jsonl(self.path)["duplicates"], 3)
        self.assertEqual(len(self.store.query()), 1)

    def test_legacy_cross_pid_update_is_not_guessed(self):
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 3,
                    "pid": 10,
                    "timestamp": 1000,
                },
                {
                    "event": "alarm_cleared",
                    "room": "201",
                    "event_id": 3,
                    "pid": 20,
                    "timestamp": 1001,
                },
            ]
        )
        stats = self.store.import_jsonl(self.path)
        self.assertEqual(stats["unmatched"], 1)
        start = next(row for row in self.store.query() if row["started_at"])
        self.assertIsNone(start["cleared_at"])
        fragment = next(row for row in self.store.query() if not row["started_at"])
        self.assertEqual(fragment["identity_confidence"], "unmatched")
        self.assertTrue(stats["warnings"])

    def test_import_uses_uid_to_deduplicate_already_recorded_live_event(self):
        self.event("alarm_started")
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "event_uid": "occurrence-a",
                    "room": "201",
                    "event_id": 1,
                    "pid": 30,
                    "session_id": "1",
                    "timestamp": "2026-11-03T00:00:00.1Z",
                }
            ]
        )
        self.store.import_jsonl(self.path)
        self.assertEqual(len(self.store.query()), 1)

    def test_unique_restored_pending_seed_can_link_old_start(self):
        self.event("alarm_started", source="pending_restore", timestamp=1000)
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 1,
                    "pid": 10,
                    "timestamp": 1000.2,
                },
                {
                    "event": "alarm_cleared",
                    "room": "201",
                    "event_id": 1,
                    "pid": 10,
                    "timestamp": 1002,
                },
            ]
        )
        self.store.import_jsonl(self.path)
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_uid"], "occurrence-a")
        self.assertIsNotNone(rows[0]["cleared_at"])

    def test_ambiguous_pending_seeds_are_never_merged(self):
        self.event("alarm_started", source="pending_restore", timestamp=1000)
        self.event(
            "alarm_started", event_uid="b", source="pending_restore", timestamp=1000.1
        )
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 1,
                    "pid": 10,
                    "timestamp": 1000.2,
                }
            ]
        )
        self.store.import_jsonl(self.path)
        self.assertEqual(len(self.store.query()), 3)

    def test_known_other_session_is_preserved_and_unrelated_events_are_ignored(self):
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 1,
                    "session_id": "2",
                    "timestamp": 1000,
                },
                {"event": "display_requested", "timestamp": 1000},
            ]
        )
        stats = self.store.import_jsonl(self.path)
        self.assertEqual(stats["foreign_session"], 1)
        self.assertEqual(stats["ignored"], 1)
        self.assertEqual(len(self.store.query()), 1)
        self.assertEqual(self.store.query()[0]["session_id"], "2")

    def test_known_other_session_never_matches_current_pending_seed(self):
        self.event("alarm_started", source="pending_restore", timestamp=1000)
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 1,
                    "pid": 10,
                    "session_id": "2",
                    "timestamp": 1000.2,
                },
            ]
        )
        self.store.import_jsonl(self.path)
        self.assertEqual(len(self.store.query()), 2)
        self.assertEqual({row["session_id"] for row in self.store.query()}, {"1", "2"})

    def test_partial_or_invalid_json_is_not_marked_imported(self):
        path = self.path / "alarm_events.jsonl"
        path.write_text("{partial\n", encoding="utf-8")
        self.assertEqual(self.store.import_jsonl(self.path)["malformed"], 1)
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": 1,
                    "pid": 1,
                    "timestamp": 1000,
                }
            ]
        )
        self.assertEqual(self.store.import_jsonl(self.path)["imported"], 1)

    def test_missing_timestamp_is_not_replaced_with_current_time(self):
        self.write_log(
            [{"event": "alarm_started", "room": "201", "event_id": 1, "pid": 1}]
        )
        stats = self.store.import_jsonl(self.path)
        self.assertEqual(stats["malformed"], 1)
        self.assertEqual(self.store.query(), [])

    def test_import_skips_invalid_event_id_and_keeps_later_valid_row(self):
        self.write_log(
            [
                {
                    "event": "alarm_started",
                    "room": "201",
                    "event_id": "invalid",
                    "timestamp": 1000,
                },
                {
                    "event": "alarm_started",
                    "room": "202",
                    "event_id": 2,
                    "timestamp": 1001,
                },
            ]
        )
        stats = self.store.import_jsonl(self.path)
        self.assertEqual(stats["malformed"], 1)
        self.assertEqual(stats["imported"], 1)
        self.assertEqual(self.store.query()[0]["room"], "202")

    def test_sqlite_lock_failure_returns_without_unbounded_wait(self):
        connect = sqlite3.connect
        busy_timeouts = []

        def observed_connect(*args, **kwargs):
            connection = connect(*args, **kwargs)
            busy_timeouts.append(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            return connection

        with closing(sqlite3.connect(self.store.db_path, timeout=0.1)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with patch(
                "ui.alarm_history.sqlite3.connect", side_effect=observed_connect
            ) as connections:
                self.assertFalse(
                    self.store.record_event(
                        "alarm_started",
                        event_uid="a",
                        room="201",
                        timestamp=1000,
                    )
                )
            # Assert SQLite's effective wait budget and absence of retries;
            # shared CI runners can deschedule a 250 ms operation for over 1 s.
            connections.assert_called_once()
            self.assertLessEqual(busy_timeouts[0], 250)
            self.assertIsNotNone(self.store.last_error)
            self.assertIn("locked", self.store.last_error)
            blocker.rollback()
        self.assertTrue(
            self.store.record_event(
                "alarm_started", event_uid="a", room="201", timestamp=1000
            )
        )
        self.assertIsNone(self.store.last_error)

    def test_offset_timestamp_is_normalized_to_utc(self):
        self.event("alarm_started", timestamp="2026-11-03T08:00:00+08:00")
        self.assertEqual(
            self.store.query()[0]["started_at"], "2026-11-03T00:00:00.000000+00:00"
        )

    def test_invalid_database_reports_error_without_raising(self):
        blocked = self.path / "parent-file"
        blocked.write_text("not a directory", encoding="utf-8")
        store = AlarmHistoryStore(blocked / "history.db")
        self.assertFalse(store.record_event("alarm_started", event_uid="a", room="201"))
        self.assertEqual(store.query(), [])
        self.assertIsNotNone(store.last_error)


if __name__ == "__main__":
    unittest.main()
