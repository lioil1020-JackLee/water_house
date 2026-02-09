import os
from pathlib import Path
from PyQt5.QtWidgets import QDialog, QLabel, QHBoxLayout, QPushButton, QVBoxLayout, QSizePolicy, QLineEdit, QGridLayout, QWidget
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont, QIcon


class NumpadDialog(QDialog):
    """Simple numpad dialog returning a numeric string."""
    def __init__(self, parent=None, initial: str = '0'):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle('數字鍵盤')
        # remove context-help '?' from title bar
        try:
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        except Exception:
            try:
                from PyQt5.QtCore import Qt as _Qt
                self.setWindowFlags(self.windowFlags() & ~_Qt.WindowContextHelpButtonHint)
            except Exception:
                pass
        # double size for larger keypad
        self.setFixedSize(640, 840)
        self.value = initial

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)

        self.display = QLineEdit(initial)
        self.display.setReadOnly(True)
        self.display.setAlignment(Qt.AlignRight)
        self.display.setFixedHeight(96)
        f = QFont()
        f.setPointSize(40)
        self.display.setFont(f)
        v.addWidget(self.display)

        grid = QGridLayout()
        buttons = [
            ('7', 0, 0), ('8', 0, 1), ('9', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('1', 2, 0), ('2', 2, 1), ('3', 2, 2),
            ('.', 3, 0), ('0', 3, 1), ('←', 3, 2),
        ]
        for txt, r, c in buttons:
            b = QPushButton(txt)
            b.setFixedSize(160, 128)
            bf = QFont()
            bf.setPointSize(28)
            b.setFont(bf)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda ch, t=txt: self._on_btn(t))
            grid.addWidget(b, r, c)

        v.addLayout(grid)

        ok = QPushButton('OK')
        ok.setFixedHeight(96)
        of = QFont()
        of.setPointSize(24)
        ok.setFont(of)
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self.accept)
        v.addWidget(ok)

    def _on_btn(self, t: str):
        txt = self.display.text()
        if t == '←':
            txt = txt[:-1] if txt else ''
        else:
            txt = txt + t
        # sanitize: allow only one dot
        if txt.count('.') > 1:
            # ignore extra
            return
        # remove leading zeros unless before dot
        if txt and txt != '.' and txt[0] == '0' and len(txt) > 1 and txt[1] != '.':
            txt = txt.lstrip('0') or '0'
        self.display.setText(txt)

    def get_value(self) -> str:
        return self.display.text()


class PopupDialog(QDialog):
    def __init__(self, title: str = "Alert Popup", message: str = "", initial_state: str = 'green'):
        super().__init__()
        self.setWindowTitle(title)
        # remove context-help '?' from title bar
        try:
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        except Exception:
            try:
                from PyQt5.QtCore import Qt as _Qt
                self.setWindowFlags(self.windowFlags() & ~_Qt.WindowContextHelpButtonHint)
            except Exception:
                pass
        self.setModal(True)
        # make room for controls (double size)
        self.setFixedSize(750, 520)

        self.selected_state = initial_state
        # stored alarm delay (seconds)
        self.alarm_delay = 0.0
        # locate image directory (project/img)
        self.img_dir = str(Path(__file__).parent.parent.joinpath('img'))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        # Content
        # Split message into room number (first line) and remaining description
        msg_text = (message or "").strip()
        parts = msg_text.splitlines()
        room_num_text = parts[0] if parts else ''
        desc_text = '\n'.join(parts[1:]) if len(parts) > 1 else ''

        # (Large room number removed; dialog shows only three control rows)

        # create the interactive widgets first
        # Alarm toggle button
        self.alarm_toggle = QPushButton()
        self.alarm_toggle.setCheckable(True)
        self.alarm_toggle.setFixedSize(170, 170)
        self.alarm_toggle.setIconSize(QSize(165, 165))
        try:
            self.alarm_toggle.setFlat(True)
            self.alarm_toggle.setStyleSheet('border: none; background: transparent;')
            self.alarm_toggle.setFocusPolicy(Qt.NoFocus)
        except Exception:
            pass
        try:
            self.alarm_toggle.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass
        self.alarm_toggle.toggled.connect(self._on_toggle_alarm)
        self.alarm_toggle.setChecked(initial_state == 'red')

        # editable display with border; clicking opens numpad
        self.delay_edit = QLineEdit('0')
        self.delay_edit.setReadOnly(True)
        # slightly narrower so suffix label fits beside it
        self.delay_edit.setFixedWidth(300)
        self.delay_edit.setFixedHeight(112)
        self.delay_edit.setAlignment(Qt.AlignCenter)
        self.delay_edit.setStyleSheet('border: 2px solid #444; border-radius:6px; padding:6px; background: white;')
        edf = QFont()
        edf.setPointSize(22)
        self.delay_edit.setFont(edf)
        try:
            self.delay_edit.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass

        # Reset button
        self.reset_button = QPushButton()
        self.reset_button.setFixedSize(180, 180)
        self.reset_button.setIconSize(QSize(176, 176))
        try:
            self.reset_button.setFlat(True)
            self.reset_button.setStyleSheet('border: none; background: transparent;')
            self.reset_button.setFocusPolicy(Qt.NoFocus)
        except Exception:
            pass
        try:
            self.reset_button.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass
        self.reset_button.clicked.connect(self._on_reset)

        # Controls area: use a 2-column grid so right-side widgets share the same center
        controls_grid = QGridLayout()
        controls_grid.setHorizontalSpacing(12)
        controls_grid.setVerticalSpacing(12)
        controls_grid.setColumnMinimumWidth(0, 260)
        content_width = 400
        controls_grid.setColumnMinimumWidth(1, content_width)

        # Alarm toggle (row 0)
        lbl1 = QLabel('警報開關')
        lbl1.setFont(QFont("Times New Roman", 24))
        lbl1.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls_grid.addWidget(lbl1, 0, 0, Qt.AlignRight | Qt.AlignVCenter)
        c1 = QWidget()
        c1.setFixedWidth(content_width)
        h1 = QHBoxLayout(c1)
        h1.setContentsMargins(0, 0, 40, 0)
        h1.addStretch()
        h1.addWidget(self.alarm_toggle, 0, Qt.AlignCenter)
        h1.addStretch()
        controls_grid.addWidget(c1, 0, 1)

        # Alarm delay (row 1)
        lbl2 = QLabel('警報延遲')
        lbl2.setFont(QFont("Times New Roman", 24))
        lbl2.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls_grid.addWidget(lbl2, 1, 0, Qt.AlignRight | Qt.AlignVCenter)
        c2 = QWidget()
        c2.setFixedWidth(content_width)
        h2 = QHBoxLayout(c2)
        # add left margin so the edit+suffix group shifts right
        h2.setContentsMargins(30, 0, 0, 0)
        h2.setSpacing(12)
        # left-align the group inside the container (remove leading stretch)
        h2.addWidget(self.delay_edit)
        # fixed spacing between edit and suffix so suffix shifts right
        h2.addSpacing(28)
        lbl_sec = QLabel('秒')
        lbl_sec.setFont(QFont("Times New Roman", 20))
        lbl_sec.setAlignment(Qt.AlignVCenter)
        # increase left margin to move the suffix further right
        lbl_sec.setContentsMargins(24, 0, 0, 0)
        # match edit height so baseline/vertical center align
        lbl_sec.setFixedHeight(self.delay_edit.height())
        h2.addWidget(lbl_sec, 0, Qt.AlignVCenter)
        h2.addStretch()
        controls_grid.addWidget(c2, 1, 1)

        # Reset (row 2)
        lbl3 = QLabel('警報復歸')
        lbl3.setFont(QFont("Times New Roman", 24))
        lbl3.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls_grid.addWidget(lbl3, 2, 0, Qt.AlignRight | Qt.AlignVCenter)
        c3 = QWidget()
        c3.setFixedWidth(content_width)
        h3 = QHBoxLayout(c3)
        h3.setContentsMargins(0, 0, 40, 0)
        h3.addStretch()
        h3.addWidget(self.reset_button, 0, Qt.AlignCenter)
        h3.addStretch()
        controls_grid.addWidget(c3, 2, 1)
        # attach mouse click to open numpad
        def _open_npad(evt=None):
            dlg = NumpadDialog(self, initial=self.delay_edit.text())
            if dlg.exec_() == QDialog.Accepted:
                val = dlg.get_value().strip()
                try:
                    if val == '':
                        val = '0'
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
        spacer_top.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(spacer_top)

        # Create horizontal container to center controls horizontally
        container = QHBoxLayout()
        spacer_left = QWidget()
        spacer_left.setFixedWidth(8)
        container.addWidget(spacer_left)
        container.addLayout(controls_grid)
        spacer_right = QWidget()
        spacer_right.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        container.addWidget(spacer_right)
        layout.addLayout(container)

        spacer_bottom = QWidget()
        spacer_bottom.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(spacer_bottom)

        # update icons to match initial state
        self._update_icons()

    def _on_reset(self):
        # reset clears alarm and sets selected_state to green
        self.alarm_toggle.setChecked(False)
        self.selected_state = 'green'
        self._update_icons()

    def _on_ok(self):
        # determine selected state
        if self.alarm_toggle.isChecked():
            self.selected_state = 'red'
        # else keep selected_state (could be 'green')
        # capture alarm delay value from edit (if present)
        try:
            txt = getattr(self, 'delay_edit', None).text() if getattr(self, 'delay_edit', None) is not None else '0'
            self.alarm_delay = float(txt)
        except Exception:
            self.alarm_delay = 0.0
        self.accept()

    def _on_toggle_alarm(self, checked: bool):
        # update selected_state and icon when toggled
        self.selected_state = 'red' if checked else 'green'
        self._update_icons()

    def _update_icons(self):
        # load ON/OFF icons and reset icon from img dir
        try:
            on_path = os.path.join(self.img_dir, 'ON.png')
            off_path = os.path.join(self.img_dir, 'OFF.png')
            reset_path = os.path.join(self.img_dir, 'reset.png')
            if os.path.exists(on_path) and os.path.exists(off_path):
                icon = QIcon(on_path) if self.alarm_toggle.isChecked() else QIcon(off_path)
                if not icon.isNull():
                    self.alarm_toggle.setIcon(icon)
                    self.alarm_toggle.setText('')
                else:
                    self.alarm_toggle.setText('開' if self.alarm_toggle.isChecked() else '關')
            else:
                self.alarm_toggle.setText('開' if self.alarm_toggle.isChecked() else '關')
            if os.path.exists(reset_path):
                icon = QIcon(reset_path)
                if not icon.isNull():
                    self.reset_button.setIcon(icon)
                    self.reset_button.setText('')
                else:
                    self.reset_button.setText('復歸')
            else:
                self.reset_button.setText('復歸')
        except Exception:
            pass