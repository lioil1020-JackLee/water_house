import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import threading
import time
import unittest

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QAbstractItemView, QApplication

from ui.alarm_history_dialog import AlarmHistoryDialog, display_timestamp


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []
        self.imports = []
        self.last_error = None

    def query(self, **kwargs):
        self.calls.append(kwargs)
        rows = self.rows
        if kwargs["pending_only"]:
            rows = [row for row in rows if row.get("acknowledged_at") is None]
        if kwargs["text"]:
            rows = [row for row in rows if kwargs["text"] in row["room"]]
        return rows[: kwargs["limit"]]

    def import_jsonl(self, directory):
        self.imports.append((Path(directory), threading.get_ident()))


def sample_row(**fields):
    return {
        "event_uid": "test-event",
        "room": "201",
        "started_at": "2026-11-03T00:01:02+00:00",
        "cleared_at": "2026-11-03T00:02:03+00:00",
        "acknowledged_at": "2026-11-03T00:03:04+00:00",
        "alarm_status": "cleared",
        "source": "live",
        **fields,
    }


class HistoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            self.assertTrue(dialog.shutdown(3000))
            dialog.deleteLater()
        self.app.processEvents()

    def dialog(self, store, **kwargs):
        dialog = AlarmHistoryDialog(store, **kwargs)
        self.dialogs.append(dialog)
        return dialog

    def wait_loaded(self, dialog):
        deadline = time.monotonic() + 3
        while dialog.is_loading and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(dialog.is_loading)
        self.app.processEvents()

    def test_empty_history_opens_and_can_refresh(self):
        store = FakeStore()
        dialog = self.dialog(store)
        dialog.show()
        self.wait_loaded(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertIn("沒有符合條件", dialog.status_label.text())
        self.assertTrue(dialog.refresh_button.isEnabled())
        dialog.refresh_button.click()
        self.wait_loaded(dialog)
        self.assertEqual(len(store.calls), 2)

    def test_acknowledged_record_is_read_only_and_uses_taipei_time(self):
        row = sample_row()
        store = FakeStore([row])
        dialog = self.dialog(store, room_description=lambda room: f"2F {room}客房")
        self.wait_loaded(dialog)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "2F 201客房")
        self.assertEqual(dialog.table.item(0, 1).text(), "2026-11-03 08:01:02")
        self.assertEqual(dialog.table.item(0, 2).text(), "已收到復歸")
        self.assertEqual(dialog.table.item(0, 4).text(), "已確認")
        self.assertEqual(
            dialog.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.assertEqual(store.rows, [row])

    def test_filters_pass_parameters_and_keep_only_requested_rows(self):
        store = FakeStore(
            [
                sample_row(),
                sample_row(room="202", event_uid="other", acknowledged_at=None),
            ]
        )
        dialog = self.dialog(store)
        self.wait_loaded(dialog)
        dialog.scope_combo.setCurrentIndex(1)
        self.wait_loaded(dialog)
        self.assertTrue(store.calls[-1]["pending_only"])
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.search_edit.setText("201")
        dialog.refresh_button.click()
        self.wait_loaded(dialog)
        self.assertEqual(store.calls[-1]["text"], "201")
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_missing_clear_is_described_as_unreceived_rather_than_proven_current_alarm(
        self,
    ):
        dialog = self.dialog(
            FakeStore(
                [
                    sample_row(
                        cleared_at=None,
                        acknowledged_at=None,
                        alarm_status="active",
                        source="legacy_jsonl",
                    )
                ]
            )
        )
        self.wait_loaded(dialog)
        self.assertEqual(dialog.table.item(0, 2).text(), "未收到復歸紀錄")
        self.assertEqual(dialog.table.item(0, 3).text(), "—")
        self.assertEqual(dialog.table.item(0, 6).text(), "舊版紀錄匯入（桌面未記錄）")
        self.assertEqual(dialog.table.item(0, 4).text(), "未記錄")

    def test_legacy_fragment_with_no_start_is_explicitly_marked_unrecorded(self):
        dialog = self.dialog(
            FakeStore(
                [
                    sample_row(
                        started_at=None, acknowledged_at=None, source="legacy_jsonl"
                    )
                ]
            )
        )
        self.wait_loaded(dialog)
        self.assertEqual(dialog.table.item(0, 1).text(), "未記錄")
        self.assertEqual(dialog.table.item(0, 4).text(), "未記錄")

    def test_other_desktop_is_distinguished_from_current_pending_alarms(self):
        store = FakeStore([sample_row(session_id="2")])
        store.session_id = "1"
        dialog = self.dialog(store)
        self.wait_loaded(dialog)
        self.assertEqual(dialog.table.item(0, 6).text(), "本機接收（其他桌面 2）")

    def test_import_is_background_and_disabled_when_no_log_directory(self):
        main_thread = threading.get_ident()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            dialog = self.dialog(store, log_dir=directory)
            self.wait_loaded(dialog)
        self.assertEqual(len(store.imports), 1)
        self.assertNotEqual(store.imports[0][1], main_thread)
        isolated_store = FakeStore()
        dialog = self.dialog(isolated_store)
        self.wait_loaded(dialog)
        self.assertEqual(isolated_store.imports, [])

    def test_slow_query_does_not_block_gui_and_closing_waits_for_thread(self):
        release = threading.Event()
        entered = threading.Event()

        class SlowStore(FakeStore):
            def query(self, **kwargs):
                entered.set()
                release.wait(2)
                return super().query(**kwargs)

        dialog = self.dialog(SlowStore())
        dialog.show()
        self.assertTrue(entered.wait(1))
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        self.app.processEvents()
        self.assertTrue(ticks)
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.shutdown(1))
        release.set()
        self.wait_loaded(dialog)
        self.assertFalse(dialog.isVisible())

    def test_import_warning_is_retained_even_when_history_query_succeeds(self):
        class WarningStore(FakeStore):
            def import_jsonl(self, directory):
                return {
                    "warnings": ["舊版來源沒有工作階段，未跨程序推測復歸。"],
                    "malformed": 2,
                }

        with tempfile.TemporaryDirectory() as directory:
            dialog = self.dialog(WarningStore([sample_row()]), log_dir=directory)
            self.wait_loaded(dialog)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertIn("未跨程序推測復歸", dialog.status_label.text())
        self.assertIn("2 筆不完整", dialog.status_label.text())

    def test_import_failure_does_not_hide_existing_database_records(self):
        class ImportFailureStore(FakeStore):
            def import_jsonl(self, directory):
                raise OSError("synthetic import fault")

        with tempfile.TemporaryDirectory() as directory:
            dialog = self.dialog(ImportFailureStore([sample_row()]), log_dir=directory)
            self.wait_loaded(dialog)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertIn("synthetic import fault", dialog.status_label.text())

    def test_query_error_is_shown_and_does_not_close_dialog(self):
        class BrokenStore(FakeStore):
            def query(self, **kwargs):
                raise OSError("synthetic query fault")

        dialog = self.dialog(BrokenStore())
        dialog.show()
        self.wait_loaded(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertIn("synthetic query fault", dialog.status_label.text())
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_ambiguous_or_invalid_old_time_is_not_given_an_invented_offset(self):
        self.assertIn("時區未記錄", display_timestamp("2026-11-03T08:00:00"))
        self.assertEqual(display_timestamp("bad-time"), "時間資料不完整")
        self.assertEqual(display_timestamp(None), "—")


if __name__ == "__main__":
    unittest.main()
