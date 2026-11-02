import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from runtime_support import (
    APP_VERSION, AlarmEventFormatter, atomic_json_write, heartbeat_payload, write_heartbeat,
)


class RuntimeSupportTests(unittest.TestCase):
    def test_heartbeat_distinguishes_running_reconnect_from_dead_worker(self):
        client = SimpleNamespace(
            isRunning=lambda: True,
            health_snapshot=lambda: {"last_data_at": 10, "monitoring_healthy": False},
        )
        window = SimpleNamespace(opcua_client=client, monitoring_recovery_active=False)
        alive = heartbeat_payload(window, now=100)
        self.assertTrue(alive["worker_alive"])
        self.assertFalse(alive["monitoring_healthy"])
        self.assertEqual(alive["last_data_at"], 10)
        client.isRunning = lambda: False
        dead = heartbeat_payload(window, now=101)
        self.assertFalse(dead["worker_alive"])
        self.assertEqual(dead["updated_at"], 101)

    def test_atomic_heartbeat_has_both_pid_and_diagnostic_files(self):
        window = SimpleNamespace(opcua_client=None, monitoring_recovery_active=True)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            self.assertTrue(write_heartbeat(directory, window))
            per_pid = json.loads((directory / f"water_house.{os.getpid()}.heartbeat").read_text("utf-8"))
            legacy = json.loads((directory / "water_house.heartbeat").read_text("utf-8"))
            self.assertEqual(per_pid, legacy)
            self.assertEqual(per_pid["pid"], os.getpid())
            self.assertEqual(per_pid["version"], APP_VERSION)
            self.assertTrue(per_pid["recovery_active"])
            self.assertEqual(len(list(directory.iterdir())), 2)

    def test_atomic_json_replaces_an_existing_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            atomic_json_write(path, {"room": "201", "pending": True})
            atomic_json_write(path, {"room": "201", "pending": False})
            self.assertFalse(json.loads(path.read_text("utf-8"))["pending"])
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_event_formatter_retains_machine_readable_details(self):
        import logging
        record = logging.LogRecord("water_house.alarms", logging.INFO, "", 0, "alarm", (), None)
        record.alarm_event = {"event": "alarm_received", "room": "201", "event_id": 7}
        payload = json.loads(AlarmEventFormatter().format(record))
        self.assertEqual(payload["room"], "201")
        self.assertEqual(payload["event_id"], 7)
        self.assertEqual(payload["version"], APP_VERSION)
        self.assertIn("timestamp", payload)


if __name__ == "__main__":
    unittest.main()
