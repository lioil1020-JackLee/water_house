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
            self.window._on_mute_clicked()
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
        self.window._on_mute_clicked()
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.window._alarm_display_timer.isActive())

    def test_audio_exception_never_prevents_display(self):
        with patch.object(self.window, '_play_alarm_sound', side_effect=RuntimeError('synthetic audio fault')):
            self.window.hide()
            self.update(201, True)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(len(self.window.alarm_state.pending), 1)

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
