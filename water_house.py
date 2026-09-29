import sys
import os
import tempfile
import time
import faulthandler
import logging
import threading
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import QTimer
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
    """Create writable diagnostics for windowed/onefile deployments."""
    log_dir = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "WaterHouse" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_dir / "water_house.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        encoding="utf-8",
    )

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("未捕捉例外", exc_info=(exc_type, exc_value, exc_traceback))

    def handle_thread_exception(args):
        logging.critical(
            "執行緒未捕捉例外 (%s)",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception
    fault_file = open(log_dir / "water_house_fault.log", "a", encoding="utf-8")
    faulthandler.enable(fault_file)
    logging.info("water_house 啟動，PID=%s", os.getpid())
    return log_dir

scada_mod = load_from_path('scada_dialog', os.path.join(root, 'ui', 'scada_dialog.py'))
popup_mod = load_from_path('popup_dialog', os.path.join(root, 'ui', 'popup_dialog.py'))

ScadaDialog = scada_mod.ScadaDialog
PopupDialog = popup_mod.PopupDialog 

if __name__ == "__main__":
    log_dir = _setup_runtime_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
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
    heartbeat_path = log_dir / "water_house.heartbeat"
    heartbeat_path.write_text(str(time.time()), encoding="ascii")
    heartbeat_timer = QTimer()
    heartbeat_timer.timeout.connect(
        lambda: heartbeat_path.write_text(str(time.time()), encoding="ascii")
    )
    heartbeat_timer.start(5000)
    sys.exit(app.exec())
