import sys
import os
from PyQt5.QtWidgets import QApplication
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

scada_mod = load_from_path('scada_dialog', os.path.join(root, 'ui', 'scada_dialog.py'))
popup_mod = load_from_path('popup_dialog', os.path.join(root, 'ui', 'popup_dialog.py'))

ScadaDialog = scada_mod.ScadaDialog
PopupDialog = popup_mod.PopupDialog

if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Load stylesheet if available
    try:
        with open(r"UI/style.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception:
        pass

    window = ScadaDialog()
    window.show()
    sys.exit(app.exec_())