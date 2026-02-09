import os
import importlib.util
from pathlib import Path
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QDialog, QApplication, QSizePolicy
)
from PyQt5.QtGui import QPixmap, QFont, QPalette, QColor
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QPoint, QRect


def _load_popup_class():
    """Load PopupDialog."""
    try:
        from popup_dialog import PopupDialog
        return PopupDialog
    except:
        pass
    
    here = os.path.dirname(__file__)
    path = os.path.abspath(os.path.join(here, 'popup_dialog.py'))
    if os.path.exists(path):
        spec = importlib.util.spec_from_file_location('popup_dialog', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, 'PopupDialog', None)
    return None


PopupDialog = _load_popup_class()


# Hardcoded room data
ROOMS_DATA = {
    '5F': {
        'top_row': [(508, '客房\n壓扣'), (509, '客房\n壓扣'), (512, '客房\n壓扣'), (513, '客房\n壓扣')],
        'customer': [
            (501, '客房\n壓扣'), (502, '客房\n壓扣'), (503, '客房\n壓扣'), (505, '客房\n壓扣'),
            (506, '客房\n壓扣'), (507, '客房\n壓扣')
        ],
        'public': [
            (1, '公共澡堂\n壓扣x2'), (2, '殘障廁所\n壓扣x2')
        ]
    },
    '3F': {
        'customer': [
            (308, '客房\n壓扣'), (309, '客房\n壓扣'), (310, '客房\n壓扣'), (311, '客房\n壓扣'),
            (312, '客房\n壓扣')
        ],
        'customer2': [
            (301, '客房\n壓扣'), (302, '客房\n壓扣'), (303, '客房\n壓扣'), (305, '客房\n壓扣'),
            (306, '客房\n壓扣'), (307, '客房\n壓扣')
        ]
    },
    '2F': {
        'customer': [
            (208, '客房\n壓扣'), (209, '客房\n壓扣'), (210, '客房\n壓扣'), (211, '客房\n壓扣'),
            (212, '客房\n壓扣'), (213, '客房\n壓扣')
        ],
        'customer2': [
            (201, '客房\n壓扣'), (202, '客房\n壓扣'), (203, '客房\n壓扣'), (204, '客房\n壓扣'),
            (205, '客房\n壓扣'), (206, '客房\n壓扣')
        ],
        'public': [
            (1, '公共澡堂\n壓扣x4'), (2, '殘障廁所\n壓扣x2')
        ]
    }
}

# Vertical spacing between floors (pixels)
FLOOR_SPACING = 40


class IndicatorWidget(QWidget):
    """Single room indicator card with indicator inside card."""
    state_changed = pyqtSignal(str)
    
    def __init__(self, room_id, room_type, img_dir, parent=None):
        super().__init__(parent)
        self.room_id = room_id
        self.room_type = room_type
        self.img_dir = img_dir
        self.state = 'normal'
        self.blink_state = False
        
        self.setMinimumSize(160, 160)
        self.setMaximumSize(160, 160)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        
        # Card container with border and background
        card_widget = QWidget()
        card_layout = QVBoxLayout(card_widget)
        # reduce bottom margin so indicator can sit nearer card bottom
        card_layout.setContentsMargins(10, 12, 10, 6)
        card_layout.setSpacing(6)

        # allow the card to expand to fill the IndicatorWidget area
        card_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        card_widget.setStyleSheet('''
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                    stop:0 #6b4f2a, stop:1 #8a6b3a);
                border-radius: 5px;
                border: 2px solid #5a3f1a;
            }
        ''')
        
        # Room number (blue)
        self.button_label = QLabel(str(room_id))
        self.button_label.setFont(QFont('Arial', 12, QFont.Bold))
        self.button_label.setStyleSheet('color: #1e6bd6; background: transparent; border: none;')
        self.button_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.button_label)
        
        # Room type (purple)
        self.type_label = QLabel(room_type)
        self.type_label.setFont(QFont('Arial', 10, QFont.Bold))
        self.type_label.setStyleSheet('color: #d64dd6; background: transparent; border: none;')
        self.type_label.setAlignment(Qt.AlignCenter)
        self.type_label.setWordWrap(True)
        card_layout.addWidget(self.type_label)

        # push indicator toward bottom by adding stretch above it
        card_layout.addStretch(1)
        
        # Indicator light inside card
        self.indicator_label = QLabel()
        # increase default indicator size so it appears larger before first resize
        self.indicator_label.setFixedSize(80, 80)
        self.indicator_label.setStyleSheet('background: transparent; border: none;')
        self.indicator_label.setAlignment(Qt.AlignCenter)
        # place indicator aligned to bottom center so its bottom edge meets the card's bottom margin
        card_layout.addWidget(self.indicator_label, alignment=Qt.AlignHCenter | Qt.AlignBottom)

        # make the card occupy the widget area (no extra outer stretch)
        layout.addWidget(card_widget)
        
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self._toggle_blink)
        self.blink_interval = 500
        
        self.set_state('normal')
        # only the indicator should show a pointing-hand cursor to avoid mis-clicks
        try:
            self.indicator_label.setCursor(Qt.PointingHandCursor)
        except Exception:
            pass
    
    def mousePressEvent(self, event):
        """Open popup only when clicking the indicator (to avoid mis-clicks)."""
        try:
            # map the indicator label rect into this widget's coordinates
            top_left = self.indicator_label.mapTo(self, self.indicator_label.rect().topLeft())
            bottom_right = self.indicator_label.mapTo(self, self.indicator_label.rect().bottomRight())
            ind_rect = QRect(top_left, bottom_right)
        except Exception:
            # fallback to geometry (may be parent-relative)
            ind_rect = self.indicator_label.geometry()

        if ind_rect.contains(event.pos()):
            # use card's displayed name and type for popup title/content
            try:
                room_num = self.button_label.text()
                room_type_text = self.type_label.text().replace('\n', ' ')
            except Exception:
                room_num = str(self.room_id)
                room_type_text = self.room_type

            # title: show room number + first line of type (e.g. '307 客房')
            first_type_line = self.type_label.text().splitlines()[0] if self.type_label.text() else ''
            title = f"{room_num} {first_type_line}".strip()
            # message: show number + full type (preserve newline from card)
            message = f"{room_num}\n{self.type_label.text()}"
            # map current widget state to popup initial_state expected values
            initial_state = 'red' if self.state == 'alarm' else 'green'

            popup = PopupDialog(title, message, initial_state)
            result = popup.exec_()
            if result == QDialog.Accepted:
                self.set_state(popup.selected_state)
                self.state_changed.emit(popup.selected_state)
        else:
            # ignore clicks outside the indicator (do not open popup)
            super().mousePressEvent(event)
    
    def set_state(self, state):
        """Set indicator state."""
        self.state = state
        if state == 'alarm':
            self.blink_state = False
            self.blink_timer.start(self.blink_interval)
            self._update_indicator()
        else:
            self.blink_timer.stop()
            self.blink_state = False
            self._update_indicator()
    
    def _toggle_blink(self):
        """Toggle blink."""
        self.blink_state = not self.blink_state
        self._update_indicator()
    
    def _update_indicator(self):
        """Update indicator color."""
        if self.state == 'normal':
            color = 'green'
        elif self.state == 'alarm':
            color = 'red' if self.blink_state else 'yellow'
        elif self.state == 'warning':
            color = 'yellow'
        else:
            color = 'gray'
        
        self._set_color(color)
    
    def _set_color(self, color):
        """Set indicator color from PNG file."""
        color_map = {
            'green': 'green.png',
            'red': 'red.png',
            'yellow': 'yellow.png',
            'gray': 'gray.png',
        }
        
        filename = color_map.get(color, 'green.png')
        img_path = os.path.join(self.img_dir, filename)
        
        if os.path.exists(img_path):
            try:
                pixmap = QPixmap(img_path)
                if not pixmap.isNull():
                    # scale pixmap to the indicator_label's current size so it fills the surrounding box
                    w = max(1, self.indicator_label.width())
                    h = max(1, self.indicator_label.height())
                    # PNGs are square; use IgnoreAspectRatio to force fill the box exactly
                    scaled = pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    self.indicator_label.setPixmap(scaled)
            except Exception as e:
                print(f'Error loading {img_path}: {e}')


class ScadaDialog(QMainWindow):
    """Main SCADA window matching Excel layout exactly."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.workspace_root = Path(__file__).parent.parent
        # Image directory and indicator registry
        self.img_dir = os.path.join(self.workspace_root, 'img')
        self._indicator_widgets = {}
        # Track number of card 'slots' per row to compute scaling
        self._row_slot_counts = []
        # Base layout metrics (used to compute proportional sizes)
        self.BASE_WINDOW_WIDTH = 1600
        self.BASE_WINDOW_HEIGHT = 1000
        self.BASE_CARD_SIZE = 180
        self.BASE_H_SPACING = 8
        self.BASE_SIDE_MARGIN = 20
        self.MIN_CARD_SIZE = 100
        
        self.setWindowTitle('北投享溫泉 保全壓扣系統 by lioil')
        self.setGeometry(100, 100, 1600, 1000)
        
        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Scroll area with no horizontal scrollbar
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { background-color: #1E2228; border: none; }')
        scroll.horizontalScrollBar().setStyleSheet('QScrollBar:horizontal { height: 0px; }')
        scroll.verticalScrollBar().setStyleSheet('QScrollBar:vertical { width: 0px; }')
        
        # Room container
        self.room_container = QWidget()
        self.room_layout = QVBoxLayout(self.room_container)
        self.room_layout.setContentsMargins(20, 20, 20, 20)
        self.room_layout.setSpacing(0)
        
        # Build layout
        self._build_layout()
        
        scroll.setWidget(self.room_container)
        main_layout.addWidget(scroll)
        
        self.setCentralWidget(main_widget)
        self._apply_dark_bg(main_widget)
        
        # Maximize window
        self.showMaximized()
    
    def _create_room_row(self, rooms, show_label=False, label_text="", show_public=False, public_rooms=None):
        """Create a horizontal row of room cards with optional public rooms."""
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(8)
        
        # Track slot count for this row (customer rooms + public spacer + public rooms)
        slots = len(rooms)
        if show_public and public_rooms:
            slots += 1 + len(public_rooms)
        # record slots for later scaling calculations
        try:
            self._row_slot_counts.append(slots)
        except Exception:
            pass

        # Add customer rooms
        for room_id, room_type in rooms:
            indicator = IndicatorWidget(room_id, room_type, self.img_dir)
            indicator.set_state('normal')
            row_l.addWidget(indicator)
            self._indicator_widgets[room_id] = indicator
        
        # Add spacer if public rooms follow (mark object name for adaptive resize)
        if show_public and public_rooms:
            spacer = QWidget()
            spacer.setObjectName('public_spacer')
            spacer.setFixedWidth(self.BASE_CARD_SIZE + self.BASE_H_SPACING)  # initial
            row_l.addWidget(spacer)
            
            # Add public rooms
            for room_id, room_type in public_rooms:
                indicator = IndicatorWidget(room_id, room_type, self.img_dir)
                indicator.set_state('normal')
                row_l.addWidget(indicator)
                self._indicator_widgets[f'{label_text}_public_{room_id}'] = indicator
        
        # Add floor label on right
        if show_label:
            label = QLabel(label_text)
            label.setObjectName('floor_label')
            label.setFont(QFont('Arial', 20, QFont.Bold))
            label.setStyleSheet('color: #2EA3FF; margin: 0px 10px;')
            label.setAlignment(Qt.AlignCenter)
            label.setFixedWidth(50)
            row_l.addWidget(label)
        
        row_l.addStretch(1)
        return row_w
    
    def _build_layout(self):
        """Build exact Excel layout with public rooms beside customer rooms."""
        
        # Outer horizontal layout for centering
        outer_h = QHBoxLayout()
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_h.setSpacing(0)
        outer_h.addStretch(1)
        
        # Main vertical layout
        main_v = QVBoxLayout()
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(0)
        # add top stretch so content has symmetric stretch above and below
        # this centers the entire content vertically within available space
        main_v.addStretch(1)
        
        # 5F container
        container_5f = QWidget()
        layout_5f = QVBoxLayout(container_5f)
        layout_5f.setContentsMargins(0, 0, 0, 0)
        layout_5f.setSpacing(0)
        layout_5f.addWidget(self._create_room_row(ROOMS_DATA['5F']['top_row']))
        layout_5f.addWidget(self._create_room_row(
            ROOMS_DATA['5F']['customer'],
            show_label=True,
            label_text='5F',
            show_public=True,
            public_rooms=ROOMS_DATA['5F']['public']
        ))
        main_v.addWidget(container_5f)
        
        # Spacer between floors (increase to separate floors visually)
        spacer1 = QWidget()
        spacer1.setFixedHeight(FLOOR_SPACING)
        main_v.addWidget(spacer1)
        
        # 3F container
        container_3f = QWidget()
        layout_3f = QVBoxLayout(container_3f)
        layout_3f.setContentsMargins(0, 0, 0, 0)
        layout_3f.setSpacing(0)
        layout_3f.addWidget(self._create_room_row(ROOMS_DATA['3F']['customer']))
        layout_3f.addWidget(self._create_room_row(
            ROOMS_DATA['3F']['customer2'],
            show_label=True,
            label_text='3F'
        ))
        main_v.addWidget(container_3f)
        
        # Spacer between floors (increase to separate floors visually)
        spacer2 = QWidget()
        spacer2.setFixedHeight(FLOOR_SPACING)
        main_v.addWidget(spacer2)
        
        # 2F container
        container_2f = QWidget()
        layout_2f = QVBoxLayout(container_2f)
        layout_2f.setContentsMargins(0, 0, 0, 0)
        layout_2f.setSpacing(0)
        layout_2f.addWidget(self._create_room_row(ROOMS_DATA['2F']['customer']))
        layout_2f.addWidget(self._create_room_row(
            ROOMS_DATA['2F']['customer2'],
            show_label=True,
            label_text='2F',
            show_public=True,
            public_rooms=ROOMS_DATA['2F']['public']
        ))
        main_v.addWidget(container_2f)
        
        main_v.addStretch(1)
        
        outer_h.addLayout(main_v)
        outer_h.addStretch(1)
        
        # Add to room layout
        container = QWidget()
        container.setLayout(outer_h)
        self.room_layout.addWidget(container)
    
    def _apply_dark_bg(self, widget):
        """Apply dark background."""
        widget.setAutoFillBackground(True)
        pal = widget.palette()
        pal.setColor(QPalette.Window, QColor('#1E2228'))
        widget.setPalette(pal)
    
    def set_room_state(self, room_id, state):
        """Set room state."""
        if room_id in self._indicator_widgets:
            self._indicator_widgets[room_id].set_state(state)
    
    def get_room_state(self, room_id):
        """Get room state."""
        if room_id in self._indicator_widgets:
            return self._indicator_widgets[room_id].state
        return None
    
    def resizeEvent(self, event):
        """Handle resize with auto-scaling."""
        super().resizeEvent(event)
        # Determine available width/height for proportional layout
        content_w = self.room_container.width() if getattr(self, 'room_container', None) is not None else self.width()
        content_h = self.room_container.height() if getattr(self, 'room_container', None) is not None else self.height()

        # Use safe fallbacks
        avail_w = content_w if content_w and content_w > 0 else self.width()
        avail_h = content_h if content_h and content_h > 0 else self.height()

        # Compute scale factors relative to base window
        scale_w = avail_w / float(self.BASE_WINDOW_WIDTH)
        scale_h = avail_h / float(self.BASE_WINDOW_HEIGHT)
        # preserve proportions: use the smaller scale so whole layout fits
        global_scale = min(scale_w, scale_h)

        # Derived sizes
        h_spacing = max(4, int(self.BASE_H_SPACING * global_scale))
        side_margin = max(8, int(self.BASE_SIDE_MARGIN * global_scale))
        floor_space_scaled = max(4, int(FLOOR_SPACING * global_scale))

        # compute widest row slots and number of rows
        try:
            max_slots = max(self._row_slot_counts) if self._row_slot_counts else 1
        except Exception:
            max_slots = 1
        try:
            num_rows = len(self._row_slot_counts) if self._row_slot_counts else 1
        except Exception:
            num_rows = 1

        # card width constrained by available width
        card_w_by_width = max(self.MIN_CARD_SIZE, int((avail_w - 2 * side_margin - (max_slots - 1) * h_spacing) / max_slots))

        # card height constrained by available height (consider floor spacers and some padding)
        vertical_padding = 2 * side_margin + (max(0, len(ROOMS_DATA) - 1) * floor_space_scaled) + (num_rows - 1) * h_spacing + 40
        card_h_by_height = max(self.MIN_CARD_SIZE, int((avail_h - vertical_padding) / num_rows))

        # final card size is the limiting dimension; keep square cards
        card_size = min(card_w_by_width, card_h_by_height)
        scale = card_size / float(self.BASE_CARD_SIZE)

        # Update indicator widgets sizes and fonts
        for key, widget in list(self._indicator_widgets.items()):
            try:
                if widget is None or widget.parent() is None:
                    continue

                new_w = max(self.MIN_CARD_SIZE, int(card_size))
                new_h = max(self.MIN_CARD_SIZE, int(card_size))
                widget.setMinimumSize(new_w, new_h)
                widget.setMaximumSize(new_w, new_h)

                btn_font = widget.button_label.font()
                btn_font.setPointSize(max(10, int(12 * scale)))
                widget.button_label.setFont(btn_font)

                type_font = widget.type_label.font()
                type_font.setPointSize(max(9, int(10 * scale)))
                widget.type_label.setFont(type_font)

                # compute indicator size so it never exceeds the card inner height
                # available = card_size - margins - labels heights - extra spacing
                try:
                    label_h = widget.button_label.sizeHint().height() + widget.type_label.sizeHint().height()
                except Exception:
                    label_h = 40
                margins_top = 12
                margins_bottom = 6
                extra_spacing = 12
                available = int(card_size - (margins_top + margins_bottom) - label_h - extra_spacing)
                # prefer a large fraction but clamp to available inner height
                preferred = int(card_size * 0.6)
                ind_size = max(36, min(preferred, max(24, available)))
                if ind_size < 24:
                    ind_size = 24
                widget.indicator_label.setFixedSize(ind_size, ind_size)

            except Exception:
                if key in self._indicator_widgets:
                    del self._indicator_widgets[key]

        # Adapt public spacers and floor labels to new card size
        for child in self.room_container.findChildren(QWidget):
            try:
                name = child.objectName()
                if name and name.startswith('public_spacer'):
                    child.setFixedWidth(int(card_size + h_spacing))
                if name and name.startswith('floor_label'):
                    child.setFixedWidth(max(30, int(50 * global_scale)))
            except Exception:
                pass
