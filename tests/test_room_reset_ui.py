"""Offline integration of popup reset writes and per-room acknowledgement."""

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from ui.popup_dialog import PopupDialog
from ui.scada_dialog import ScadaDialog


class ResetWorker(QObject):
    update_signal = pyqtSignal(dict)
    write_failed_signal = pyqtSignal(str)
    write_result_signal = pyqtSignal(str, str, bool, str)

    def __init__(self):
        super().__init__()
        self.writes = []
        self.current_values = {}
        self.write_timestamps = {}
        self.values = {}
        self.accept_writes = True

    def write_value(self, tag, value, *, request_id=None):
        self.writes.append((tag, value, request_id))
        return self.accept_writes

    def read_value(self, tag):
        return self.values.get(tag)

    def complete(self, index, success=True):
        tag, _, request_id = self.writes[index]
        self.write_result_signal.emit(request_id, tag, success,
                                      '' if success else 'synthetic denied write')


class RoomResetUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(ScadaDialog, '_setup_system_tray', lambda s: setattr(
                s, 'tray_icon', SimpleNamespace(show=lambda: None, showMessage=lambda *a: None))),
            patch.object(ScadaDialog, '_initial_scale', lambda s: None),
            patch.object(ScadaDialog, '_do_scale', lambda s: None),
            patch.object(ScadaDialog, '_play_alarm_sound'),
            patch.object(ScadaDialog, '_stop_alarm_sound'),
            patch('ui.scada_dialog.record_alarm_event'),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.window = ScadaDialog(start_monitoring=False,
                                  alarm_state_path=Path(self.directory.name) / 'pending.json')
        self.worker = self.bind_worker(ResetWorker())
        self.popups = []
        self.app.processEvents()

    def bind_worker(self, worker):
        self.window.opcua_client = worker
        worker.update_signal.connect(self.window._on_opcua_update)
        worker.write_result_signal.connect(self.window._on_room_reset_write_result)
        return worker

    def tearDown(self):
        for popup in self.popups:
            popup.reject()
            popup.deleteLater()
        if self.window._history_dialog is not None:
            self.assertTrue(self.window._history_dialog.shutdown(3000))
        self.window._closing = True
        self.window._alarm_display_timer.stop()
        self.window._opcua_restart_timer.stop()
        self.window._connection_alarm_timer.stop()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def update(self, room, active):
        tag = next(tag for tag, key in self.window.tag_to_room.items()
                   if key == room and '警報狀態' in tag)
        self.worker.update_signal.emit({tag: active})

    def reset_tag(self, room=201):
        return self.window.room_to_tags[room]['reset']

    def start_reset(self, room=201):
        operation_id = self.window.begin_room_reset(self.reset_tag(room))
        self.assertIsNotNone(operation_id)
        self.window.finish_room_reset(operation_id)
        return operation_id

    def popup(self, room=201):
        tags = self.window.room_to_tags[room]
        self.worker.values.update({tags['enable']: True, tags['reset']: False, tags['delay']: 0.0})
        self.window.latest_values.update(self.worker.values)
        popup = PopupDialog(initial_state='red', parent_dialog=self.window,
                            enable_tag=tags['enable'], reset_tag=tags['reset'], delay_tag=tags['delay'])
        self.popups.append(popup)
        popup.show()
        self.app.processEvents()
        return popup

    def test_both_writes_must_succeed_and_other_rooms_and_communication_stay_pending(self):
        self.update(201, True)
        self.update(202, True)
        self.window.alarm_state.update('__connection__', True)
        self.window._sync_alarm_presentation()
        self.start_reset()
        self.worker.complete(0)
        self.assertEqual(len(self.window.alarm_state.pending), 3)
        with patch.object(self.window, '_stop_alarm_sound') as stop:
            self.worker.complete(1)
            stop.assert_not_called()
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['202', '__connection__'])
        self.assertFalse(self.window.is_muted)
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.window.room_cards[201].alarm_status)

    def test_last_room_reset_stops_sound_but_never_fakes_live_alarm_clear(self):
        self.update(201, True)
        self.start_reset()
        self.worker.complete(0)
        with patch.object(self.window, '_stop_alarm_sound') as stop:
            self.worker.complete(1)
            stop.assert_called_once()
        self.assertEqual(self.window.alarm_state.pending, [])
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.window.alarm_state.active['201'])
        self.assertTrue(self.window.room_cards[201].alarm_status)

    def test_press_failure_keeps_pending_and_still_attempts_release(self):
        self.update(201, True)
        self.window.begin_room_reset(self.reset_tag())
        self.worker.complete(0, False)
        self.assertEqual([write[1] for write in self.worker.writes], [True, False])
        self.worker.complete(1)
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])
        self.assertEqual(self.window._room_reset_operations, {})

    def test_release_failure_does_not_acknowledge_even_after_successful_press(self):
        self.update(201, True)
        popup = self.popup()
        popup.reset_button.click()
        self.worker.complete(0)
        self.worker.complete(1, False)
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])
        self.assertIn('失敗', popup.reset_status_label.text())

    def test_new_occurrence_in_same_room_during_reset_is_not_acknowledged(self):
        self.update(201, True)
        old_uid = self.window.alarm_state.pending[0].event_uid
        self.start_reset()
        self.update(201, False)
        self.update(201, True)
        new_uid = self.window.alarm_state.pending[-1].event_uid
        self.worker.complete(0)
        self.worker.complete(1)
        self.assertEqual([e.event_uid for e in self.window.alarm_state.pending], [new_uid])
        history = {row['event_uid']: row for row in self.window.alarm_history.query()}
        self.assertIsNotNone(history[old_uid]['acknowledged_at'])
        self.assertIsNone(history[new_uid]['acknowledged_at'])

    def test_global_confirmation_still_confirms_everything_without_plc_writes(self):
        self.update(201, True)
        self.update(202, True)
        self.window.alarm_state.update('__connection__', True)
        self.window._sync_alarm_presentation()
        self.assertEqual(self.window.acknowledge_button.text(), '確認所有警報')
        self.window.acknowledge_button.click()
        self.assertEqual(self.window.alarm_state.pending, [])
        self.assertEqual(self.worker.writes, [])
        self.assertTrue(self.window.is_muted)

    def test_popup_close_releases_pressed_reset_and_late_completion_acknowledges(self):
        self.update(201, True)
        popup = self.popup()
        QTest.mousePress(popup.reset_button, Qt.MouseButton.LeftButton)
        self.assertEqual([write[1] for write in self.worker.writes], [True])
        popup.reject()
        self.assertEqual([write[1] for write in self.worker.writes], [True, False])
        self.worker.complete(0)
        self.worker.complete(1)
        self.assertEqual(self.window.alarm_state.pending, [])

    def test_replaced_worker_results_cannot_acknowledge_pending_alarms(self):
        self.update(201, True)
        self.start_reset()
        old = self.worker
        self.bind_worker(ResetWorker())
        old.complete(0)
        old.complete(1)
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])
        self.assertEqual(self.window._room_reset_operations, {})

    def test_accepting_card_popup_does_not_repeat_reset_or_write_delay(self):
        self.update(201, True)
        popup = self.popup()
        popup.hide()
        with patch('ui.scada_dialog.PopupDialog', return_value=popup):
            QTimer.singleShot(0, popup.accept)
            self.window.room_cards[201]._on_light_clicked()
        self.assertEqual(self.worker.writes, [])
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])

    def test_public_room_mapping_resets_only_that_public_room(self):
        room = next(key for key in self.window.room_to_tags if str(key).startswith('public_'))
        self.update(room, True)
        self.update(201, True)
        self.start_reset(room)
        self.assertEqual([write[:2] for write in self.worker.writes],
                         [(self.reset_tag(room), True), (self.reset_tag(room), False)])
        self.worker.complete(0)
        self.worker.complete(1)
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])

    def test_rejected_scheduling_keeps_pending_and_cleans_operation(self):
        self.update(201, True)
        self.worker.accept_writes = False
        statuses = []
        self.window.room_reset_status_signal.connect(lambda tag, message: statuses.append(message))
        self.window.begin_room_reset(self.reset_tag())
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201'])
        self.assertEqual(self.window._room_reset_operations, {})
        self.assertTrue(any('失敗' in message for message in statuses))

    def test_reset_values_from_server_are_not_a_local_user_acknowledgement(self):
        self.update(201, True)
        self.update(202, True)
        self.worker.update_signal.emit({self.reset_tag(): True})
        self.assertEqual([e.key for e in self.window.alarm_state.pending], ['201', '202'])
        self.assertEqual(self.worker.writes, [])


if __name__ == '__main__':
    unittest.main()
