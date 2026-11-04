"""Network-free smoke checks executed by each built release binary."""

import os
from pathlib import Path
import tempfile
import time
import traceback

from runtime_support import APP_VERSION, atomic_json_write


def run_self_test(output: Path) -> int:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from PyQt6.QtGui import QFont, QFontDatabase
    from PyQt6.QtWidgets import QApplication
    from ui.scada_dialog import ScadaDialog

    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="water-house-self-test-") as directory:
        app = QApplication.instance() or QApplication([])
        # The offscreen platform has no system font database on Windows.
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msjh.ttc"
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))
            app.setFont(QFont("Microsoft JhengHei", 10))
        app.setQuitOnLastWindowClosed(False)
        window = ScadaDialog(
            start_monitoring=False, alarm_state_path=Path(directory) / "pending.json"
        )
        window._play_alarm_sound = lambda: None
        window._stop_alarm_sound = lambda: None

        class OfflineSource(QObject):
            update_signal = pyqtSignal(dict)

        source = OfflineSource()
        window.opcua_client = source
        source.update_signal.connect(window._on_opcua_update)
        result = {
            "version": APP_VERSION,
            "network_access": False,
            "passed": False,
            "checks": {},
        }

        def wait_history():
            deadline = time.monotonic() + 3
            while window._history_dialog.is_loading and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            app.processEvents()
            if window._history_dialog.is_loading:
                raise TimeoutError("offline history query did not finish")

        def finish():
            try:
                result["checks"][
                    "history_available_without_alarm"
                ] = window.pending_button.isEnabled()
                window.pending_button.click()
                wait_history()
                result["checks"]["empty_history_dialog_opens"] = (
                    window._history_dialog.isVisible()
                    and window._history_dialog.table.rowCount() == 0
                )
                window._history_dialog.reject()
                status_tags = [tag for tag in window.tag_to_room if "警報狀態" in tag]
                enables = {
                    room: tag
                    for tag, room in window.tag_to_room.items()
                    if "警報開關" in tag
                }
                room_a, room_b = (window.tag_to_room[tag] for tag in status_tags[:2])
                first, second = status_tags[:2]
                source.update_signal.emit({enables[room_a]: True, first: True})
                app.processEvents()
                result["checks"]["first_alarm_pending"] = (
                    len(window.alarm_state.pending) == 1
                )
                window.showMinimized()
                app.processEvents()
                source.update_signal.emit({enables[room_b]: True, second: True})
                app.processEvents()
                result["checks"]["second_alarm_not_suppressed"] = (
                    len(window.alarm_state.pending) == 2 and not window.is_muted
                )
                result["checks"]["restored_from_tray"] = (
                    window.isVisible() and not window.isMinimized()
                )
                source.update_signal.emit({first: False, second: False})
                result["checks"]["server_reset_keeps_local_pending"] = (
                    len(window.alarm_state.pending) == 2
                )
                result["checks"]["pending_saved"] = (
                    Path(directory) / "pending.json"
                ).exists()
                window.grab().save(str(output.with_suffix(".png")))
                window._on_mute_clicked()
                result["checks"]["speaker_mute_keeps_pending"] = (
                    window.is_muted
                    and window.mute_button.text() == "🔇"
                    and len(window.alarm_state.pending) == 2
                )
                window._on_acknowledge_clicked()
                result["checks"]["local_acknowledgement_clears_pending"] = (
                    len(window.alarm_state.pending) == 0
                )
                window.pending_button.click()
                wait_history()
                rows = window.alarm_history.query()
                result["checks"]["history_survives_acknowledgement"] = len(
                    rows
                ) == 2 and all(row["acknowledged_at"] for row in rows)
                result["checks"]["history_table_shows_acknowledged_records"] = (
                    window._history_dialog.table.rowCount() == 2
                    and len(window.alarm_state.pending) == 0
                )
                window._history_dialog.grab().save(
                    str(output.with_name(output.stem + "-history.png"))
                )
                window._on_connection_lost()
                result["checks"]["communication_alarm_waits_sixty_seconds"] = (
                    window.CONNECTION_ALARM_DELAY_SECONDS == 60.0
                    and window._connection_alarm_timer.isActive()
                    and not window.alarm_state.pending
                )
                source.update_signal.emit({first: True})
                result["checks"]["room_alarm_immediate_during_communication_grace"] = (
                    [event.key for event in window.alarm_state.pending] == [str(room_a)]
                )
                window._on_connection_restored()
                result["checks"]["short_communication_fault_cancelled"] = (
                    not window._connection_alarm_timer.isActive()
                    and window._connection_fault_since is None
                    and [event.key for event in window.alarm_state.pending] == [str(room_a)]
                )
                window._on_acknowledge_clicked()
                source.update_signal.emit({first: False})
                window._on_connection_lost()
                # Advance only the offline candidate's age; exercise the real
                # Qt timer callback without delaying each packaged build a minute.
                window._connection_fault_since -= window.CONNECTION_ALARM_DELAY_SECONDS
                window._connection_alarm_timer.start(0)
                app.processEvents()
                result["checks"]["sustained_communication_fault_confirmed"] = (
                    [event.key for event in window.alarm_state.pending] == ["__connection__"]
                )
                window._on_connection_restored()
                result["checks"]["recovered_communication_fault_stays_pending"] = (
                    len(window.alarm_state.pending) == 1
                    and window.alarm_state.pending[0].cleared_at is not None
                    and "已恢復" in window.pending_label.text()
                )
                result["checks"]["opc_not_started"] = window.opcua_client is source
                result["checks"]["all_tags_mapped"] = len(window.tag_to_room) == 148
                result["passed"] = all(result["checks"].values())
            except Exception:
                result["error"] = traceback.format_exc()
            finally:
                window._connection_alarm_timer.stop()
                if window._history_dialog is not None:
                    window._history_dialog.shutdown(3000)
                atomic_json_write(output, result)
                window.opcua_client = None
                window.hide()
                app.exit(0 if result["passed"] else 1)

        QTimer.singleShot(800, finish)
        return app.exec()
