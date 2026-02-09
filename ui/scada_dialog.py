import os
import importlib.util
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QDialog, QApplication
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QScreen
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QEvent


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


# 房間數據（None 表示空位）
ROOMS_DATA = {
    '5F': {
        'row1': [(508, '客房\n壓扣'), (509, '客房\n壓扣'), None, None, (512, '客房\n壓扣'), (513, '客房\n壓扣')],
        'row2': [(501, '客房\n壓扣'), (502, '客房\n壓扣'), (503, '客房\n壓扣'), (505, '客房\n壓扣'),
                 (506, '客房\n壓扣'), (507, '客房\n壓扣')],
        'public': [(1, '公共澡堂\n壓扣x2'), (2, '殘障廁所\n壓扣x2')]
    },
    '3F': {
        'row1': [(308, '客房\n壓扣'), (309, '客房\n壓扣'), (310, '客房\n壓扣'), None, (312, '客房\n壓扣'),
                 (313, '客房\n壓扣')],
        'row2': [(301, '客房\n壓扣'), (302, '客房\n壓扣'), (303, '客房\n壓扣'), (305, '客房\n壓扣'),
                 (306, '客房\n壓扣'), (307, '客房\n壓扣')]
    },
    '2F': {
        'row1': [(208, '客房\n壓扣'), (209, '客房\n壓扣'), (210, '客房\n壓扣'), (211, '客房\n壓扣'),
                 (212, '客房\n壓扣'), (213, '客房\n壓扣')],
        'row2': [(201, '客房\n壓扣'), (202, '客房\n壓扣'), (203, '客房\n壓扣'), (204, '客房\n壓扣'),
                 (205, '客房\n壓扣'), (206, '客房\n壓扣')],
        'public': [(1, '公共澡堂\n壓扣x4'), (2, '殘障廁所\n壓扣x2')]
    }
}


class ClickableLabel(QLabel):
    """可點擊的標籤。"""
    clicked = pyqtSignal()
    
    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class RoomCard(QWidget):
    """房間卡片 - 正方形，房號+壓扣在上，綠燈在下。"""
    clicked = pyqtSignal()
    
    def __init__(self, room_id, room_type, img_dir, is_public=False, floor: str = None):
        super().__init__()
        self.room_id = room_id
        self.room_type = room_type
        self.is_public = is_public
        self.floor = floor
        self.img_dir = img_dir
        self.state = 'normal'
        self.blink_state = False
        
        self.setFixedSize(90, 90)
        
        # 設定 objectName 以便在樣式表中限定只有此元件有邊框
        self.setObjectName('RoomCard')
        
        # 啟用背景繪製（必須設定才能讓 QWidget 顯示背景）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        
        # 偵測淺色/深色模式並設定卡片樣式
        self._update_card_style()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(1)
        
        # 房號
        first_line = room_type.splitlines()[0] if room_type else ''
        if is_public:
            room_text = first_line
        else:
            room_text = f"{room_id}{first_line}"
        
        self.room_label = QLabel(room_text)
        self.room_label.setFont(QFont('微軟正黑體', 11, QFont.Weight.Bold))
        # 使用應用程式 palette 而非硬編碼色碼，讓文字能響應系統主題
        self.room_label.setStyleSheet('QLabel { background: transparent; border: none; }')
        app = QApplication.instance()
        if app:
            self.room_label.setPalette(app.palette())
        self.room_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.room_label)
        
        # 壓扣
        pressure_line = room_type.splitlines()[1] if len(room_type.splitlines()) > 1 else ''
        self.pressure_label = QLabel(pressure_line)
        self.pressure_label.setFont(QFont('微軟正黑體', 8, QFont.Weight.Bold))
        # 使用應用程式 palette 而非硬編碼色碼
        self.pressure_label.setStyleSheet('QLabel { background: transparent; border: none; }')
        if app:
            self.pressure_label.setPalette(app.palette())
        self.pressure_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pressure_label.setWordWrap(True)
        layout.addWidget(self.pressure_label)
        
        # 彈簧
        layout.addStretch(1)
        
        # 燈號（可點擊）
        self.light_label = ClickableLabel()
        self.light_label.setFixedSize(48, 48)
        self.light_label.setStyleSheet('QLabel { background: transparent; border: none; }')
        self.light_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.light_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.light_label.clicked.connect(self._on_light_clicked)
        layout.addWidget(self.light_label, alignment=Qt.AlignmentFlag.AlignHCenter)
        
        layout.addSpacing(0)
        
        # 計時器（閃爍）
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self._on_blink)
        self.blink_interval = 500
        
        self.set_state('normal')
    
    def _update_card_style(self):
        """根據系統淺色/深色模式更新卡片樣式。"""
        # 偵測淺色/深色模式
        app = QApplication.instance()
        if app:
            palette = app.palette()
            bg_color = palette.color(palette.ColorRole.Window)
            # 計算背景亮度
            lum = 0.2126 * bg_color.red() + 0.7152 * bg_color.green() + 0.0722 * bg_color.blue()
            is_light = lum > 128
        else:
            is_light = False
        
        if is_light:
            # 淺色模式：較深的卡片背景
            self.setStyleSheet('''
                QWidget#RoomCard {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #e8e8e8, stop:1 #d8d8d8);
                    border: 2px solid #b0b0b0;
                    border-radius: 5px;
                }
            ''')
        else:
            # 深色模式：較亮的卡片背景
            self.setStyleSheet('''
                QWidget#RoomCard {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                        stop:0 #4a4a4a, stop:1 #3a3a3a);
                    border: 2px solid #5a5a5a;
                    border-radius: 5px;
                }
            ''')
    
    def _on_light_clicked(self):
        """點擊燈號打開設置對話。"""
        if not PopupDialog:
            return
        
        room_num = self.room_label.text()
        first_line = self.room_type.splitlines()[0] if self.room_type else ''
        if self.is_public:
            # include floor for public facilities to avoid duplicate names
            if getattr(self, 'floor', None):
                title = f"{self.floor} {first_line}".strip()
            else:
                title = first_line
        else:
            title = f"{self.room_id} {first_line}".strip()
        
        message = f"{room_num}\n{self.pressure_label.text()}"
        initial_state = 'red' if self.state == 'alarm' else 'green'
        
        popup = PopupDialog(title, message, initial_state)
        if popup.exec() == QDialog.DialogCode.Accepted:
            self.set_state(popup.selected_state)
            self.clicked.emit()
    
    def set_state(self, state):
        """設置狀態。"""
        self.state = state
        if state == 'alarm':
            self.blink_state = False
            self.blink_timer.start(self.blink_interval)
            self._update_light()
        else:
            self.blink_timer.stop()
            self.blink_state = False
            self._update_light()
    
    def _on_blink(self):
        self.blink_state = not self.blink_state
        self._update_light()
    
    def _update_light(self):
        """更新燈號。"""
        if self.state == 'normal':
            color = 'green'
        elif self.state == 'alarm':
            color = 'red' if self.blink_state else 'yellow'
        elif self.state == 'warning':
            color = 'yellow'
        else:
            color = 'gray'
        
        # 加載 PNG
        filename = {'green': 'green.png', 'red': 'red.png', 
                   'yellow': 'yellow.png', 'gray': 'gray.png'}[color]
        path = os.path.join(self.img_dir, filename)
        
        if os.path.exists(path):
            try:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    size = self.light_label.width()
                    scaled = pixmap.scaledToWidth(size, Qt.TransformationMode.SmoothTransformation)
                    self.light_label.setPixmap(scaled)
            except:
                pass
    
    def scale_to_size(self, size):
        """縮放卡片到指定大小。"""
        self.setFixedSize(size, size)
        
        scale = size / 90.0
        
        # 縮放字體
        room_font = QFont('微軟正黑體', max(8, int(11 * scale)), QFont.Weight.Bold)
        self.room_label.setFont(room_font)
        
        pressure_font = QFont('微軟正黑體', max(6, int(8 * scale)), QFont.Weight.Bold)
        self.pressure_label.setFont(pressure_font)
        
        # 縮放燈號
        light_size = max(30, int(48 * scale))
        self.light_label.setFixedSize(light_size, light_size)
        self._update_light()


class FloorLabel(QLabel):
    """樓層標籤（左邊或右邊）。"""
    def __init__(self, text, width, height=None, align_right=True):
        super().__init__(text)
        self._base_font_size = 16
        self._base_width = width
        self.setFont(QFont('微軟正黑體', self._base_font_size, QFont.Weight.Bold))
        # 使用應用程式 palette 設定文字色與底線色，避免硬編碼
        app = QApplication.instance()
        if app:
            pal = app.palette()
            text_color = pal.color(pal.ColorRole.WindowText).name()
            highlight = pal.color(pal.ColorRole.Highlight).name()
            self.setStyleSheet(f'''
                QLabel {{
                    color: {text_color};
                    background: transparent;
                    border-bottom: 2px solid {highlight};
                    padding: 0px;
                    margin: 0px;
                }}
            ''')
        else:
            self.setStyleSheet('''
                QLabel {
                    background: transparent;
                    border-bottom: 2px solid #2EA3FF;
                    padding: 0px;
                    margin: 0px;
                }
            ''')
        # 左側標籤文字靠右，右側標籤文字靠左
        if align_right:
            self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        else:
            self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.setMinimumWidth(40)
        self.setMaximumWidth(width)
        if height is not None:
            self.setFixedHeight(height)
        self.min_height = height
    
    def scale_to_size(self, scale):
        """根據比例縮放字體和寬度。"""
        font_size = max(10, int(self._base_font_size * scale))
        self.setFont(QFont('微軟正黑體', font_size, QFont.Weight.Bold))
        new_width = max(40, int(self._base_width * scale))
        self.setFixedWidth(new_width)


class ScadaDialog(QMainWindow):
    """主視窗 - 完整的房間管理介面。"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('北投享溫泉 保全壓扣系統 by lioil')
        
        self.workspace_root = Path(__file__).parent.parent
        self.img_dir = os.path.join(self.workspace_root, 'img')
        self.room_cards = {}  # room_id -> RoomCard
        self._resizing = False  # 防止 resizeEvent 無限循環
        self._last_card_size = 0  # 記錄上次卡片大小
        
        # 獲取螢幕可用區域（扣除工具列）
        screen = QApplication.primaryScreen()
        if screen:
            available_geometry = screen.availableGeometry()
            self.screen_width = available_geometry.width()
            self.screen_height = available_geometry.height()
        else:
            self.screen_width = 1920
            self.screen_height = 1080
        
        # 設定視窗大小為可用區域
        self.setGeometry(0, 0, self.screen_width, self.screen_height)
        
        # 主佈局（不使用滾動區域）
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 房間容器
        self.room_widget = QWidget()
        self.room_layout = QVBoxLayout(self.room_widget)
        self.room_layout.setContentsMargins(10, 8, 10, 10)
        self.room_layout.setSpacing(0)
        
        # 構建房間佈局
        self._build_rooms()
        
        main_layout.addWidget(self.room_widget)
        self.setCentralWidget(main_widget)
        
        # 背景色 — 使用應用程式主題色，支援系統淺色/深色
        main_widget.setAutoFillBackground(True)
        app = QApplication.instance()
        if app:
            # 使用全域應用程式 palette，讓視窗背景跟隨系統主題
            main_widget.setPalette(app.palette())
        else:
            # fallback
            palette = main_widget.palette()
            palette.setColor(palette.ColorRole.Window, QColor('#1E2228'))
            main_widget.setPalette(palette)
        
        # 設定最小視窗大小，允許自由縮放和 Snap Layouts
        self.setMinimumSize(800, 500)
        
        # 顯示最大化視窗
        self.showMaximized()
        
        # 延遲執行初始縮放，確保視窗已經正確顯示
        QTimer.singleShot(100, self._initial_scale)
    
    def _build_rooms(self):
        """構建房間佈局。"""
        # 外層水平佈局
        outer_h = QHBoxLayout()
        outer_h.setContentsMargins(0, 0, 0, 0)
        outer_h.setSpacing(0)
        outer_h.addStretch(1)
        
        # 主垂直佈局
        main_v = QVBoxLayout()
        main_v.setContentsMargins(0, 0, 0, 0)
        main_v.setSpacing(3)  # 樓層間距與行間距一致
        
        # ===== 5F =====
        floor_5f = QWidget()
        layout_5f = QHBoxLayout(floor_5f)
        layout_5f.setContentsMargins(0, 0, 0, 0)
        layout_5f.setSpacing(0)
        
        # 5F 左標籤（動態高度，初始設定為0）
        label_5f = FloorLabel('5F', 80, align_right=True)
        self.label_5f = label_5f  # 保存引用以供後續調整
        layout_5f.addWidget(label_5f, alignment=Qt.AlignmentFlag.AlignBottom)
        
        # 5F 房間（2行）
        rooms_5f = QVBoxLayout()
        rooms_5f.setContentsMargins(0, 0, 0, 0)
        rooms_5f.setSpacing(3)  # 行間距與水平間距一致
        
        # 5F 第1行：包含空位
        row1_5f = QHBoxLayout()
        row1_5f.setContentsMargins(0, 0, 0, 0)
        row1_5f.setSpacing(3)
        self._spacers_5f_row1 = []  # 保存空位引用
        for item in ROOMS_DATA['5F']['row1']:
            if item is None:
                # 空位
                spacer = QWidget()
                spacer.setFixedSize(90, 90)
                spacer.setObjectName('room_spacer_5f_row1')
                row1_5f.addWidget(spacer)
                self._spacers_5f_row1.append(spacer)
            else:
                room_id, room_type = item
                card = RoomCard(room_id, room_type, self.img_dir)
                row1_5f.addWidget(card)
                self.room_cards[room_id] = card
        row1_5f.addStretch(1)
        rooms_5f.addLayout(row1_5f)
        
        # 5F 第2行：6張卡片 + 公共設施
        row2_5f = QHBoxLayout()
        row2_5f.setContentsMargins(0, 0, 0, 0)
        row2_5f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['5F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir)
            row2_5f.addWidget(card)
            self.room_cards[room_id] = card
        
        # 間隙
        spacer = QWidget()
        spacer.setFixedWidth(100)
        spacer.setObjectName('spacer_5f_public')
        row2_5f.addWidget(spacer)
        
        # 公共設施
        for room_id, room_type in ROOMS_DATA['5F']['public']:
            card = RoomCard(room_id, room_type, self.img_dir, is_public=True, floor='5F')
            row2_5f.addWidget(card)
            self.room_cards[f'public_5f_{room_id}'] = card
        
        # 右側 5F 標籤
        label_5f_right = FloorLabel('5F', 90, align_right=False)
        self.label_5f_right = label_5f_right  # 保存引用
        row2_5f.addWidget(label_5f_right, alignment=Qt.AlignmentFlag.AlignBottom)
        row2_5f.addStretch(1)
        
        rooms_5f.addLayout(row2_5f)
        
        layout_5f.addLayout(rooms_5f)
        main_v.addWidget(floor_5f)
        
        # ===== 3F =====
        floor_3f = QWidget()
        layout_3f = QHBoxLayout(floor_3f)
        layout_3f.setContentsMargins(0, 0, 0, 0)
        layout_3f.setSpacing(0)
        
        # 3F 左標籤
        label_3f = FloorLabel('3F', 80, align_right=True)
        self.label_3f = label_3f  # 保存引用
        layout_3f.addWidget(label_3f, alignment=Qt.AlignmentFlag.AlignBottom)
        
        # 3F 房間
        rooms_3f = QVBoxLayout()
        rooms_3f.setContentsMargins(0, 0, 0, 0)
        rooms_3f.setSpacing(3)  # 行間距與水平間距一致
        
        # 3F 第1行：包含空位
        row1_3f = QHBoxLayout()
        row1_3f.setContentsMargins(0, 0, 0, 0)
        row1_3f.setSpacing(3)
        self._spacers_3f_row1 = []  # 保存空位引用
        for item in ROOMS_DATA['3F']['row1']:
            if item is None:
                # 空位
                spacer = QWidget()
                spacer.setFixedSize(90, 90)
                spacer.setObjectName('room_spacer_3f_row1')
                row1_3f.addWidget(spacer)
                self._spacers_3f_row1.append(spacer)
            else:
                room_id, room_type = item
                card = RoomCard(room_id, room_type, self.img_dir)
                row1_3f.addWidget(card)
                self.room_cards[room_id] = card
        row1_3f.addStretch(1)
        rooms_3f.addLayout(row1_3f)
        
        # 3F 第2行：6張卡片
        row2_3f = QHBoxLayout()
        row2_3f.setContentsMargins(0, 0, 0, 0)
        row2_3f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['3F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir)
            row2_3f.addWidget(card)
            self.room_cards[room_id] = card
        row2_3f.addStretch(1)
        rooms_3f.addLayout(row2_3f)
        
        layout_3f.addLayout(rooms_3f)
        main_v.addWidget(floor_3f)
        
        # ===== 2F & 1F =====
        floor_2f = QWidget()
        layout_2f = QHBoxLayout(floor_2f)
        layout_2f.setContentsMargins(0, 0, 0, 0)
        layout_2f.setSpacing(0)
        
        # 2F 左標籤
        label_2f = FloorLabel('2F', 80, align_right=True)
        self.label_2f = label_2f  # 保存引用
        layout_2f.addWidget(label_2f, alignment=Qt.AlignmentFlag.AlignBottom)
        
        # 2F 房間
        rooms_2f = QVBoxLayout()
        rooms_2f.setContentsMargins(0, 0, 0, 0)
        rooms_2f.setSpacing(3)  # 行間距與水平間距一致
        
        # 2F 第1行
        row1_2f = QHBoxLayout()
        row1_2f.setContentsMargins(0, 0, 0, 0)
        row1_2f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['2F']['row1']:
            card = RoomCard(room_id, room_type, self.img_dir)
            row1_2f.addWidget(card)
            self.room_cards[room_id] = card
        row1_2f.addStretch(1)
        rooms_2f.addLayout(row1_2f)
        
        # 2F 第2行：6張卡片（左側客房）+ 公共設施
        row2_2f = QHBoxLayout()
        row2_2f.setContentsMargins(0, 0, 0, 0)
        row2_2f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['2F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir)
            row2_2f.addWidget(card)
            self.room_cards[room_id] = card
        
        # 間隙
        spacer = QWidget()
        spacer.setFixedWidth(100)
        spacer.setObjectName('spacer_2f_public')
        row2_2f.addWidget(spacer)
        
        # 公共設施（1F）
        for room_id, room_type in ROOMS_DATA['2F']['public']:
            # these public entries are positioned as 1F on the UI
            card = RoomCard(room_id, room_type, self.img_dir, is_public=True, floor='1F')
            row2_2f.addWidget(card)
            self.room_cards[f'public_2f_{room_id}'] = card
        
        # 1F 右標籤（只在第2行，與殘障廁所對齊）
        label_1f = FloorLabel('1F', 90, align_right=False)
        self.label_1f = label_1f  # 保存引用
        row2_2f.addWidget(label_1f, alignment=Qt.AlignmentFlag.AlignBottom)
        row2_2f.addStretch(1)
        
        rooms_2f.addLayout(row2_2f)
        
        layout_2f.addLayout(rooms_2f)
        main_v.addWidget(floor_2f)
        
        outer_h.addLayout(main_v)
        outer_h.addStretch(1)
        
        container = QWidget()
        container.setLayout(outer_h)
        self.room_layout.addWidget(container)
    
    def set_room_state(self, room_id, state):
        """設置房間狀態。"""
        if room_id in self.room_cards:
            self.room_cards[room_id].set_state(state)
    
    def get_room_state(self, room_id):
        """獲取房間狀態。"""
        if room_id in self.room_cards:
            return self.room_cards[room_id].state
        return None
    
    def _initial_scale(self):
        """初始化時執行一次縮放。"""
        self._last_card_size = 0  # 重置以強制縮放
        self._do_scale()
    
    def resizeEvent(self, event):
        """視窗大小改變時重新縮放卡片。"""
        super().resizeEvent(event)
        
        # 防止無限循環
        if self._resizing:
            return
        
        # 執行縮放
        self._do_scale()
    
    def _do_scale(self):
        """執行卡片縮放邏輯。"""
        avail_w = self.room_widget.width() if self.room_widget else self.width()
        avail_h = self.room_widget.height() if self.room_widget else self.height()
        
        if avail_w <= 0:
            avail_w = 1600
        if avail_h <= 0:
            avail_h = 900
        
        # 基準佈局參數（基於 90px 卡片大小）
        BASE_CARD = 90
        BASE_LEFT_LABEL = 80
        BASE_RIGHT_LABEL = 90
        H_MARGINS = 20     # 左右邊距
        V_MARGINS = 18     # 上下邊距 (10 + 8)
        SPACING = 3        # 卡片間距
        MAX_CARDS_H = 9    # 水平最大卡片數
        TOTAL_ROWS = 6     # 垂直總行數
        FLOOR_GAPS = 2     # 樓層之間的間隙數量
        ROW_GAPS = 3       # 每層樓內的行間距數量
        
        # 先用固定標籤寬度估算卡片大小
        est_label_w = BASE_LEFT_LABEL + BASE_RIGHT_LABEL
        available_w = avail_w - H_MARGINS - est_label_w - SPACING
        card_size_by_width = int((available_w - (MAX_CARDS_H - 1) * SPACING) / MAX_CARDS_H)
        
        # 根據高度計算卡片大小
        total_gaps = ROW_GAPS + FLOOR_GAPS
        available_h = avail_h - V_MARGINS - total_gaps * SPACING
        card_size_by_height = int(available_h / TOTAL_ROWS)
        
        # 取較小值以確保全部顯示
        card_size = max(60, min(card_size_by_width, card_size_by_height))
        
        # 如果卡片大小沒有改變，不需要重新縮放
        if card_size == self._last_card_size:
            return
        
        self._resizing = True
        self._last_card_size = card_size
        
        # 縮放所有卡片
        for card in self.room_cards.values():
            if card:
                try:
                    card.scale_to_size(card_size)
                except:
                    pass
        
        # 計算縮放比例
        scale = card_size / 90.0
        
        # 縮放樓層標籤
        for label in [self.label_5f, self.label_3f, self.label_2f, 
                      self.label_5f_right, self.label_1f]:
            if label and hasattr(label, 'scale_to_size'):
                label.scale_to_size(scale)
        
        # 縮放公共設施間隙和房間空位
        spacer_size = int(card_size + SPACING)
        for child in self.room_widget.findChildren(QWidget):
            try:
                obj_name = child.objectName()
                if obj_name.startswith('spacer_'):
                    child.setFixedWidth(spacer_size)
                elif obj_name.startswith('room_spacer_'):
                    child.setFixedSize(card_size, card_size)
            except:
                pass
        
        # 動態計算樓層標籤高度
        self._update_floor_label_heights()
        
        self._resizing = False
    
    def _update_floor_label_heights(self):
        """根據房間卡片的實際高度動態更新樓層標籤高度。
        
        根據 scada.png 的布局要求：
        - 左側5F底線：對齊501客房的燈號下緣
        - 左側3F底線：對齊301客房的燈號下緣
        - 左側2F底線：對齊201客房的燈號下緣
        - 右側5F底線：對齊右上角殘障廁所的燈號下緣
        - 右側1F底線：對齊右下角殘障廁所的燈號下緣
        """
        if not hasattr(self, 'room_cards') or not self.room_cards:
            return
        
        # 獲取第一張卡片的實際高度（所有卡片高度相同）
        first_card = next(iter(self.room_cards.values()), None)
        if not first_card:
            return
        
        card_height = first_card.height()
        if card_height <= 0:
            return
        
        # 左側標籤：覆蓋該樓層的2行房間，底線對齊第2行燈號下緣
        # 由於使用 AlignBottom，標籤高度設為 2 * card_height 會使底線對齊第2行底部
        height_2rows = 2 * card_height
        
        # 5F 左側標籤：對齊501客房燈號下緣（2行高度）
        if hasattr(self, 'label_5f') and self.label_5f:
            self.label_5f.setFixedHeight(height_2rows)
        
        # 5F 右側標籤：只在第2行，對齊殘障廁所燈號下緣（1行高度）
        if hasattr(self, 'label_5f_right') and self.label_5f_right:
            self.label_5f_right.setFixedHeight(card_height)
        
        # 3F 左側標籤：對齊301客房燈號下緣（2行高度）
        if hasattr(self, 'label_3f') and self.label_3f:
            self.label_3f.setFixedHeight(height_2rows)
        
        # 2F 左側標籤：對齊201客房燈號下緣（2行高度）
        if hasattr(self, 'label_2f') and self.label_2f:
            self.label_2f.setFixedHeight(height_2rows)
        
        # 1F 右側標籤：只在第2行，對齊殘障廁所燈號下緣（1行高度）
        if hasattr(self, 'label_1f') and self.label_1f:
            self.label_1f.setFixedHeight(card_height)

    def _apply_palette(self):
        """Apply the current application palette to labels and components that used
        to have hard-coded colors, so they follow the system theme.
        """
        app = QApplication.instance()
        if not app:
            return
        pal = app.palette()

        # Update room cards' labels
        for card in self.room_cards.values():
            try:
                if hasattr(card, 'room_label'):
                    card.room_label.setPalette(pal)
                if hasattr(card, 'pressure_label'):
                    card.pressure_label.setPalette(pal)
            except:
                pass

        # Update floor labels style (recompute border color)
        highlight = pal.color(pal.ColorRole.Highlight).name()
        text_color = pal.color(pal.ColorRole.WindowText).name()
        for attr in ('label_5f', 'label_3f', 'label_2f', 'label_5f_right', 'label_1f'):
            lbl = getattr(self, attr, None)
            if lbl:
                try:
                    lbl.setStyleSheet(f"""
                        QLabel {{
                            color: {text_color};
                            background: transparent;
                            border-bottom: 2px solid {highlight};
                            padding: 0px;
                            margin: 0px;
                        }}
                    """)
                except:
                    pass
    
    def changeEvent(self, event):
        """處理視窗狀態變化事件，修改還原按鈕行為。"""
        # Window state change (restore down behavior)
        if event.type() == QEvent.Type.WindowStateChange:
            # 檢查是否從最大化狀態變為正常狀態
            if (event.oldState() & Qt.WindowState.WindowMaximized) and not (self.windowState() & Qt.WindowState.WindowMaximized):
                # 從最大化變為正常，將視窗調整為螢幕的50%
                screen = QApplication.primaryScreen()
                if screen:
                    screen_geometry = screen.geometry()
                    new_width = int(screen_geometry.width() * 0.5)
                    new_height = int(screen_geometry.height() * 0.5)
                    new_x = (screen_geometry.width() - new_width) // 2
                    new_y = (screen_geometry.height() - new_height) // 2
                    self.setGeometry(new_x, new_y, new_width, new_height)
        # Application palette change (system theme changed)
        elif event.type() == QEvent.Type.ApplicationPaletteChange:
            # Re-apply palette-derived styles to labels and widgets
            try:
                self._apply_palette()
            except:
                pass
        
        super().changeEvent(event)
