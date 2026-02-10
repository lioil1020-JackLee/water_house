import os
import sys
import importlib.util
from pathlib import Path
import csv
import threading
import asyncio
import tempfile
import shutil
from asyncua import Client, ua
from datetime import datetime
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QDialog, QApplication, QSystemTrayIcon, QMenu, QPushButton
)
from PyQt6.QtGui import QPixmap, QFont, QColor, QScreen, QIcon
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QEvent, QThread, pyqtSlot
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from PyQt6.QtCore import QUrl


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


class OPCUAClient(QThread):
    """OPC UA Client thread for reading tag values."""
    update_signal = pyqtSignal(dict)  # {tag_name: value}
    write_signal = pyqtSignal(str, object)  # tag_name, value
    write_failed_signal = pyqtSignal(str)  # tag_name
    connection_lost_signal = pyqtSignal()  # 連接斷線信號
    connection_restored_signal = pyqtSignal()  # 連接恢復信號
    
    def __init__(self, server_url, tag_list):
        super().__init__()
        self.server_url = server_url
        self.tag_list = tag_list  # list of (tag_name, node_id)
        self.running = True
        self.nodes = {}  # tag_name -> node
        self.client = None
        self.current_values = {}  # tag_name -> value, 待寫入的緩存值
        self.write_timestamps = {}  # tag_name -> timestamp, 寫入時間戳
        self.write_timeout = 10  # seconds
        self.last_emitted_values = {}  # tag_name -> last value, 上次發出的值，避免重複發出
        self.is_connected = False  # 連接狀態
        self.connection_fail_count = 0  # 連接失敗計數
        self.max_fail_count = 3  # 失敗次數上限，超過認為斷線
    
    def write_value(self, tag_name, value):
        """Write value to a tag."""
        if tag_name in self.nodes and self.client and hasattr(self, 'loop'):
            # Cache the write value and timestamp for read-ahead logic
            import time
            self.current_values[tag_name] = value
            self.write_timestamps[tag_name] = time.time()
            asyncio.run_coroutine_threadsafe(self._write_async(tag_name, value), self.loop)
    
    def read_value(self, tag_name):
        """Read value from a tag synchronously."""
        if tag_name in self.nodes and self.client:
            future = asyncio.run_coroutine_threadsafe(self._read_async(tag_name), self.loop)
            return future.result()
        return None
    
    async def _read_async(self, tag_name):
        try:
            node = self.nodes[tag_name]
            value = await node.read_value()
            return value
        except Exception as e:
            return None
    
    async def _write_async(self, tag_name, value):
        try:
            node = self.nodes[tag_name]
            # Directly use DataValue for writing to avoid server format issues
            if isinstance(value, bool):
                variant = ua.Variant(value, ua.VariantType.Boolean)
            elif isinstance(value, float):
                variant = ua.Variant(value, ua.VariantType.Float)
            elif isinstance(value, int):
                variant = ua.Variant(value, ua.VariantType.Int32)
            else:
                variant = ua.Variant(value)
            data_value = ua.DataValue(variant)
            await node.write_value(data_value)
            print(f"[OPC UA] 寫入成功: {tag_name} = {value}")
            # Write succeeded, keep cache until polling confirms
        except Exception as e:
            print(f"[OPC UA 錯誤] 寫入失敗: {tag_name} - {e}")
            # Write failed completely, emit failed signal
            self.write_failed_signal.emit(tag_name)
    
    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._run_client())
    
    async def _run_client(self):
        reconnect_delay = 2  # 重連延遲（秒）
        
        while self.running:
            self.client = Client(url=self.server_url)
            # Security mode = None, security policy = None, authentication = Anonymous (default)
            try:
                await self.client.connect()
                print(f"[OPC UA] 已連接到伺服器: {self.server_url}")
                
                # 連接成功，立即標記為已連接
                if not self.is_connected:
                    self.is_connected = True
                    print("[OPC UA] 連接已建立")
                    self.connection_restored_signal.emit()
                
                # Create node objects
                for tag_name, node_id in self.tag_list:
                    try:
                        node = self.client.get_node(node_id)
                        self.nodes[tag_name] = node
                    except Exception as e:
                        print(f"[OPC UA 錯誤] 無法取得節點 {node_id}: {e}")
                
                print(f"[OPC UA] 已加載 {len(self.nodes)} 個標籤")
                
                # Initialize last_emitted_values with None
                for tag_name in self.nodes.keys():
                    self.last_emitted_values[tag_name] = None
                
                reconnect_delay = 2  # 重置延遲
                
                # 內部輪詢循環
                poll_fail_count = 0  # 連續輪詢失敗計數
                while self.running:
                    updates = {}
                    import time
                    current_time = time.time()
                    
                    poll_success = False  # 標記本次輪詢是否成功
                    
                    for tag_name, node in self.nodes.items():
                        try:
                            server_value = await node.read_value()
                            poll_success = True  # 至少有一個標籤讀取成功
                            
                            # 決定要發出的值
                            value_to_emit = None
                            should_emit = False
                            
                            # 情況1: 有待寫入的值（緩存中）
                            if tag_name in self.current_values:
                                cached_value = self.current_values[tag_name]
                                write_time = self.write_timestamps.get(tag_name, current_time)
                                elapsed = current_time - write_time
                                
                                # 伺服器已確認寫入
                                if server_value == cached_value:
                                    print(f"[OPC UA] 寫入已確認: {tag_name} = {cached_value}")
                                    del self.current_values[tag_name]
                                    if tag_name in self.write_timestamps:
                                        del self.write_timestamps[tag_name]
                                    value_to_emit = server_value
                                    should_emit = True
                                # 寫入超時：放棄緩存，相信伺服器值
                                elif elapsed > self.write_timeout:
                                    print(f"[OPC UA] 寫入超時 {tag_name} ({elapsed:.1f}s)")
                                    del self.current_values[tag_name]
                                    if tag_name in self.write_timestamps:
                                        del self.write_timestamps[tag_name]
                                    value_to_emit = server_value
                                    should_emit = True
                                # 寫入進行中：使用緩存值，但只有在與上次發出值不同時才發出
                                else:
                                    value_to_emit = cached_value
                                    # 只有當緩存值與上次發出值不同時，才發出信號
                                    if self.last_emitted_values[tag_name] != cached_value:
                                        should_emit = True
                            # 情況2: 沒有待寫入的值，使用伺服器值
                            else:
                                value_to_emit = server_value
                                # 只有當伺服器值改變時才發出
                                if self.last_emitted_values[tag_name] != server_value:
                                    should_emit = True
                            
                            # 發出信號（如果需要）
                            if should_emit and value_to_emit is not None:
                                updates[tag_name] = value_to_emit
                                self.last_emitted_values[tag_name] = value_to_emit
                                
                        except Exception as e:
                            # 讀取失敗，記錄失敗計數
                            self.connection_fail_count += 1
                    
                    # 如果本次輪詢完全失敗（沒有任何標籤讀取成功），累計失敗次數
                    if not poll_success:
                        poll_fail_count += 1
                        # 連續失敗 3 次以上，視為連接斷線
                        if poll_fail_count >= 3 and self.is_connected:
                            print(f"[OPC UA] 連接已斷線 (輪詢失敗 {poll_fail_count} 次)")
                            self.is_connected = False
                            self.connection_lost_signal.emit()
                            # 直接 break，讓外部異常處理來完整清理和重連
                            break
                    else:
                        # 輪詢成功，重置失敗計數
                        poll_fail_count = 0
                        if self.connection_fail_count > 0:
                            self.connection_fail_count = 0
                            if not self.is_connected:
                                self.is_connected = True
                                print("[OPC UA] 連接已恢復")
                                self.connection_restored_signal.emit()
                    
                    if updates:
                        self.update_signal.emit(updates)
                    
                    await asyncio.sleep(1)  # Poll every second
                        
            except Exception as e:
                # 輪詢時斷線，記錄連接已斷開
                if self.is_connected:
                    print(f"[OPC UA] 連接已斷線: {e}")
                    self.is_connected = False
                    self.connection_lost_signal.emit()
                
                # 清理當前連接（帶超時，避免掛起）
                try:
                    await asyncio.wait_for(self.client.disconnect(), timeout=2.0)
                except asyncio.TimeoutError:
                    print("[OPC UA] 斷開連接超時")
                except:
                    pass
                
                # 如果還在運行，等待後重新連接
                if self.running:
                    print(f"[OPC UA] 將在 {reconnect_delay} 秒後重新連接...")
                    await asyncio.sleep(reconnect_delay)
                    # 增加重連延遲，每次加 1 秒，但不超過 10 秒
                    reconnect_delay = min(reconnect_delay + 1, 10)
                # 不 break，讓外部 while 迴圈自動進行下一次重連嘗試
    
    def stop(self):
        self.running = False


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
        'row2': [(201, '客房\n壓扣'), (202, '客房\n壓扣'), (203, '客房\n壓扣'), (205, '客房\n壓扣'),
                 (206, '客房\n壓扣'), (207, '客房\n壓扣')],
        '1f_public': [(1, '公共澡堂\n壓扣x4'), (2, '殘障廁所\n壓扣x2')]  # 1F公共設施，顯示在2F佈局中
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
    
    def __init__(self, room_id, room_type, img_dir, is_public=False, floor: str = None, parent=None):
        super().__init__()
        self.room_id = room_id
        self.room_type = room_type
        self.is_public = is_public
        self.floor = floor
        self.img_dir = img_dir
        self.state = 'normal'  # 保留向後兼容
        self.blink_state = False
        self.parent_dialog = parent
        
        # 新的狀態追蹤（優先級邏輯）
        self.is_disconnected = True   # 通訊斷線狀態（初始為斷線，等待連接建立）
        self.alarm_enabled = False    # 警報開關狀態
        self.alarm_status = False     # 警報狀態
        
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
        pressure = self.pressure_label.text()
        
        # 統一加入樓層和壓扣信息
        floor = getattr(self, 'floor', '')
        if self.is_public:
            title = f"{floor} {first_line} {pressure}".strip()
        else:
            title = f"{floor} {self.room_id}{first_line} {pressure}".strip()
        
        message = f"{floor} {room_num}\n{pressure}".strip() if floor else f"{room_num}\n{pressure}"
        
        # Read current values before opening popup
        initial_state = 'red' if self.state == 'alarm' else 'green'
        initial_delay = 0.0
        enable_tag = None
        reset_tag = None
        delay_tag = None
        
        if self.parent_dialog and self.parent_dialog.opcua_client:
            if self.is_public:
                # Map 1F to 2F for public facilities
                floor_num = self.floor.lower()[0]  # '1' or '2'
                if floor_num == '1':
                    floor_num = '2'
                room_key = f'public_{floor_num}f_{self.room_id}'
            else:
                room_key = self.room_id
            
            if room_key in self.parent_dialog.room_to_tags:
                tags = self.parent_dialog.room_to_tags[room_key]
                enable_tag = tags.get('enable')
                reset_tag = tags.get('reset')
                delay_tag = tags.get('delay')
                
                # Read current values synchronously before opening popup
                if enable_tag:
                    current_enable = self.parent_dialog.opcua_client.read_value(enable_tag)
                    if current_enable is not None:
                        initial_state = 'red' if bool(current_enable) else 'green'
                
                if delay_tag:
                    current_delay = self.parent_dialog.opcua_client.read_value(delay_tag)
                    if current_delay is not None:
                        initial_delay = float(current_delay)
        
        popup = PopupDialog(
            title, message, initial_state, initial_delay,
            parent_dialog=self.parent_dialog,
            enable_tag=enable_tag,
            reset_tag=reset_tag,
            delay_tag=delay_tag
        )
        if popup.exec() == QDialog.DialogCode.Accepted:
            self.set_state(popup.selected_state)
            self.clicked.emit()
            
            # Write to OPC UA
            if self.parent_dialog and self.parent_dialog.opcua_client:
                if self.is_public:
                    # Map 1F to 2F for public facilities
                    floor_num = self.floor.lower()[0]  # '1' or '2'
                    if floor_num == '1':
                        floor_num = '2'
                    room_key = f'public_{floor_num}f_{self.room_id}'
                else:
                    room_key = self.room_id
                if popup.selected_state == 'red':
                    # Enable alarm
                    if room_key in self.parent_dialog.room_to_tags and 'enable' in self.parent_dialog.room_to_tags[room_key]:
                        tag_name = self.parent_dialog.room_to_tags[room_key]['enable']
                        self.parent_dialog.opcua_client.write_value(tag_name, True)
                else:
                    # Reset alarm
                    if room_key in self.parent_dialog.room_to_tags and 'reset' in self.parent_dialog.room_to_tags[room_key]:
                        tag_name = self.parent_dialog.room_to_tags[room_key]['reset']
                        self.parent_dialog.opcua_client.write_value(tag_name, True)
                
                # Write delay
                if room_key in self.parent_dialog.room_to_tags and 'delay' in self.parent_dialog.room_to_tags[room_key]:
                    tag_name = self.parent_dialog.room_to_tags[room_key]['delay']
                    self.parent_dialog.opcua_client.write_value(tag_name, popup.alarm_delay)
    
    def set_state(self, state):
        """設置狀態（向後兼容）。"""
        self.state = state
        if state == 'alarm':
            self.blink_state = False
            self.blink_timer.start(self.blink_interval)
            self._update_light()
        else:
            self.blink_timer.stop()
            self.blink_state = False
            self._update_light()
    
    def set_disconnect(self, disconnected):
        """設置斷線狀態。"""
        self.is_disconnected = disconnected
        if disconnected:
            self.blink_timer.stop()  # 停止閃爍
        self._update_light()
    
    def set_alarm_enabled(self, enabled):
        """設置警報開關狀態。"""
        self.alarm_enabled = enabled
        self._update_light()
    
    def set_alarm_status(self, status):
        """設置警報狀態。"""
        self.alarm_status = status
        if status:
            # 警報開啟，開始閃爍
            self.blink_state = False
            self.blink_timer.start(self.blink_interval)
        else:
            # 警報關閉，停止閃爍
            self.blink_timer.stop()
            self.blink_state = False
        self._update_light()
    
    def _on_blink(self):
        self.blink_state = not self.blink_state
        self._update_light()
    
    def _update_light(self):
        """根據優先級更新燈號。
        優先級：
        1. 通訊斷線 → 灰色 (gray.png)
        2. 警報開關 = false → 藍色 (blue.png)
        3. 警報狀態 = false → 綠色 (green.png)
        4. 警報狀態 = true → 紅黃閃爍 (red/yellow.png)
        """
        # 優先級 1: 通訊斷線 → 灰色
        if self.is_disconnected:
            color = 'gray'
        # 優先級 2: 警報開關 = false → 藍色
        elif not self.alarm_enabled:
            color = 'blue'
        # 優先級 3: 警報狀態 = false → 綠色
        elif not self.alarm_status:
            color = 'green'
        # 優先級 4: 警報狀態 = true → 紅黃閃爍
        else:  # self.alarm_status == true
            color = 'red' if self.blink_state else 'yellow'
        
        # 加載 PNG
        filename = {'green': 'green.png', 'red': 'red.png', 
                   'yellow': 'yellow.png', 'gray': 'gray.png', 'blue': 'blue.png'}[color]
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
        
        if hasattr(sys, '_MEIPASS'):
            meipass = Path(sys._MEIPASS)
            if '_internal' in str(meipass):
                self.workspace_root = meipass.parent
                self.img_dir = os.path.join(self.workspace_root, '_internal', 'img')
            else:
                self.workspace_root = meipass
                self.img_dir = os.path.join(self.workspace_root, 'img')
        else:
            self.workspace_root = Path(__file__).parent.parent
            self.img_dir = os.path.join(self.workspace_root, 'img')
        self.room_cards = {}  # room_id -> RoomCard
        self._resizing = False  # 防止 resizeEvent 無限循環
        self._last_card_size = 0  # 記錄上次卡片大小
        
        # 警報狀態追踪
        self.has_alarm = False  # 當前是否有任何警報（壓扣或通訊斷線）
        self.is_disconnected = False  # 通訊斷線狀態
        self.alarm_window_raised = False  # 用於防止重複置頂窗口
        
        # 音訊播放器
        self.media_player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.media_player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(100)
        
        # 音訊檔案路徑
        alarm_sound_path = os.path.join(self.img_dir, 'Windows Error.wav')
        if os.path.exists(alarm_sound_path):
            self.alarm_sound_url = QUrl.fromLocalFile(alarm_sound_path)
        else:
            self.alarm_sound_url = None
            print(f"[音訊] 警報音檔不存在: {alarm_sound_path}")
        
        # Load OPC UA tags
        self.opcua_tags = self._load_opcua_tags()
        self.tag_to_room = self._build_tag_mapping()
        
        # OPC UA Client
        self.opcua_client = None
        self.opcua_nodes = {}  # tag_name -> node
        self.latest_values = {}  # tag_name -> latest value
        
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
        
        # 頂部工具欄（靜音按鈕）
        top_bar = QWidget()
        top_bar_layout = QHBoxLayout(top_bar)
        top_bar_layout.setContentsMargins(10, 5, 10, 5)
        top_bar_layout.addStretch()  # 左側彈性空間，讓按鈕靠右
        
        # 靜音按鈕（臨時靜音，按下時停止當前警報音，新警報時自動恢復）
        self.mute_button = QPushButton('🔊')
        self.mute_button.setFont(QFont(self.font().family(), 28))  # 放大3倍
        self.mute_button.setMaximumWidth(70)
        self.mute_button.setMaximumHeight(70)
        self.mute_button.clicked.connect(self._on_mute_clicked)
        self.is_muted = False  # 當前靜音狀態（只在警報播放時有效）
        self.mute_by_user = False  # 記錄用戶是否按過靜音按鈕
        top_bar_layout.addWidget(self.mute_button)
        
        main_layout.addWidget(top_bar)
        
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
        
        # 初始化系統列托盤
        self._setup_system_tray()
        
        # Start OPC UA client after UI is built
        QTimer.singleShot(1000, self._start_opcua_client)
    
    def _load_opcua_tags(self):
        """Load OPC UA tags from CSV file."""
        tags = []
        if hasattr(sys, '_MEIPASS'):
            csv_path = Path(sys._MEIPASS) / 'opc_tags.csv' / 'OPC UA tag.csv'
        else:
            csv_path = self.workspace_root / 'OPC UA tag.csv'
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:  # utf-8-sig handles BOM
                reader = csv.DictReader(f)
                for row in reader:
                    tag_name = row['TagName']
                    node_id = row['OPC UA NodeId']
                    tags.append((tag_name, node_id))
        except Exception as e:
            print(f"Failed to load OPC UA tags: {e}")
        return tags
    
    def _build_tag_mapping(self):
        """Build mapping from tag names to room identifiers."""
        self.tag_to_room = {}
        self.room_to_tags = {}  # room_key -> {type: tag_name}
        for tag_name, node_id in self.opcua_tags:
            room_key = self._parse_tag_to_room_key(tag_name)
            if room_key:
                self.tag_to_room[tag_name] = room_key
                if room_key not in self.room_to_tags:
                    self.room_to_tags[room_key] = {}
                
                if '警報開關' in tag_name:
                    self.room_to_tags[room_key]['enable'] = tag_name
                elif '警報復歸' in tag_name:
                    self.room_to_tags[room_key]['reset'] = tag_name
                elif '警報延遲' in tag_name:
                    self.room_to_tags[room_key]['delay'] = tag_name
        return self.tag_to_room
    
    def _parse_tag_to_room_key(self, tag_name):
        """Parse tag name to room key for room_cards dict."""
        # Example: "2F 201客房 壓扣 警報狀態" -> 201
        # Example: "1F 公共澡堂 壓扣x4 警報狀態" -> 'public_2f_1' (map 1F to 2F)
        parts = tag_name.split()
        if len(parts) < 4:
            return None
        
        floor = parts[0]  # e.g., '2F' or '1F'
        floor_num = floor[0]  # '2' or '1'
        
        if '客房' in tag_name:
            # Guest room: "2F 201客房 壓扣 警報狀態"
            room_num_str = parts[1][:3]  # '201'
            try:
                room_num = int(room_num_str)
                return room_num
            except:
                return None
        elif '公共澡堂' in tag_name or '殘障廁所' in tag_name:
            # Public: "1F 公共澡堂 壓扣x4 警報狀態" -> map to 2F or 5F
            if '公共澡堂' in tag_name:
                public_index = 1
            else:  # 殘障廁所
                public_index = 2
            
            # Map 1F to 2F, since 2F has similar public facilities
            if floor_num == '1':
                floor_num = '2'
            return f'public_{floor_num}f_{public_index}'
        return None
    
    @pyqtSlot(dict)
    def _on_opcua_update(self, updates):
        """Handle OPC UA value updates."""
        # Store latest values
        self.latest_values.update(updates)
        
        # Group updates by room
        room_updates = {}
        for tag_name, value in updates.items():
            if tag_name in self.tag_to_room:
                room_key = self.tag_to_room[tag_name]
                if room_key not in room_updates:
                    room_updates[room_key] = {}
                
                # 根據標籤類型分類更新
                if '警報狀態' in tag_name:
                    room_updates[room_key]['alarm_status'] = bool(value)
                elif '警報開關' in tag_name:
                    room_updates[room_key]['alarm_enabled'] = bool(value)
                elif '警報復歸' in tag_name:
                    room_updates[room_key]['reset'] = bool(value)
                elif '警報延遲' in tag_name:
                    room_updates[room_key]['delay'] = float(value)
        
        # 應用更新到各個卡片
        for room_key, updates_dict in room_updates.items():
            if room_key in self.room_cards:
                card = self.room_cards[room_key]
                
                if 'alarm_status' in updates_dict:
                    card.set_alarm_status(updates_dict['alarm_status'])
                if 'alarm_enabled' in updates_dict:
                    card.set_alarm_enabled(updates_dict['alarm_enabled'])
                if 'reset' in updates_dict and updates_dict['reset']:
                    # 按下 reset 時停止音訊
                    self._stop_alarm_sound()
        
        # 檢查是否有任何房間的警報狀態為 true
        has_room_alarm = any(
            card.alarm_status for card in self.room_cards.values()
        )
        
        # 如果警報狀態改變，更新音訊播放和窗口
        self._update_alarm_state(has_room_alarm)
    
    def _update_alarm_state(self, has_room_alarm):
        """Update alarm state and control audio/window."""
        new_alarm_state = has_room_alarm or self.is_disconnected
        
        # 警報狀態從 false → true，開始播放音訊並置頂窗口
        if new_alarm_state and not self.has_alarm:
            self.has_alarm = True
            # 新警報進來時，重置靜音狀態（用戶之前按的靜音只對該警報有效）
            self.is_muted = False
            self.mute_by_user = False
            self._play_alarm_sound()
            self._raise_and_maximize_window()
        
        # 警報狀態從 true → false，停止播放音訊
        elif not new_alarm_state and self.has_alarm:
            self.has_alarm = False
            self._stop_alarm_sound()
            self.alarm_window_raised = False
    
    def _play_alarm_sound(self):
        """Play alarm sound on loop."""
        # 如果処於靜音模式，不播放
        if self.is_muted:
            return
        
        if self.alarm_sound_url is None:
            return
        
        print("[音訊] 開始播放警報聲...")
        self.media_player.setSource(self.alarm_sound_url)
        # 設置無限循環（使用很大的次數）
        self.media_player.setLoops(-1)  # -1 表示無限循環
        self.media_player.play()
    
    def _stop_alarm_sound(self):
        """Stop alarm sound."""
        if self.media_player.isPlaying():
            print("[音訊] 停止警報聲...")
            self.media_player.stop()
    
    def _on_mute_clicked(self):
        """Handle mute button click - temporarily mute current alarm."""
        # 按下靜音按鈕，停止當前警報的播放
        self._stop_alarm_sound()
        self.is_muted = True
        self.mute_by_user = True  # 記錄用戶按過靜音
        print("[UI] 用戶按下靜音按鈕 - 當前警報靜音")
    
    def _raise_and_maximize_window(self):
        """Raise window to top and maximize (once per alarm)."""
        if self.alarm_window_raised:
            return
        
        print("[UI] 警報視窗置頂並最大化...")
        self.alarm_window_raised = True
        
        # 使用 QTimer 延遲執行，避免阻擋其他事件
        def raise_window():
            # 暫時設置為最上層
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.show()
            self.raise_()
            self.activateWindow()
            self.showMaximized()
            
            # 500ms 後移除最上層標誌，允許其他對話框出現在上方
            QTimer.singleShot(500, self._remove_stay_on_top)
        
        QTimer.singleShot(0, raise_window)
    
    def _remove_stay_on_top(self):
        """Remove WindowStaysOnTopHint after alarm window is raised."""
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()
        print("[UI] 已移除最上層鎖定，允許其他對話框出現")
    
    @pyqtSlot()
    def _on_connection_lost(self):
        """Handle OPC UA connection loss."""
        print("[UI] OPC UA 連接已斷線，燈號變灰色")
        self.is_disconnected = True
        for card in self.room_cards.values():
            card.set_disconnect(True)
        
        # 檢查是否有房間警報，有的話繼續播放，沒有才開始播放通訊斷線警報聲
        has_room_alarm = any(
            card.alarm_status for card in self.room_cards.values()
        )
        self._update_alarm_state(has_room_alarm or self.is_disconnected)
    
    @pyqtSlot()
    def _on_connection_restored(self):
        """Handle OPC UA connection restoration."""
        print("[UI] OPC UA 連接已恢復，燈號恢復正常")
        self.is_disconnected = False
        for card in self.room_cards.values():
            card.set_disconnect(False)
        
        # 檢查是否還有房間警報
        has_room_alarm = any(
            card.alarm_status for card in self.room_cards.values()
        )
        self._update_alarm_state(has_room_alarm)
    
    def get_latest_value(self, tag_name):
        """Get the latest value for a tag."""
        return self.latest_values.get(tag_name)
    
    def _start_opcua_client(self):
        """Start the OPC UA client thread."""
        server_url = "opc.tcp://172.27.119.6:49320"
        if self.opcua_tags:
            self.opcua_client = OPCUAClient(server_url, self.opcua_tags)
            self.opcua_client.update_signal.connect(self._on_opcua_update)
            self.opcua_client.connection_lost_signal.connect(self._on_connection_lost)
            self.opcua_client.connection_restored_signal.connect(self._on_connection_restored)
            self.opcua_client.start()
    
    def _get_opcua_nodes(self):
        """Get nodes from OPC UA client."""
        if self.opcua_client:
            self.opcua_nodes = self.opcua_client.nodes
            # Wait a bit for nodes to be loaded
            QTimer.singleShot(1000, self._get_opcua_nodes)
    
    def closeEvent(self, event):
        """Handle window close event - ensure complete shutdown."""
        print("[UI] 關閉視窗，清理資源...")
        
        # 停止 OPC UA 客戶端線程
        if self.opcua_client:
            self.opcua_client.stop()
            # 等待線程完全停止（最多等 5 秒）
            if not self.opcua_client.wait(5000):
                print("[UI 警告] OPC UA 線程未能在 5 秒內停止，強制終止...")
                self.opcua_client.terminate()
                self.opcua_client.wait()
        
        # 接受關閉事件
        event.accept()
        print("[UI] 視窗已關閉，應用即將退出")
        
        # 強制退出應用（確保沒有其他線程在背景執行）
        import sys
        import os
        sys.exit(0)
    
    def _setup_system_tray(self):
        """設置系統列托盤圖標和功能。"""
        try:
            # 創建系統列托盤圖標
            self.tray_icon = QSystemTrayIcon(self)
            
            # 設置托盤圖標（使用應用圖標）
            icon_path = os.path.join(self.img_dir, '享溫泉.ico')
            if os.path.exists(icon_path):
                self.tray_icon.setIcon(QIcon(icon_path))
            
            # 創建托盤菜單
            tray_menu = QMenu(self)
            
            # 添加「顯示」菜單項
            show_action = tray_menu.addAction("顯示視窗")
            show_action.triggered.connect(self._show_from_tray)
            
            # 添加分隔線
            tray_menu.addSeparator()
            
            # 添加「退出」菜單項
            exit_action = tray_menu.addAction("結束應用")
            exit_action.triggered.connect(self.close)
            
            # 設置菜單
            self.tray_icon.setContextMenu(tray_menu)
            
            # 點擊托盤圖標時顯示或隱藏視窗
            self.tray_icon.activated.connect(self._on_tray_icon_activated)
            
            # 顯示托盤圖標
            self.tray_icon.show()
            print("[UI] 系統列托盤已初始化")
        except Exception as e:
            print(f"[UI 警告] 無法初始化系統列托盤: {e}")
    
    def _on_tray_icon_activated(self, reason):
        """處理托盤圖標點擊事件。"""
        from PyQt6.QtWidgets import QSystemTrayIcon as QSTIcon
        # 只在雙擊或點擊時顯示視窗
        if reason in (QSTIcon.ActivationReason.DoubleClick, QSTIcon.ActivationReason.Trigger):
            self._show_from_tray()
    
    def _show_from_tray(self):
        """從系統列恢復視窗。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        
        # 恢復後強制重新計算佈局
        QTimer.singleShot(50, self._do_scale)
        print("[UI] 視窗已從系統列恢復")
    
    def changeEvent(self, event):
        """處理視窗狀態變化事件：最小化時隱藏到系統列，處理還原按鈕和主題變化。"""
        # Window state change
        if hasattr(QEvent.Type, 'WindowStateChange') and event.type() == QEvent.Type.WindowStateChange:
            # 檢查是否最小化 → 隱藏到系統列
            if self.windowState() & Qt.WindowState.WindowMinimized:
                self.hide()
                self.tray_icon.showMessage("北投享溫泉", "應用已最小化到系統列", QSystemTrayIcon.MessageIcon.Information, 2000)
                event.ignore()
                return
            
            # 檢查是否從最大化變為正常狀態 → 調整視窗大小
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
    
    def closeEvent(self, event):
        """Handle window close event - ensure complete shutdown."""
        print("[UI] 關閉視窗，清理資源...")
        
        # 停止 OPC UA 客戶端線程
        if self.opcua_client:
            self.opcua_client.stop()
            # 等待線程完全停止（最多等 5 秒）
            if not self.opcua_client.wait(5000):
                print("[UI 警告] OPC UA 線程未能在 5 秒內停止，強制終止...")
                self.opcua_client.terminate()
                self.opcua_client.wait()
        
        # 接受關閉事件
        event.accept()
        print("[UI] 視窗已關閉，應用即將退出")
        
        # 強制退出應用（確保沒有其他線程在背景執行）
        import sys
        import os
        sys.exit(0)
    
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
                card = RoomCard(room_id, room_type, self.img_dir, floor='5F', parent=self)
                row1_5f.addWidget(card)
                self.room_cards[room_id] = card
        row1_5f.addStretch(1)
        rooms_5f.addLayout(row1_5f)

        # 5F 第2行：6張卡片 + 公共設施
        row2_5f = QHBoxLayout()
        row2_5f.setContentsMargins(0, 0, 0, 0)
        row2_5f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['5F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir, floor='5F', parent=self)
            row2_5f.addWidget(card)
            self.room_cards[room_id] = card

        # 間隙
        spacer = QWidget()
        spacer.setFixedWidth(100)
        spacer.setObjectName('spacer_5f_public')
        row2_5f.addWidget(spacer)

        # 公共設施
        for room_id, room_type in ROOMS_DATA['5F']['public']:
            card = RoomCard(room_id, room_type, self.img_dir, is_public=True, floor='5F', parent=self)
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
                card = RoomCard(room_id, room_type, self.img_dir, floor='3F', parent=self)
                row1_3f.addWidget(card)
                self.room_cards[room_id] = card
        row1_3f.addStretch(1)
        rooms_3f.addLayout(row1_3f)

        # 3F 第2行：6張卡片
        row2_3f = QHBoxLayout()
        row2_3f.setContentsMargins(0, 0, 0, 0)
        row2_3f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['3F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir, floor='3F', parent=self)
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
            card = RoomCard(room_id, room_type, self.img_dir, floor='2F', parent=self)
            row1_2f.addWidget(card)
            self.room_cards[room_id] = card
        row1_2f.addStretch(1)
        rooms_2f.addLayout(row1_2f)

        # 2F 第2行：6張卡片（左側客房）+ 公共設施
        row2_2f = QHBoxLayout()
        row2_2f.setContentsMargins(0, 0, 0, 0)
        row2_2f.setSpacing(3)
        for room_id, room_type in ROOMS_DATA['2F']['row2']:
            card = RoomCard(room_id, room_type, self.img_dir, floor='2F', parent=self)
            row2_2f.addWidget(card)
            self.room_cards[room_id] = card

        # 間隙
        spacer = QWidget()
        spacer.setFixedWidth(100)
        spacer.setObjectName('spacer_2f_public')
        row2_2f.addWidget(spacer)

        # 1F 公共設施（顯示在2F區域右下角，UI佈局設計如此）
        for room_id, room_type in ROOMS_DATA['2F']['1f_public']:
            # 注意：floor設為'1F'，佈局在2F區域，room_key為public_2f_X
            card = RoomCard(room_id, room_type, self.img_dir, is_public=True, floor='1F', parent=self)
            row2_2f.addWidget(card)
            self.room_cards[f'public_2f_{room_id}'] = card

        # 1F 樓層標籤（顯示在公共設施旁）
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
        # 延遲執行以確保視窗/佈局完成後再縮放（處理 Snap/分割情況）
        QTimer.singleShot(50, self._do_scale)
        QTimer.singleShot(300, self._do_scale)
    
    def resizeEvent(self, event):
        """視窗大小改變時重新縮放卡片。"""
        super().resizeEvent(event)
        
        # 防止無限循環
        if self._resizing:
            return
        # 延遲執行縮放，確保 layout 已更新（解決 Windows Snap 時未正確應用佈局問題）
        QTimer.singleShot(50, self._do_scale)
        QTimer.singleShot(200, self._do_scale)

    def showEvent(self, event):
        """視窗顯示時觸發一次縮放，處理 Snap/分割後的初始排列。"""
        super().showEvent(event)
        QTimer.singleShot(50, self._do_scale)
        QTimer.singleShot(300, self._do_scale)
    
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
        MAX_CARDS_H = 6    # 水平最大卡片數（改為6以恢復原始卡片大小）
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
    

