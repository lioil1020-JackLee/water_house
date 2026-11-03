import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui.scada_dialog import ScadaDialog


class FakeWorker(QObject):
    update_signal = pyqtSignal(dict)
    connection_lost_signal = pyqtSignal()
    connection_restored_signal = pyqtSignal()
    health_signal = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, *args):
        super().__init__()
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running


class AlarmUiTests(unittest.TestCase):
    def test_first_clear_after_restart_is_logged_with_restored_occurrence_uid(self):
        from ui.alarm_state import AlarmState

        model = self.window.alarm_state
        occurrence = model.update(201, True)
        model.acknowledge()
        self.window.alarm_state = AlarmState(
            model.storage_path, history_store=self.window.alarm_history
        )
        with patch('ui.scada_dialog.record_alarm_event') as events:
            self.update(201, False)
        events.assert_any_call('alarm_cleared', room='201',
                               event_id=occurrence.event_id, event_uid=occurrence.event_uid)
        self.assertEqual(self.window.alarm_state.pending, [])

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(ScadaDialog, '_setup_system_tray', lambda s: setattr(s, 'tray_icon', SimpleNamespace(show=lambda: None, showMessage=lambda *a: None))),
            patch.object(ScadaDialog, '_initial_scale', lambda s: None),
            patch.object(ScadaDialog, '_do_scale', lambda s: None),
            patch.object(ScadaDialog, '_play_alarm_sound'),
            patch('ui.scada_dialog.record_alarm_event'),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.window = ScadaDialog(start_monitoring=False, alarm_state_path=Path(self.directory.name) / 'pending.json')
        self.app.processEvents()
        self.tags = {key: next(tag for tag, room in self.window.tag_to_room.items()
                               if room == key and '警報狀態' in tag) for key in (201, 202)}

    def tearDown(self):
        if self.window._history_dialog is not None:
            self.assertTrue(self.window._history_dialog.shutdown(3000))
        self.window._closing = True
        self.window._alarm_display_timer.stop()
        self.window._opcua_restart_timer.stop()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        for p in reversed(self.patches):
            p.stop()
        self.directory.cleanup()

    def update(self, room, active):
        self.window._on_opcua_update({self.tags[room]: active})

    def test_second_room_alerts_again_after_first_was_acknowledged(self):
        with patch.object(self.window, '_raise_and_maximize_window') as display:
            self.update(201, True)
            self.window._on_acknowledge_clicked()
            self.update(202, True)
            self.assertEqual(display.call_count, 2)
            self.assertFalse(self.window.is_muted)
            self.assertEqual([e.key for e in self.window.alarm_state.pending], ['202'])

    def test_remote_reset_retains_local_event_and_visible_summary(self):
        self.update(201, True)
        self.update(201, False)
        self.assertEqual(len(self.window.alarm_state.pending), 1)
        self.assertIn('201', self.window.pending_label.text())
        self.assertIsNotNone(self.window.alarm_state.pending[0].cleared_at)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_tray_hidden_window_is_restored_and_acknowledgement_releases_topmost(self):
        self.window.showMinimized()
        self.window.hide()
        self.update(201, True)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.window._on_acknowledge_clicked()
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.window._alarm_display_timer.isActive())

    def test_audio_exception_never_prevents_display(self):
        with patch.object(self.window, '_play_alarm_sound', side_effect=RuntimeError('synthetic audio fault')):
            self.window.hide()
            self.update(201, True)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.window.alarm_state.pending), 1)

    def test_speaker_remains_visible_when_no_pending_alarm(self):
        self.assertEqual(self.window.mute_button.text(), '🔊')
        self.assertEqual(self.window.mute_button.width(), 70)
        self.assertEqual(self.window.mute_button.height(), 70)
        self.assertTrue(self.window.mute_button.isEnabled())
        self.assertFalse(self.window.acknowledge_button.isEnabled())
        self.assertTrue(self.window.pending_button.isEnabled())

    def test_history_is_available_without_pending_and_viewing_never_acknowledges_or_resets(self):
        self.assertEqual(self.window.pending_button.text(), '警報紀錄')
        self.window.pending_button.click()
        dialog = self.window._history_dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIsNone(self.window.history_log_dir)
        self.assertEqual(self.window.alarm_state.pending, [])
        self.update(201, True)
        pending_id = self.window.alarm_state.pending[0].event_id
        self.window.pending_button.click()
        self.assertIs(self.window._history_dialog, dialog)
        self.assertEqual([event.event_id for event in self.window.alarm_state.pending], [pending_id])

    def test_speaker_mute_and_unmute_do_not_acknowledge_or_write_plc(self):
        worker = SimpleNamespace(write_value=Mock())
        self.window.opcua_client = worker
        self.update(201, True)
        pending_id = self.window.alarm_state.pending[0].event_id
        self.window.mute_button.click()
        self.assertTrue(self.window.is_muted)
        self.assertEqual(self.window.mute_button.text(), '🔇')
        self.assertEqual([event.event_id for event in self.window.alarm_state.pending], [pending_id])
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.window.mute_button.click()
        self.assertFalse(self.window.is_muted)
        self.assertEqual(self.window.mute_button.text(), '🔊')
        self.assertEqual([event.event_id for event in self.window.alarm_state.pending], [pending_id])
        worker.write_value.assert_not_called()

    def test_new_alarm_restores_speaker_after_current_alarm_was_muted(self):
        self.update(201, True)
        self.window.mute_button.click()
        self.assertEqual(self.window.mute_button.text(), '🔇')
        self.update(202, True)
        self.assertFalse(self.window.is_muted)
        self.assertEqual(self.window.mute_button.text(), '🔊')
        self.assertEqual(len(self.window.alarm_state.pending), 2)

    def test_acknowledge_button_clears_local_records_and_mutes_without_plc_write(self):
        worker = SimpleNamespace(write_value=Mock())
        self.window.opcua_client = worker
        self.update(201, True)
        self.window.acknowledge_button.click()
        self.assertEqual(self.window.alarm_state.pending, [])
        self.assertTrue(self.window.is_muted)
        self.assertEqual(self.window.mute_button.text(), '🔇')
        self.assertTrue(self.window.room_cards[201].alarm_status)
        worker.write_value.assert_not_called()

    def test_display_retries_are_bounded_and_new_alarm_can_start_new_attempts(self):
        with patch.object(self.window, 'isActiveWindow', return_value=False):
            self.update(201, True)
            self.window._alarm_display_timer.stop()
            self.window._attempt_alarm_display()
            self.window._alarm_display_timer.stop()
            self.window._attempt_alarm_display()
            self.assertEqual(self.window._display_attempts, 3)
            self.assertFalse(self.window._alarm_display_timer.isActive())
            self.update(202, True)
            self.assertEqual(self.window._display_attempts, 1)

    def test_stopped_worker_schedules_recovery_without_clearing_pending(self):
        with patch('ui.scada_dialog.OPCUAClient', FakeWorker):
            self.window._start_opcua_client()
            old = self.window.opcua_client
            self.update(201, True)
            old.running = False
            old.finished.emit()
            self.assertTrue(self.window.monitoring_recovery_active)
            self.assertTrue(self.window._opcua_restart_timer.isActive())
            self.window._start_opcua_client()
            self.assertIsNot(self.window.opcua_client, old)
            self.assertTrue(self.window.opcua_client.isRunning())
            self.assertFalse(self.window.monitoring_recovery_active)
            self.assertIn('201', [e.key for e in self.window.alarm_state.pending])

    def test_partial_health_failure_marks_affected_room_and_preserves_pending(self):
        self.update(201, True)
        self.window._on_opcua_health({'healthy': False, 'connected': True, 'reason': 'one unreadable point',
                                     'bad_tags': [self.tags[201]], 'stale_tags': [], 'unknown_tags': []})
        self.assertIn('失敗 1', self.window.health_label.text())
        self.assertTrue(self.window.room_cards[201].is_disconnected)
        self.assertFalse(self.window.room_cards[202].is_disconnected)
        self.assertEqual(len(self.window.alarm_state.pending), 1)

    def test_timing_tooltip_keeps_requested_and_server_values_distinct(self):
        self.window._on_opcua_health({'healthy': True, 'connected': True, 'reason': 'test',
                                     'timing': {'requested_sampling_interval_ms': 100,
                                                'requested_publishing_interval_ms': 200}})
        tooltip = self.window.health_label.toolTip()
        self.assertIn('取樣要求：100 ms；伺服器核定：未取得', tooltip)
        self.assertIn('發布要求：200 ms；伺服器核定：未取得', tooltip)
        self.window._on_opcua_health({'healthy': True, 'connected': True, 'reason': 'test',
                                     'timing': {'requested_sampling_interval_ms': 100,
                                                'requested_publishing_interval_ms': 200,
                                                'revised_sampling_interval_ms_min': 250,
                                                'revised_sampling_interval_ms_max': 500,
                                                'revised_publishing_interval_ms': 1000}})
        tooltip = self.window.health_label.toolTip()
        self.assertIn('伺服器核定：250 ms ～ 500 ms', tooltip)
        self.assertIn('發布要求：200 ms；伺服器核定：1000 ms', tooltip)

    def test_worker_creation_failure_schedules_retry_and_local_communication_alarm(self):
        with patch('ui.scada_dialog.OPCUAClient', side_effect=RuntimeError('synthetic start failure')):
            self.window._start_opcua_client()
        self.assertTrue(self.window._opcua_restart_timer.isActive())
        self.assertTrue(self.window.monitoring_recovery_active)
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['__connection__'])

    def test_blinking_resumes_after_point_quality_recovers(self):
        card = self.window.room_cards[201]
        card.set_disconnect(False)
        card.set_alarm_status(True)
        card.set_disconnect(True)
        self.assertFalse(card.blink_timer.isActive())
        card.set_disconnect(False)
        self.assertTrue(card.blink_timer.isActive())


if __name__ == '__main__':
    unittest.main()
