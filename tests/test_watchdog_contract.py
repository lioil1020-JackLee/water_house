"""Run the actual AHK policy/parser self-tests, without starting a watchdog."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def find_ahk():
    configured = os.environ.get("AUTOHOTKEY_V2")
    candidates = [configured] if configured else []
    candidates += [shutil.which("AutoHotkey64.exe"), shutil.which("AutoHotkey.exe")]
    candidates += [
        str(Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "AutoHotkey/v2/AutoHotkey64.exe"),
    ]
    return next((p for p in candidates if p and Path(p).is_file() and Path(p).stat().st_size), None)


@unittest.skipUnless(os.name == "nt" and find_ahk(), "AutoHotkey v2 Windows runtime required")
class WatchdogContractTests(unittest.TestCase):
    def test_native_policy_and_parser_without_process_actions(self):
        with tempfile.TemporaryDirectory(prefix="water-house-watchdog-test-") as tmp:
            result_path = Path(tmp) / "result.json"
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            run = subprocess.run(
                [find_ahk(), "/ErrorStdOut=UTF-8", str(ROOT / "water_house_watchdog.ahk"),
                 "--validate-only", str(result_path)],
                cwd=ROOT, capture_output=True, timeout=20, startupinfo=startup,
            )
            self.assertEqual(run.returncode, 0, (run.stdout + run.stderr).decode("utf-8", "replace"))
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            self.assertTrue(result["passed"])
            self.assertGreaterEqual(result["test_count"], 21)
            self.assertEqual(result["process_actions"], 0)


if __name__ == "__main__":
    unittest.main()
