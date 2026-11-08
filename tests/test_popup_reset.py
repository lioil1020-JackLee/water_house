import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from unittest.mock import Mock

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from ui.popup_dialog import PopupDialog


class PopupWorker(QObject):
    update_signal = pyqtSignal(dict)
    write_failed_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_values = {}
        self.write_timestamps = {}
        self.write_value = Mock()


class PopupParent(QObject):
    room_reset_status_signal = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.opcua_client = PopupWorker()
        self.begin_room_reset = Mock(return_value='operation-1')
        self.finish_room_reset = Mock()
        self.values = {'enable': True, 'reset': False, 'delay': 2.0}

    def get_latest_value(self, tag):
        return self.values.get(tag)


class PopupResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.parent = PopupParent()
        self.popup = self.make_popup(self.parent)

    def make_popup(self, parent):
        popup = PopupDialog(
            initial_state='red', initial_delay=2.0, parent_dialog=parent,
            enable_tag='enable', reset_tag='reset', delay_tag='delay',
        )
        popup.show()
        self.app.processEvents()
        return popup

    def tearDown(self):
        self.popup.reject()
        self.popup.deleteLater()
        self.app.processEvents()

    def press(self):
        QTest.mousePress(self.popup.reset_button, Qt.MouseButton.LeftButton)

    def release(self):
        QTest.mouseRelease(self.popup.reset_button, Qt.MouseButton.LeftButton)

    def test_reset_button_routes_one_press_and_release_without_faking_server_state(self):
        updates = []
        self.parent.opcua_client.update_signal.connect(updates.append)
        self.press()
        self.parent.begin_room_reset.assert_called_once_with('reset')
        self.parent.finish_room_reset.assert_not_called()
        self.assertTrue(self.popup.alarm_toggle.isChecked())
        self.assertEqual(self.popup.selected_state, 'red')
        self.release()
        self.parent.finish_room_reset.assert_called_once_with('operation-1')
        self.parent.opcua_client.write_value.assert_not_called()
        self.assertEqual(self.parent.opcua_client.current_values, {})
        self.assertEqual(updates, [])

    def test_repeated_press_during_hold_does_not_start_another_operation(self):
        self.press()
        self.popup.reset_button.pressed.emit()
        self.parent.begin_room_reset.assert_called_once_with('reset')
        self.release()
        self.popup.reset_button.released.emit()
        self.parent.finish_room_reset.assert_called_once_with('operation-1')
        self.popup.reset_button.click()
        self.assertEqual(self.parent.begin_room_reset.call_count, 2)
        self.assertEqual(self.parent.finish_room_reset.call_count, 2)

    def test_every_dialog_exit_releases_a_held_reset_exactly_once(self):
        for action in ('close', 'accept', 'reject', 'done'):
            with self.subTest(action=action):
                self.popup.reject()
                self.popup.deleteLater()
                self.app.processEvents()
                self.parent = PopupParent()
                self.popup = self.make_popup(self.parent)
                self.popup.update_timer = QTimer(self.popup)
                self.popup.update_timer.start(10000)
                self.press()
                if action == 'done':
                    self.popup.done(QDialog.DialogCode.Accepted)
                else:
                    getattr(self.popup, action)()
                self.popup.reset_button.released.emit()
                self.parent.finish_room_reset.assert_called_once_with('operation-1')
                self.assertFalse(self.popup.update_timer.isActive())
                self.assertEqual(self.popup._live_connections, [])

    def test_status_feedback_is_visible_and_filtered_to_this_reset_tag(self):
        self.assertTrue(self.popup.reset_status_label.isVisible())
        before = self.popup.reset_status_label.text()
        self.parent.room_reset_status_signal.emit('another-room', 'other result')
        self.assertEqual(self.popup.reset_status_label.text(), before)
        self.parent.room_reset_status_signal.emit('reset', '本房復歸指令已完成')
        self.assertEqual(self.popup.reset_status_label.text(), '本房復歸指令已完成')
        self.assertEqual(self.popup.reset_status_label.toolTip(), '本房復歸指令已完成')

    def test_synchronous_failure_feedback_is_not_overwritten_by_pending_text(self):
        def fail(tag):
            self.parent.room_reset_status_signal.emit(tag, '無法寫入：未連線')
            return None

        self.parent.begin_room_reset.side_effect = fail
        self.popup.reset_button.click()
        self.assertEqual(self.popup.reset_status_label.text(), '無法寫入：未連線')
        self.parent.finish_room_reset.assert_not_called()
        self.assertTrue(self.popup.alarm_toggle.isChecked())

    def test_missing_handler_never_reports_success_or_disables_alarm(self):
        self.parent.begin_room_reset = None
        self.popup.reset_button.click()
        self.assertIn('無法復歸', self.popup.reset_status_label.text())
        self.assertTrue(self.popup.alarm_toggle.isChecked())
        self.parent.finish_room_reset.assert_not_called()

    def test_reset_begin_exception_is_visible_and_preserves_alarm_enable(self):
        self.parent.begin_room_reset.side_effect = RuntimeError('synthetic failure')
        self.popup.reset_button.click()
        self.assertIn('無法送出', self.popup.reset_status_label.text())
        self.assertTrue(self.popup.alarm_toggle.isChecked())
        self.parent.finish_room_reset.assert_not_called()

    def test_reset_release_exception_is_visible_and_not_retried_by_dialog_close(self):
        self.parent.finish_room_reset.side_effect = RuntimeError('synthetic failure')
        self.popup.reset_button.click()
        self.assertIn('釋放未成功', self.popup.reset_status_label.text())
        self.popup.reject()
        self.parent.finish_room_reset.assert_called_once_with('operation-1')

    def test_old_worker_signals_cannot_overwrite_new_reset_result_or_values(self):
        previous = self.parent.opcua_client
        self.parent.opcua_client = PopupWorker()
        self.parent.room_reset_status_signal.emit('reset', '本房復歸成功')
        self.parent.values['enable'] = False
        previous.write_failed_signal.emit('reset')
        previous.update_signal.emit({'enable': False})
        self.assertEqual(self.popup.reset_status_label.text(), '本房復歸成功')
        self.assertTrue(self.popup.alarm_toggle.isChecked())
        self.parent.room_reset_status_signal.emit('reset', '新的操作結果')
        self.assertEqual(self.popup.reset_status_label.text(), '新的操作結果')

    def test_live_reset_true_does_not_change_alarm_enable_or_write_back(self):
        self.parent.values['reset'] = True
        self.parent.opcua_client.update_signal.emit({'reset': True})
        self.assertTrue(self.popup.alarm_toggle.isChecked())
        self.assertEqual(self.popup.selected_state, 'red')
        self.parent.opcua_client.write_value.assert_not_called()
        self.parent.values['enable'] = False
        self.parent.opcua_client.update_signal.emit({'enable': False})
        self.assertFalse(self.popup.alarm_toggle.isChecked())
        self.assertEqual(self.popup.selected_state, 'green')
        self.parent.opcua_client.write_value.assert_not_called()

    def test_close_disconnects_original_worker_even_if_parent_replaced_it(self):
        old = self.parent.opcua_client
        self.parent.opcua_client = PopupWorker()
        self.popup.reject()
        before = self.popup.reset_status_label.text()
        old.write_failed_signal.emit('reset')
        self.parent.room_reset_status_signal.emit('reset', 'late result')
        self.assertEqual(self.popup.reset_status_label.text(), before)
        self.assertEqual(old.receivers(old.update_signal), 0)
        self.assertEqual(old.receivers(old.write_failed_signal), 0)
        self.assertEqual(self.parent.receivers(self.parent.room_reset_status_signal), 0)


if __name__ == '__main__':
    unittest.main()
