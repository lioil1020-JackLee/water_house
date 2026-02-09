import os
from pathlib import Path
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
from PyQt6.QtCore import Qt, QSize, QEvent
from PyQt6.QtGui import QFont, QIcon, QPalette, QColor


class NumpadDialog(QDialog):
    """Simple numpad dialog returning a numeric string."""

    def __init__(self, parent=None, initial: str = "0"):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle("數字鍵盤")
        self._remove_help_button()
        # base (original) sizes for scaling calculations
        self._base_width = 640
        self._base_height = 840
        self._base_display_h = 80
        self._base_display_font = 48
        self._base_btn_w = 160
        self._base_btn_h = 128
        self._base_btn_font = 44
        self._base_ok_h = 96
        self._base_ok_font = 48

        # start at 70% of base size but allow resizing so layout can scale
        self.resize(int(self._base_width * 0.7), int(self._base_height * 0.7))
        self.setMinimumSize(320, 420)
        # remove minimize and maximize/restore buttons; keep close
        try:
            self.setWindowFlag(Qt.WindowMinimizeButtonHint, False)
            self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        except Exception:
            pass
        self.value = initial

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.display = QLineEdit(initial)
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setFixedHeight(96)
        f = QFont("微軟正黑體")
        f.setPointSize(40)
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

        grid = QGridLayout()
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
        for txt, r, c in buttons:
            b = QPushButton(txt)
            b.setFixedSize(160, 128)
            bf = QFont("微軟正黑體")
            bf.setPointSize(44)
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
                b.setStyleSheet(
                    f"background:{b_bg}; color:{b_text}; font-size:{self._base_btn_font}px;"
                )
            except Exception:
                pass
            self._numpad_buttons.append(b)
            grid.addWidget(b, r, c)

        v.addLayout(grid)

        ok = QPushButton("OK")
        ok.setFixedHeight(96)
        of = QFont("微軟正黑體")
        of.setPointSize(24)
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
        # apply initial scaling so child widgets match window size
        try:
            self._apply_scale()
        except Exception:
            pass

    def resizeEvent(self, event):
        try:
            self._apply_scale()
        except Exception:
            pass
        super().resizeEvent(event)

    def _apply_scale(self):
        # compute uniform scale based on width
        w = max(1, self.width())
        scale = w / float(self._base_width)
        # display height and font
        try:
            self.display.setFixedHeight(max(24, int(self._base_display_h * scale)))
            df = QFont(self.display.font())
            df.setPointSize(max(8, int(self._base_display_font * scale)))
            self.display.setFont(df)
        except Exception:
            pass
        # buttons
        try:
            for b in getattr(self, "_numpad_buttons", []):
                b.setFixedSize(
                    max(48, int(self._base_btn_w * scale)),
                    max(48, int(self._base_btn_h * scale)),
                )
                bf = QFont(b.font())
                bf.setPointSize(max(8, int(self._base_btn_font * scale)))
                b.setFont(bf)
        except Exception:
            pass
        # OK button
        try:
            # find ok button (last widget in layout)
            ok = None
            for w in self.findChildren(QPushButton):
                if w.text() == "OK":
                    ok = w
                    break
            if ok is not None:
                ok.setFixedHeight(max(24, int(self._base_ok_h * scale)))
                of = QFont(ok.font())
                of.setPointSize(max(8, int(self._base_ok_font * scale)))
                ok.setFont(of)
        except Exception:
            pass

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
        txt = self.display.text()
        if t == "←":
            txt = txt[:-1] if txt else ""
        else:
            txt = txt + t
        # sanitize: allow only one dot
        if txt.count(".") > 1:
            # ignore extra
            return
        # remove leading zeros unless before dot
        if txt and txt != "." and txt[0] == "0" and len(txt) > 1 and txt[1] != ".":
            txt = txt.lstrip("0") or "0"
        self.display.setText(txt)

    def get_value(self) -> str:
        return self.display.text()


class PopupDialog(QDialog):
    def __init__(
        self,
        title: str = "Alert Popup",
        message: str = "",
        initial_state: str = "green",
    ):
        super().__init__()
        self.setWindowTitle(title)
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
        self.setFixedSize(int(self._base_width * 0.7), int(self._base_height * 0.7))
        # remove minimize and maximize/restore buttons; keep close
        try:
            self.setWindowFlag(Qt.WindowMinimizeButtonHint, False)
            self.setWindowFlag(Qt.WindowMaximizeButtonHint, False)
            self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        except Exception:
            pass

        self.selected_state = initial_state
        # stored alarm delay (seconds)
        self.alarm_delay = 0.0
        # locate image directory (project/img)
        self.img_dir = str(Path(__file__).parent.parent.joinpath("img"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        # Content
        # (Large room number removed; dialog shows only three control rows)

        # create the interactive widgets first
        # Alarm toggle button
        self.alarm_toggle = QPushButton()
        self.alarm_toggle.setCheckable(True)
        # use base sizes; actual scaled sizes applied in _apply_scale
        self._setup_button(
            self.alarm_toggle,
            QSize(self._base_alarm_btn[0], self._base_alarm_btn[1]),
            QSize(self._base_alarm_btn[0] - 5, self._base_alarm_btn[1] - 5),
        )
        self.alarm_toggle.toggled.connect(self._on_toggle_alarm)
        self.alarm_toggle.setChecked(initial_state == "red")

        # editable display with border; clicking opens numpad
        self.delay_edit = QLineEdit("0")
        self.delay_edit.setReadOnly(True)
        # slightly narrower so suffix label fits beside it (base values)
        self.delay_edit.setFixedWidth(self._base_delay_w)
        self.delay_edit.setFixedHeight(self._base_delay_h)
        self.delay_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            pal = QApplication.instance().palette()
            base_color = pal.color(QPalette.ColorRole.Base)
            text_hex = pal.color(QPalette.ColorRole.Text).name()
            base_hex = base_color.name()
            # compute perceived luminance to decide contrasting border color
            r, g, b = base_color.red(), base_color.green(), base_color.blue()
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            # if background is dark, use white border; else use darker border
            border_hex = (
                "#ffffff" if lum < 96 else pal.color(QPalette.ColorRole.Dark).name()
            )
            # make the edit border thinner and padding smaller
            self.delay_edit.setStyleSheet(
                f"border: 1px solid {border_hex}; border-radius:3px; padding:2px; background: {base_hex}; color: {text_hex};"
            )
        except Exception:
            self.delay_edit.setStyleSheet(
                "border: 1px solid #444; border-radius:4px; padding:4px; background: white;"
            )
        edf = QFont("微軟正黑體")
        edf.setPointSize(self._base_delay_font)
        self.delay_edit.setFont(edf)
        self.delay_edit.setCursor(Qt.CursorShape.PointingHandCursor)

        # Reset button
        self.reset_button = QPushButton()
        self._setup_button(
            self.reset_button,
            QSize(self._base_reset_btn[0], self._base_reset_btn[1]),
            QSize(self._base_reset_icon[0], self._base_reset_icon[1]),
        )
        self.reset_button.clicked.connect(self._on_reset)

        # Controls area: use a 2-column grid so right-side widgets share the same center
        controls_grid = QGridLayout()
        controls_grid.setHorizontalSpacing(12)
        controls_grid.setVerticalSpacing(12)
        # let columns stretch instead of using fixed minimum widths to keep layout flexible
        controls_grid.setColumnStretch(0, 1)
        controls_grid.setColumnStretch(1, 2)

        # Alarm toggle (row 0)
        self.lbl1 = QLabel("警報開關")
        self.lbl1.setFont(QFont("微軟正黑體", 32))
        self.lbl1.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        controls_grid.addWidget(
            self.lbl1, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        c1 = QWidget()
        # allow container to size dynamically so centering works
        self._c1 = c1
        h1 = QHBoxLayout(c1)
        h1.setContentsMargins(0, 0, 40, 0)
        h1.addStretch()
        h1.addWidget(self.alarm_toggle, 0, Qt.AlignmentFlag.AlignCenter)
        h1.addStretch()
        controls_grid.addWidget(c1, 0, 1)

        # Alarm delay (row 1)
        self.lbl2 = QLabel("警報延遲")
        self.lbl2.setFont(QFont("微軟正黑體", 32))
        self.lbl2.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        controls_grid.addWidget(
            self.lbl2, 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        c2 = QWidget()
        # allow container to size dynamically so centering works
        self._c2 = c2
        c2.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        h2 = QHBoxLayout(c2)
        # add left margin so the edit+suffix group shifts right
        h2.setContentsMargins(30, 0, 0, 0)
        h2.setSpacing(12)
        # left-align the group inside the container (remove leading stretch)
        h2.addWidget(self.delay_edit, 0, Qt.AlignmentFlag.AlignVCenter)
        # fixed spacing between edit and suffix so suffix shifts right
        h2.addSpacing(28)
        self.lbl_sec = QLabel("秒")
        self.lbl_sec.setFont(QFont("微軟正黑體", 24))
        self.lbl_sec.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        # increase left margin to move the suffix further right
        self.lbl_sec.setContentsMargins(24, 0, 0, 0)
        # match edit height so baseline/vertical center align
        self.lbl_sec.setFixedHeight(self.delay_edit.height())
        h2.addWidget(self.lbl_sec, 0, Qt.AlignmentFlag.AlignVCenter)
        h2.addStretch()
        controls_grid.addWidget(c2, 1, 1, Qt.AlignmentFlag.AlignVCenter)

        # Reset (row 2)
        self.lbl3 = QLabel("警報復歸")
        self.lbl3.setFont(QFont("微軟正黑體", 32))
        self.lbl3.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        controls_grid.addWidget(
            self.lbl3, 2, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        c3 = QWidget()
        # allow container to size dynamically so centering works
        self._c3 = c3
        h3 = QHBoxLayout(c3)
        h3.setContentsMargins(0, 0, 40, 0)
        h3.addStretch()
        h3.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignCenter)
        h3.addStretch()
        controls_grid.addWidget(c3, 2, 1)

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

        self.delay_edit.mousePressEvent = _open_npad

        # Add spacers to center the controls vertically
        spacer_top = QWidget()
        spacer_top.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(spacer_top)

        # Create horizontal container to center controls horizontally
        container = QHBoxLayout()
        spacer_left = QWidget()
        spacer_left.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        container.addWidget(spacer_left)
        # wrap grid in a widget so we can center it reliably
        controls_widget = QWidget()
        controls_widget.setLayout(controls_grid)
        controls_widget.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        container.addWidget(controls_widget, 0, Qt.AlignmentFlag.AlignCenter)
        spacer_right = QWidget()
        spacer_right.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        container.addWidget(spacer_right)
        layout.addLayout(container)

        spacer_bottom = QWidget()
        spacer_bottom.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(spacer_bottom)

        # update icons to match initial state
        self._update_icons()
        # apply current application palette so widgets match system theme
        try:
            self._apply_palette()
        except Exception:
            pass
        # apply initial scaling
        try:
            self._apply_scale()
        except Exception:
            pass

    def resizeEvent(self, event):
        try:
            self._apply_scale()
        except Exception:
            pass
        super().resizeEvent(event)

    def _apply_scale(self):
        w = max(1, self.width())
        scale = w / float(self._base_width)
        # alarm and reset buttons
        try:
            aw = max(32, int(self._base_alarm_btn[0] * scale))
            ah = max(32, int(self._base_alarm_btn[1] * scale))
            self.alarm_toggle.setFixedSize(aw, ah)
            self.alarm_toggle.setIconSize(
                QSize(
                    max(16, int((self._base_alarm_btn[0] - 5) * scale)),
                    max(16, int((self._base_alarm_btn[1] - 5) * scale)),
                )
            )
            rw = max(32, int(self._base_reset_btn[0] * scale))
            rh = max(32, int(self._base_reset_btn[1] * scale))
            self.reset_button.setFixedSize(rw, rh)
            self.reset_button.setIconSize(
                QSize(
                    max(16, int(self._base_reset_icon[0] * scale)),
                    max(16, int(self._base_reset_icon[1] * scale)),
                )
            )
        except Exception:
            pass
        # delay edit sizing and font
        try:
            dw = max(40, int(self._base_delay_w * scale))
            dh = max(20, int(self._base_delay_h * scale))
            self.delay_edit.setFixedWidth(dw)
            self.delay_edit.setFixedHeight(dh)
            df = QFont(self.delay_edit.font())
            df.setPointSize(max(8, int(self._base_delay_font * scale)))
            self.delay_edit.setFont(df)
            # suffix label height
            try:
                self.lbl_sec.setFixedHeight(self.delay_edit.height())
                sf = QFont(self.lbl_sec.font())
                sf.setPointSize(max(8, int(self._base_suffix_font * scale)))
                self.lbl_sec.setFont(sf)
            except Exception:
                pass
        except Exception:
            pass
        # labels
        try:
            lf = QFont(self.lbl1.font())
            lf.setPointSize(max(8, int(self._base_label_font * scale)))
            self.lbl1.setFont(lf)
            self.lbl2.setFont(lf)
            self.lbl3.setFont(lf)
        except Exception:
            pass
        # container widths (adjust the small minimum widths to keep layout balanced)
        try:
            if hasattr(self, "_c1"):
                self._c1.setMinimumWidth(max(120, int(400 * scale)))
            if hasattr(self, "_c2"):
                self._c2.setMinimumWidth(max(120, int(400 * scale)))
            if hasattr(self, "_c3"):
                self._c3.setMinimumWidth(max(120, int(400 * scale)))
        except Exception:
            pass

    def _on_reset(self):
        # reset clears alarm and sets selected_state to green
        self.alarm_toggle.setChecked(False)
        self.selected_state = "green"
        self._update_icons()

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

    def _on_toggle_alarm(self, checked: bool):
        # update selected_state and icon when toggled
        self.selected_state = "red" if checked else "green"
        self._update_icons()

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
                # keep the thinner border/padding we set earlier
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

    def _setup_button(self, button, size, icon_size):
        button.setFixedSize(size)
        button.setIconSize(icon_size)
        button.setFlat(True)
        button.setStyleSheet("border: none; background: transparent;")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
