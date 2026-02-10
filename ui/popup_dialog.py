import os
from pathlib import Path
import ctypes
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QSizePolicy,
    QLineEdit,
    QGridLayout,
    QWidget,
    QApplication,
)
from PyQt6.QtCore import Qt, QSize, QEvent, QThread, pyqtSignal, QTimer, pyqtSlot
from PyQt6.QtGui import QFont, QIcon, QPalette, QColor
import asyncio
from asyncua import Client


class NumpadDialog(QDialog):
    """Simple numpad dialog returning a numeric string."""

    def __init__(self, parent=None, initial: str = "0"):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("數字鍵盤")
        self.workspace_root = Path(__file__).parent.parent
        self.img_dir = os.path.join(self.workspace_root, 'img')
        self.setWindowIcon(QIcon(os.path.join(self.img_dir, '享溫泉.ico')))
        self._remove_help_button()
        # base (original) sizes for scaling calculations
        self._base_width = 640
        self._base_height = 840
        # start at 70% of base size but allow resizing so layout can scale
        self.setFixedSize(int(self._base_width * 0.8), int(self._base_height * 0.8))
        # No minimum size since we're using fixed size
        # remove minimize and maximize/restore buttons; keep title bar and close button
        try:
            # Use WindowsSystemHint to get more control
            self.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.WindowSystemMenuHint
            )
        except Exception:
            try:
                # Alternative approach
                flags = self.windowFlags()
                flags = flags & ~Qt.WindowType.WindowMinimizeButtonHint
                flags = flags & ~Qt.WindowType.WindowMaximizeButtonHint
                self.setWindowFlags(flags)
            except Exception:
                pass
        self.value = initial
        self.initial_value = initial
        self.first_press = True

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        # Display area
        self.display = QLineEdit(initial)
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setFixedHeight(80)
        f = QFont("微軟正黑體")
        f.setPointSize(32)
        self.display.setFont(f)
        # style display using application palette so it matches theme
        try:
            pal = QApplication.instance().palette()
            base_color = pal.color(QPalette.ColorRole.Base)
            text = pal.color(QPalette.ColorRole.Text).name()
            base = base_color.name()
            # perceived luminance to choose a contrasting border (white on dark)
            r, g, b = base_color.red(), base_color.green(), base_color.blue()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            border = (
                "#ffffff" if lum < 96 else pal.color(QPalette.ColorRole.Dark).name()
            )
            self.display.setStyleSheet(
                f"background:{base}; color:{text}; border:2px solid {border};"
            )
        except Exception:
            pass
        v.addWidget(self.display)

        # Numpad buttons grid
        grid = QGridLayout()
        grid.setSpacing(8)
        self._grid = grid
        buttons = [
            ("7", 0, 0),
            ("8", 0, 1),
            ("9", 0, 2),
            ("4", 1, 0),
            ("5", 1, 1),
            ("6", 1, 2),
            ("1", 2, 0),
            ("2", 2, 1),
            ("3", 2, 2),
            (".", 3, 0),
            ("0", 3, 1),
            ("←", 3, 2),
        ]
        self._numpad_buttons = []
        # Calculate button size based on window size
        btn_width = int(
            (self._base_width * 0.8 - 40) / 3 - 20
        )  # Account for margins and spacing
        btn_height = int(btn_width * 0.75)  # 3:4 ratio

        for txt, r, c in buttons:
            b = QPushButton(txt)
            b.setFixedSize(btn_width, btn_height)
            bf = QFont("微軟正黑體")
            bf.setPointSize(int(btn_width * 0.25))  # Scale font with button size
            b.setFont(bf)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            # do not receive focus by default so no "selected" outline shows
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda ch, t=txt: self._on_btn(t))
            # style numpad buttons to follow theme
            try:
                pal = QApplication.instance().palette()
                b_bg = pal.color(QPalette.ColorRole.Button).name()
                b_text = pal.color(QPalette.ColorRole.ButtonText).name()
                b.setStyleSheet(f"background:{b_bg}; color:{b_text};")
            except Exception:
                pass
            self._numpad_buttons.append(b)
            grid.addWidget(b, r, c)

        v.addLayout(grid)

        # OK button
        ok = QPushButton("OK")
        ok.setFixedHeight(70)
        of = QFont("微軟正黑體")
        of.setPointSize(20)
        ok.setFont(of)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self.accept)
        try:
            pal = QApplication.instance().palette()
            ok.setStyleSheet(
                f"background:{pal.color(QPalette.ColorRole.Button).name()}; color:{pal.color(QPalette.ColorRole.ButtonText).name()};"
            )
        except Exception:
            pass
        v.addWidget(ok)

    def _apply_palette(self):
        app = QApplication.instance()
        if not app:
            return
        pal = app.palette()
        try:
            base_color = pal.color(QPalette.ColorRole.Base)
            text = pal.color(QPalette.ColorRole.Text).name()
            base = base_color.name()
            r, g, b = base_color.red(), base_color.green(), base_color.blue()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            border = (
                "#ffffff" if lum < 96 else pal.color(QPalette.ColorRole.Dark).name()
            )
            self.display.setStyleSheet(
                f"background:{base}; color:{text}; border:2px solid {border};"
            )
        except Exception:
            pass

        try:
            b_bg = pal.color(QPalette.ColorRole.Button).name()
            b_text = pal.color(QPalette.ColorRole.ButtonText).name()
            for b in getattr(self, "_numpad_buttons", []):
                try:
                    b.setStyleSheet(f"background:{b_bg}; color:{b_text};")
                except:
                    pass
        except Exception:
            pass

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange:
            try:
                self._apply_palette()
            except:
                pass
        super().changeEvent(event)

    def _remove_help_button(self):
        try:
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        except Exception:
            try:
                from PyQt6.QtCore import Qt as _Qt

                self.setWindowFlags(
                    self.windowFlags() & ~_Qt.WindowContextHelpButtonHint
                )
            except Exception:
                pass

    def _on_btn(self, t: str):
        if t == "←":
            txt = self.display.text()
            if not txt:
                # If empty, reset to initial value
                txt = self.initial_value
                self.first_press = True
            else:
                txt = txt[:-1]
                if not txt:
                    txt = self.initial_value
                    self.first_press = True
        else:
            if self.first_press:
                txt = t
                self.first_press = False
            else:
                txt = self.display.text() + t
        # sanitize: allow only one dot
        if txt.count(".") > 1:
            # ignore extra
            return
        # remove leading zeros unless before dot
        if txt and txt != "." and txt[0] == "0" and len(txt) > 1 and txt[1] != ".":
            txt = txt.lstrip("0") or "0"
        self.display.setText(txt)
        self.value = txt

    def get_value(self) -> str:
        return self.display.text()

    def showEvent(self, event):
        super().showEvent(event)
        # Remove minimize and maximize buttons using Windows API
        try:
            hwnd = int(self.winId())
            # WS_MINIMIZEBOX = 0x00020000, WS_MAXIMIZEBOX = 0x00010000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            style &= ~0x00020000  # Remove WS_MINIMIZEBOX
            style &= ~0x00010000  # Remove WS_MAXIMIZEBOX
            ctypes.windll.user32.SetWindowLongW(hwnd, -16, style)
            # Force redraw
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x27)  # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
        except Exception:
            pass


class PopupDialog(QDialog):
    def __init__(
        self,
        title: str = "Alert Popup",
        message: str = "",
        initial_state: str = "green",
        initial_delay: float = 0.0,
        parent_dialog=None,
        enable_tag=None,
        reset_tag=None,
        delay_tag=None,
    ):
        super().__init__()
        self.setWindowTitle(title)
        self.workspace_root = Path(__file__).parent.parent
        self.img_dir = os.path.join(self.workspace_root, 'img')
        self.setWindowIcon(QIcon(os.path.join(self.img_dir, '享溫泉.ico')))
        self._remove_help_button()
        self.setModal(True)
        # base sizes for scaling
        self._base_width = 750
        self._base_height = 520
        self._base_alarm_btn = (170, 170)
        self._base_reset_btn = (180, 180)
        self._base_reset_icon = (180, 180)
        self._base_delay_w = 300
        self._base_delay_h = 80
        self._base_delay_font = 40
        self._base_label_font = 40
        self._base_suffix_font = 40

        # set fixed size, no resizing allowed
        self.setFixedSize(int(self._base_width * 0.85), int(self._base_height * 0.85))
        # remove minimize and maximize/restore buttons; keep title bar and close button
        try:
            # Use WindowsSystemHint to get more control
            self.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.WindowTitleHint
                | Qt.WindowType.WindowCloseButtonHint
                | Qt.WindowType.WindowSystemMenuHint
            )
        except Exception:
            try:
                # Alternative approach
                flags = self.windowFlags()
                flags = flags & ~Qt.WindowType.WindowMinimizeButtonHint
                flags = flags & ~Qt.WindowType.WindowMaximizeButtonHint
                self.setWindowFlags(flags)
            except Exception:
                pass

        self.selected_state = initial_state
        # stored alarm delay (seconds) - ensure it's always a valid number
        try:
            self.alarm_delay = float(initial_delay) if initial_delay is not None else 0.0
        except (TypeError, ValueError):
            self.alarm_delay = 0.0
        # locate image directory (project/img)
        self.img_dir = str(Path(__file__).parent.parent.joinpath("img"))

        # Main layout - very simple approach like scada_dialog
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 20, 15, 20)
        main_layout.setSpacing(15)

        # Scale factor
        scale_factor = 0.85

        # Row 0: Alarm toggle
        row0 = QHBoxLayout()
        row0.setSpacing(15)
        row0.setContentsMargins(0, 0, 0, 0)

        # Left spacer to push labels away from edge
        row0.addSpacing(40)  # Increased to move labels right

        self.lbl1 = QLabel("警報開關")
        self.lbl1.setFont(QFont("微軟正黑體", int(32 * scale_factor)))
        self.lbl1.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl1.setFixedWidth(140)  # Increased width to prevent text cutoff
        row0.addWidget(self.lbl1)

        # Fixed spacing to align controls
        row0.addSpacing(60)  # Increased to move controls further right

        # Extra spacing to align button with edit field
        row0.addSpacing(80)

        self.alarm_toggle = QPushButton()
        self.alarm_toggle.setCheckable(True)
        self._setup_button(
            self.alarm_toggle,
            QSize(
                int(self._base_alarm_btn[0] * scale_factor),
                int(self._base_alarm_btn[1] * scale_factor),
            ),
            QSize(
                int((self._base_alarm_btn[0] - 5) * scale_factor),
                int((self._base_alarm_btn[1] - 5) * scale_factor),
            ),
        )
        self.alarm_toggle.toggled.connect(self._on_toggle_alarm)
        # 初始化時暫時禁用信號，避免初始化時寫入
        self.alarm_toggle.blockSignals(True)
        self.alarm_toggle.setChecked(initial_state == "red")
        self.alarm_toggle.blockSignals(False)
        row0.addWidget(self.alarm_toggle)

        row0.addStretch(1)
        main_layout.addLayout(row0)

        # Row 1: Alarm delay
        row1 = QHBoxLayout()
        row1.setSpacing(15)
        row1.setContentsMargins(0, 0, 0, 0)

        # Left spacer to push labels away from edge (same as row0)
        row1.addSpacing(40)  # Same as row0

        self.lbl2 = QLabel("警報延遲")
        self.lbl2.setFont(QFont("微軟正黑體", int(32 * scale_factor)))
        self.lbl2.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl2.setFixedWidth(140)  # Same as row0 label
        row1.addWidget(self.lbl2)

        # Same fixed spacing as row0 to align controls
        row1.addSpacing(60)  # Same as row0

        # Edit field
        # Format delay display: no decimal if integer, else max one decimal place
        delay_val = float(self.alarm_delay) if self.alarm_delay is not None else 0.0
        if delay_val == int(delay_val):
            delay_display = f"{int(delay_val)}"
        else:
            delay_display = f"{delay_val:.1f}"
        self.delay_edit = QLineEdit(delay_display)
        self.delay_edit.setReadOnly(True)
        self.delay_edit.setFixedWidth(int(self._base_delay_w * scale_factor))
        self.delay_edit.setFixedHeight(int(self._base_delay_h * scale_factor))
        self.delay_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            pal = QApplication.instance().palette()
            base_color = pal.color(QPalette.ColorRole.Base)
            text_hex = pal.color(QPalette.ColorRole.Text).name()
            base_hex = base_color.name()
            r, g, b = base_color.red(), base_color.green(), base_color.blue()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            border_hex = (
                "#ffffff" if lum < 96 else pal.color(QPalette.ColorRole.Dark).name()
            )
            self.delay_edit.setStyleSheet(
                f"border: 1px solid {border_hex}; border-radius:3px; padding:2px; background: {base_hex}; color: {text_hex};"
            )
        except Exception:
            self.delay_edit.setStyleSheet(
                "border: 1px solid #444; border-radius:4px; padding:4px; background: white;"
            )
        edf = QFont("微軟正黑體")
        edf.setPointSize(int(self._base_delay_font * scale_factor))
        self.delay_edit.setFont(edf)
        self.delay_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        row1.addWidget(self.delay_edit)

        # Suffix label
        row1.addSpacing(10)
        self.lbl_sec = QLabel("秒")
        self.lbl_sec.setFont(QFont("微軟正黑體", int(32 * scale_factor)))
        self.lbl_sec.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        row1.addWidget(self.lbl_sec)

        row1.addStretch(1)
        main_layout.addLayout(row1)

        # Row 2: Reset button
        row2 = QHBoxLayout()
        row2.setSpacing(15)
        row2.setContentsMargins(0, 0, 0, 0)

        # Left spacer to push labels away from edge (same as other rows)
        row2.addSpacing(40)  # Same as other rows

        self.lbl3 = QLabel("警報復歸")
        self.lbl3.setFont(QFont("微軟正黑體", int(32 * scale_factor)))
        self.lbl3.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl3.setFixedWidth(140)  # Same as other labels
        row2.addWidget(self.lbl3)

        # Same fixed spacing as other rows to align controls
        row2.addSpacing(60)  # Same as other rows

        # Extra spacing to align button with edit field (same as row0)
        row2.addSpacing(80)

        self.reset_button = QPushButton()
        self._setup_button(
            self.reset_button,
            QSize(
                int(self._base_reset_btn[0] * scale_factor),
                int(self._base_reset_btn[1] * scale_factor),
            ),
            QSize(
                int(self._base_reset_icon[0] * scale_factor),
                int(self._base_reset_icon[1] * scale_factor),
            ),
        )
        self.reset_button.pressed.connect(self._on_reset_pressed)
        self.reset_button.released.connect(self._on_reset_released)
        row2.addWidget(self.reset_button)

        row2.addStretch(1)
        main_layout.addLayout(row2)

        # attach mouse click to open numpad
        def _open_npad(evt=None):
            dlg = NumpadDialog(self, initial=self.delay_edit.text())
            if dlg.exec() == QDialog.DialogCode.Accepted:
                val = dlg.get_value().strip()
                try:
                    if val == "":
                        val = "0"
                    v = float(val)
                except Exception:
                    v = 0.0
                # store numeric value
                self.alarm_delay = v
                # format display: no decimal if integer, else max one decimal place
                if float(self.alarm_delay).is_integer():
                    disp = f"{int(self.alarm_delay)}"
                else:
                    disp = f"{self.alarm_delay:.1f}"
                self.delay_edit.setText(disp)
                
                # Write to OPC UA immediately
                if hasattr(self, 'parent_dialog') and self.parent_dialog and self.parent_dialog.opcua_client and self.delay_tag:
                    try:
                        print(f"Writing delay {self.alarm_delay} to {self.delay_tag}")
                        write_value = self.alarm_delay
                        # 立即更新本地緩存和發出信號，UI 立刻反應
                        self.parent_dialog.opcua_client.current_values[self.delay_tag] = write_value
                        self.parent_dialog.opcua_client.write_timestamps[self.delay_tag] = __import__('time').time()
                        self.parent_dialog.opcua_client.update_signal.emit({self.delay_tag: write_value})
                        # 然後非同步寫入伺服器
                        self.parent_dialog.opcua_client.write_value(self.delay_tag, write_value)
                        print("Write delay done")
                    except Exception as e:
                        print(f"Error writing delay tag: {e}")

        self.delay_edit.mousePressEvent = _open_npad

        # update icons to match initial state
        self._update_icons()
        # apply current application palette so widgets match system theme
        try:
            self._apply_palette()
        except Exception:
            pass

        # Start real-time updater if nodes are provided
        self.update_timer = None
        if parent_dialog and enable_tag and reset_tag and delay_tag:
            self.parent_dialog = parent_dialog
            self.enable_tag = enable_tag
            self.reset_tag = reset_tag
            self.delay_tag = delay_tag
            # Connect to parent's update signal for real-time updates
            self.parent_dialog.opcua_client.update_signal.connect(self._on_data_updated_from_signal)
            self.parent_dialog.opcua_client.write_failed_signal.connect(self._on_write_failed)
    
    @pyqtSlot(dict)
    def _on_data_updated_from_signal(self, updates):
        """Handle real-time data updates from parent dialog's OPC UA updates."""
        # Get latest values from parent dialog
        enable_val = self.parent_dialog.get_latest_value(self.enable_tag)
        reset_val = self.parent_dialog.get_latest_value(self.reset_tag)
        delay_val = self.parent_dialog.get_latest_value(self.delay_tag)
        
        if enable_val is not None and reset_val is not None and delay_val is not None:
            self._on_data_updated(bool(enable_val), bool(reset_val), float(delay_val))

    def _on_write_failed(self, tag_name):
        """Handle write failure by clearing current_values to revert UI."""
        if tag_name in self.parent_dialog.opcua_client.current_values:
            del self.parent_dialog.opcua_client.current_values[tag_name]
        print(f"Write failed for {tag_name}, UI will revert on next read")

    def _on_reset_pressed(self):
        # reset pressed: write True and update UI
        self.alarm_toggle.toggled.disconnect(self._on_toggle_alarm)
        self.alarm_toggle.setChecked(False)
        self.selected_state = "green"
        self._update_icons()
        self.alarm_toggle.toggled.connect(self._on_toggle_alarm)
        
        # 立即更新本地緩存和發出信號，UI 立刻反應
        if hasattr(self, 'parent_dialog') and self.parent_dialog and self.parent_dialog.opcua_client and self.reset_tag:
            try:
                print(f"Writing reset True to {self.reset_tag}")
                self.parent_dialog.opcua_client.current_values[self.reset_tag] = True
                self.parent_dialog.opcua_client.write_timestamps[self.reset_tag] = __import__('time').time()
                self.parent_dialog.opcua_client.update_signal.emit({self.reset_tag: True})
                self.parent_dialog.opcua_client.write_value(self.reset_tag, True)
            except Exception as e:
                print(f"Error writing reset True: {e}")

    def _on_reset_released(self):
        # reset released: write False to OPC UA
        if hasattr(self, 'parent_dialog') and self.parent_dialog and self.parent_dialog.opcua_client and self.reset_tag:
            try:
                print(f"Writing reset False to {self.reset_tag}")
                self.parent_dialog.opcua_client.current_values[self.reset_tag] = False
                self.parent_dialog.opcua_client.write_timestamps[self.reset_tag] = __import__('time').time()
                self.parent_dialog.opcua_client.update_signal.emit({self.reset_tag: False})
                self.parent_dialog.opcua_client.write_value(self.reset_tag, False)
            except Exception as e:
                print(f"Error writing reset False: {e}")

    def _on_ok(self):
        # determine selected state
        if self.alarm_toggle.isChecked():
            self.selected_state = "red"
        # else keep selected_state (could be 'green')
        # capture alarm delay value from edit (if present)
        try:
            txt = (
                getattr(self, "delay_edit", None).text()
                if getattr(self, "delay_edit", None) is not None
                else "0"
            )
            self.alarm_delay = float(txt)
        except Exception:
            self.alarm_delay = 0.0
        self.accept()

    def _on_data_updated(self, enable_val, reset_val, delay_val):
        """Handle real-time data updates from OPC UA."""
        # Temporarily disconnect signals to avoid triggering writes during UI update
        self.alarm_toggle.toggled.disconnect(self._on_toggle_alarm)
        try:
            # Update alarm toggle
            self.alarm_toggle.setChecked(enable_val)
            self.selected_state = "red" if enable_val else "green"
            self._update_icons()
            
            # Update delay display
            self.alarm_delay = delay_val
            if float(delay_val).is_integer():
                disp = f"{int(delay_val)}"
            else:
                disp = f"{delay_val:.1f}"
            self.delay_edit.setText(disp)
        finally:
            self.alarm_toggle.toggled.connect(self._on_toggle_alarm)

    def _on_toggle_alarm(self, checked: bool):
        # update selected_state and icon when toggled
        self.selected_state = "red" if checked else "green"
        self._update_icons()
        
        # 立即更新本地緩存和發出信號，UI 立刻反應
        if hasattr(self, 'parent_dialog') and self.parent_dialog and self.parent_dialog.opcua_client and self.enable_tag:
            try:
                print(f"Writing enable {checked} to {self.enable_tag}")
                # 先更新緩存和時間戳
                self.parent_dialog.opcua_client.current_values[self.enable_tag] = checked
                self.parent_dialog.opcua_client.write_timestamps[self.enable_tag] = __import__('time').time()
                # 發出信號更新 UI
                self.parent_dialog.opcua_client.update_signal.emit({self.enable_tag: checked})
                # 然後非同步寫入伺服器
                self.parent_dialog.opcua_client.write_value(self.enable_tag, checked)
                print("Write enable done")
            except Exception as e:
                print(f"Error writing enable tag: {e}")

    def _update_icons(self):
        # load ON/OFF icons and reset icon from img dir
        try:
            on_path = os.path.join(self.img_dir, "ON.png")
            off_path = os.path.join(self.img_dir, "OFF.png")
            reset_path = os.path.join(self.img_dir, "reset.png")
            if os.path.exists(on_path) and os.path.exists(off_path):
                icon = (
                    QIcon(on_path) if self.alarm_toggle.isChecked() else QIcon(off_path)
                )
                if not icon.isNull():
                    self.alarm_toggle.setIcon(icon)
                    self.alarm_toggle.setText("")
                else:
                    self.alarm_toggle.setText(
                        "開" if self.alarm_toggle.isChecked() else "關"
                    )
            else:
                self.alarm_toggle.setText(
                    "開" if self.alarm_toggle.isChecked() else "關"
                )
            if os.path.exists(reset_path):
                icon = QIcon(reset_path)
                if not icon.isNull():
                    self.reset_button.setIcon(icon)
                    self.reset_button.setText("")
                else:
                    self.reset_button.setText("復歸")
            else:
                self.reset_button.setText("復歸")
        except Exception:
            pass

    def _remove_help_button(self):
        try:
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        except Exception:
            try:
                from PyQt6.QtCore import Qt as _Qt

                self.setWindowFlags(
                    self.windowFlags() & ~_Qt.WindowContextHelpButtonHint
                )
            except Exception:
                pass

    def _apply_palette(self):
        app = QApplication.instance()
        if not app:
            return
        pal = app.palette()
        try:
            base_color = pal.color(QPalette.ColorRole.Base)
            text_hex = pal.color(QPalette.ColorRole.Text).name()
            base_hex = base_color.name()
            r, g, b = base_color.red(), base_color.green(), base_color.blue()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            border_hex = (
                "#ffffff" if lum < 96 else pal.color(QPalette.ColorRole.Dark).name()
            )
            try:
                self.delay_edit.setStyleSheet(
                    f"border: 1px solid {border_hex}; border-radius:4px; padding:4px; background: {base_hex}; color: {text_hex};"
                )
            except Exception:
                pass
        except Exception:
            pass

    def changeEvent(self, event):
        # respond to system/application palette changes (e.g., theme switch)
        try:
            if event.type() == QEvent.Type.ApplicationPaletteChange:
                try:
                    self._apply_palette()
                except Exception:
                    pass
        except Exception:
            pass
        super().changeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # Remove minimize and maximize buttons using Windows API
        try:
            hwnd = int(self.winId())
            # WS_MINIMIZEBOX = 0x00020000, WS_MAXIMIZEBOX = 0x00010000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            style &= ~0x00020000  # Remove WS_MINIMIZEBOX
            style &= ~0x00010000  # Remove WS_MAXIMIZEBOX
            ctypes.windll.user32.SetWindowLongW(hwnd, -16, style)
            # Force redraw
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x27)  # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
        except Exception:
            pass

    def _setup_button(self, button, size, icon_size):
        button.setFixedSize(size)
        button.setIconSize(icon_size)
        button.setFlat(True)
        button.setStyleSheet("border: none; background: transparent;")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)

    def closeEvent(self, event):
        """Handle dialog close event."""
        if self.update_timer:
            self.update_timer.stop()
        # Disconnect from parent's update signal
        if hasattr(self, 'parent_dialog') and self.parent_dialog and self.parent_dialog.opcua_client:
            try:
                self.parent_dialog.opcua_client.update_signal.disconnect(self._on_data_updated_from_signal)
            except Exception:
                pass  # Signal might already be disconnected
        super().closeEvent(event)
