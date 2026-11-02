import json
from pathlib import Path
import tempfile
import unittest

from ui.alarm_state import AlarmState


class AlarmStateTests(unittest.TestCase):
    def test_each_room_has_an_independent_edge(self):
        state = AlarmState()
        first = state.update(201, True, 'first')
        second = state.update(202, True, 'second')
        self.assertEqual([e.key for e in state.pending], ['201', '202'])
        self.assertNotEqual(first.event_id, second.event_id)
        self.assertIsNone(state.update(201, True))

    def test_server_clear_and_other_room_reset_do_not_acknowledge(self):
        state = AlarmState()
        state.update(201, True, 'start')
        state.update(201, False, 'clear')
        self.assertEqual(len(state.pending), 1)
        self.assertEqual(state.pending[0].cleared_at, 'clear')
        self.assertFalse(state.active['201'])

    def test_acknowledgement_does_not_change_live_or_plc_state(self):
        state = AlarmState()
        state.update(201, True)
        acknowledged = state.acknowledge()
        self.assertEqual(len(acknowledged), 1)
        self.assertTrue(state.active['201'])
        self.assertIsNone(state.update(201, True))
        state.update(201, False)
        self.assertIsNotNone(state.update(201, True))

    def test_recurrence_preserves_both_unacknowledged_occurrences(self):
        state = AlarmState()
        state.update(201, True, 'one')
        state.update(201, False, 'two')
        state.update(201, True, 'three')
        self.assertEqual(len(state.pending), 2)
        self.assertEqual([e.cleared_at for e in state.pending], ['two', None])

    def test_cleared_event_id_remains_traceable_after_local_acknowledgement(self):
        state = AlarmState()
        event = state.update(201, True)
        state.acknowledge()
        self.assertEqual(state.event_id_for(201), event.event_id)
        state.update(201, False)
        self.assertIsNone(state.event_id_for(201))

    def test_connection_alarm_never_masks_room_alarm(self):
        state = AlarmState()
        state.update('__connection__', True)
        self.assertIsNotNone(state.update(201, True))
        self.assertEqual(len(state.pending), 2)

    def test_pending_and_acknowledgement_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending.json'
            state = AlarmState(path)
            state.update(201, True)
            state.update(201, False)
            restored = AlarmState(path)
            self.assertEqual(len(restored.pending), 1)
            self.assertIsNotNone(restored.pending[0].cleared_at)
            restored.acknowledge()
            self.assertEqual(AlarmState(path).pending, [])

    def test_snapshot_after_restart_does_not_duplicate_pending_live_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending.json'
            AlarmState(path).update(201, True)
            restored = AlarmState(path)
            self.assertIsNone(restored.update(201, True))
            self.assertEqual(len(restored.pending), 1)
            restored.update(201, False)
            self.assertIsNotNone(restored.update(201, True))

    def test_corrupt_storage_is_preserved_for_investigation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pending.json'
            path.write_text('{broken', encoding='utf-8')
            state = AlarmState(path)
            self.assertIsNotNone(state.storage_error)
            self.assertEqual(len(list(Path(directory).glob('pending.json.invalid-*'))), 1)
            state.update('__storage__', True)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['pending'][0]['key'], '__storage__')

    def test_storage_failure_keeps_event_available_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            blocking = Path(directory) / 'file'
            blocking.write_text('file', encoding='utf-8')
            state = AlarmState(blocking / 'pending.json')
            state.update(201, True)
            self.assertEqual(len(state.pending), 1)
            self.assertIsNotNone(state.storage_error)


if __name__ == '__main__':
    unittest.main()
