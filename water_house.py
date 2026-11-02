import sys
import os
import argparse
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import QTimer, QLockFile
from runtime_support import (
    APP_VERSION, get_data_dir, get_session_id, setup_runtime_logging, write_heartbeat,
)
# ensure workspace root is in sys.path so UI package can be imported when running script
root = os.path.dirname(os.path.abspath(__file__))
if root not in sys.path:
    sys.path.insert(0, root)

# import dialogs directly from file location to avoid package import issues
import importlib.util
def load_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _setup_runtime_logging():
    return setup_runtime_logging()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WaterHouse SCADA")
    parser.add_argument("--self-test-output", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.self_test_output:
        # The packaged smoke test must never connect to the plant or touch its state.
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from application_self_test import run_self_test
        sys.exit(run_self_test(options.self_test_output))

    log_dir = _setup_runtime_logging()
    # Initialize diagnostics before UI/OPC imports so startup faults are retained.
    from ui.scada_dialog import ScadaDialog

    app = QApplication(sys.argv)
    app.setApplicationName("WaterHouse")
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    instance_lock = QLockFile(str(get_data_dir() / f"session-{get_session_id()}.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        import logging
        logging.warning("同一桌面工作階段已執行 WaterHouse，略過重複啟動")
        sys.exit(0)
    # Detect Windows light/dark preference when possible
    is_light = None
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as reg:
                val, _ = winreg.QueryValueEx(reg, "AppsUseLightTheme")
                is_light = bool(val)
        except Exception:
            is_light = None
    except Exception:
        is_light = None

    # Fallback: use current palette brightness
    if is_light is None:
        pal_check = app.palette().color(QPalette.ColorRole.Window)
        lum = 0.2126 * pal_check.red() + 0.7152 * pal_check.green() + 0.0722 * pal_check.blue()
        is_light = lum > 128

    # Define color schemes for light/dark (softened: not pure white or pure black)
    if is_light:
        bg = QColor('#F3F6F7')    # very light gray-blue (so it's not pure white)
        text = QColor('#263238')  # dark text for readability
        button_bg = QColor('#F8FAFB')
    else:
        bg = QColor('#1F2426')    # soft near-black (not absolute black)
        text = QColor('#E6F2F1')  # light text for readability on dark bg
        button_bg = QColor('#2A2E30')

    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, bg)
    pal.setColor(QPalette.ColorRole.AlternateBase, bg.darker(110))
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, button_bg)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    app.setPalette(pal)

    # Load stylesheet if available (stylesheet can reference palette colors)
    try:
        with open(r"UI/style.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass

    window = ScadaDialog()
    window.show()
    write_heartbeat(log_dir, window)
    heartbeat_timer = QTimer()
    heartbeat_timer.timeout.connect(
        lambda: write_heartbeat(log_dir, window)
    )
    heartbeat_timer.start(5000)
    sys.exit(app.exec())
