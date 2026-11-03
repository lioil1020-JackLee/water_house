"""Read-only, asynchronous alarm history for the operator's local PC."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

TAIPEI = timezone(timedelta(hours=8), name="Asia/Taipei")


def display_timestamp(value):
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                # Never invent an offset for an ambiguous old log timestamp.
                return str(value) + "（時區未記錄）"
        return parsed.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OverflowError, OSError):
        return "時間資料不完整"


class HistoryQueryThread(QThread):
    loaded = pyqtSignal(object)

    def __init__(
        self,
        store,
        *,
        log_dir=None,
        text="",
        pending_only=False,
        limit=500,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.text = text
        self.pending_only = pending_only
        self.limit = limit

    def run(self):
        warnings = []
        try:
            if self.log_dir is not None and not self.isInterruptionRequested():
                try:
                    summary = self.store.import_jsonl(self.log_dir) or {}
                    warnings.extend(str(item) for item in summary.get("warnings", []))
                    if summary.get("malformed"):
                        warnings.append(
                            f'已跳過 {summary["malformed"]} 筆不完整的舊版紀錄。'
                        )
                except Exception as exc:
                    # Existing DB history remains queryable if importing an old
                    # log fails. Import warnings must survive a successful query.
                    warnings.append(f"舊版紀錄匯入異常：{exc}")
            if self.isInterruptionRequested():
                return
            rows = self.store.query(
                limit=self.limit, text=self.text, pending_only=self.pending_only
            )
            error = getattr(self.store, "last_error", None)
            self.loaded.emit({"rows": rows, "error": error, "warnings": warnings})
        except Exception as exc:
            self.loaded.emit({"rows": [], "error": str(exc), "warnings": warnings})


class AlarmHistoryDialog(QDialog):
    """Viewing and filtering never acknowledge an alarm or write a PLC tag."""

    MAX_ROWS = 500
    HEADERS = (
        "房間／警報項目",
        "發生時間（臺北）",
        "現場復歸紀錄",
        "復歸時間（臺北）",
        "本機確認",
        "確認時間（臺北）",
        "紀錄來源",
    )

    def __init__(self, store, parent=None, *, log_dir=None, room_description=None):
        super().__init__(parent)
        self.setWindowTitle("本機警報紀錄")
        self.resize(1200, 640)
        self.setMinimumSize(760, 350)
        self.store = store
        self.log_dir = log_dir
        self.room_description = room_description or str
        self._query_thread = None
        self._refresh_queued = False
        self._close_when_finished = False
        self._rows = []

        layout = QVBoxLayout(self)
        explanation = QLabel(
            "可查看已確認與未確認的本機警報。查看、篩選與關閉此視窗，都不會確認警報或復歸現場壓扣。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("輸入房號或警報代碼，例如 201")
        self.search_edit.returnPressed.connect(self.refresh)
        filters.addWidget(self.search_edit, 1)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("全部紀錄", False)
        self.scope_combo.addItem("未確認紀錄", True)
        self.scope_combo.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.scope_combo)
        self.refresh_button = QPushButton("查詢／重新整理")
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.refresh_button)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table, 1)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        note = QLabel(
            "時間顯示為臺北時間；發生與復歸時間是本機收到通知的時間。復歸狀態依本機收到的紀錄；即時連線狀態請看主畫面。"
            "紀錄可含其他登入桌面，主畫面的確認只處理目前桌面的待確認警報。"
            "更舊、未被本機保存的警報無法回補。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("關閉")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    @property
    def is_loading(self):
        return self._query_thread is not None

    def refresh(self, *_args):
        if self._close_when_finished:
            return
        if self._query_thread is not None:
            self._refresh_queued = True
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText("讀取本機警報紀錄中…")
        thread = HistoryQueryThread(
            self.store,
            log_dir=self.log_dir,
            text=self.search_edit.text().strip(),
            pending_only=bool(self.scope_combo.currentData()),
            limit=self.MAX_ROWS,
            parent=self,
        )
        self._query_thread = thread
        thread.loaded.connect(self._on_loaded)
        thread.finished.connect(self._on_query_finished)
        thread.start()

    def _on_loaded(self, result):
        if self._close_when_finished or self._refresh_queued:
            return
        self._rows = list(result["rows"])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))
        for row_index, row in enumerate(self._rows):
            status = row.get("alarm_status")
            cleared = row.get("cleared_at")
            acknowledged = row.get("acknowledged_at")
            field_status = (
                "已收到復歸"
                if cleared is not None or status == "cleared"
                else "未收到復歸紀錄" if status == "active" else "未記錄／待更新"
            )
            source = row.get("source")
            acknowledgement = (
                "已確認"
                if acknowledged is not None
                else "未記錄" if source == "legacy_jsonl" else "未確認"
            )
            source_text = {
                "legacy_jsonl": "舊版紀錄匯入",
                "pending_restore": "待確認紀錄還原",
                "live": "本機接收",
            }.get(source, "來源未記錄")
            session_id = row.get("session_id")
            if session_id is None:
                source_text += "（桌面未記錄）"
            elif str(session_id) == str(getattr(self.store, "session_id", None)):
                source_text += "（目前桌面）"
            else:
                source_text += f"（其他桌面 {session_id}）"
            values = (
                self.room_description(row.get("room", row.get("key", ""))),
                (
                    display_timestamp(row.get("started_at"))
                    if row.get("started_at") is not None
                    else "未記錄"
                ),
                field_status,
                display_timestamp(cleared),
                acknowledgement,
                display_timestamp(acknowledged),
                source_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, row.get("event_uid"))
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        if result.get("error"):
            self.status_label.setText(
                f"警報紀錄讀取異常：{result['error']}。此視窗不會改變目前警報。"
            )
        elif self._rows:
            self.status_label.setText(
                f"顯示最近 {len(self._rows)} 筆符合條件的紀錄（最多 {self.MAX_ROWS} 筆）。"
            )
        else:
            self.status_label.setText(
                "目前沒有符合條件的本機警報紀錄；新收到的警報會保存於這台電腦。"
            )
        warnings = list(dict.fromkeys(result.get("warnings", [])))
        self.status_label.setToolTip("\n".join(warnings))
        if warnings:
            brief = "；".join(warnings[:2])
            if len(warnings) > 2:
                brief += "；其餘訊息請查看此列提示"
            self.status_label.setText(self.status_label.text() + "\n注意：" + brief)

    def _on_query_finished(self):
        thread = self._query_thread
        self._query_thread = None
        if thread is not None:
            thread.deleteLater()
        self.refresh_button.setEnabled(True)
        if self._close_when_finished:
            self._close_when_finished = False
            super().reject()
        elif self._refresh_queued:
            self._refresh_queued = False
            self.refresh()

    def _defer_close(self):
        if self._query_thread is not None and self._query_thread.isRunning():
            self._close_when_finished = True
            self._refresh_queued = False
            self._query_thread.requestInterruption()
            self.status_label.setText("正在結束讀取，完成後會關閉視窗…")
            return True
        return False

    def reject(self):
        if not self._defer_close():
            super().reject()

    def closeEvent(self, event):
        if self._defer_close():
            event.ignore()
        else:
            super().closeEvent(event)

    def shutdown(self, timeout_ms=1500):
        """Allow the main window to wait safely before app shutdown."""
        self._refresh_queued = False
        thread = self._query_thread
        if thread is not None and thread.isRunning():
            thread.requestInterruption()
            if not thread.wait(timeout_ms):
                return False
        self.hide()
        return True
