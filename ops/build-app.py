"""Build with deterministic Windows DLL discovery, excluding host toolchains.

Qt uses Windows' ICU exports. An unrelated Poppler/Conda ICU from the caller's
PATH can be collected by PyInstaller and cause QtWidgets import failures.
"""

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build_type", choices=("onefile", "onedir"))
    parser.add_argument("--distpath", default="dist")
    parser.add_argument("--workpath", default="build")
    options = parser.parse_args()
    if sys.platform == "win32":
        windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
        os.environ["PATH"] = os.pathsep.join(str(path) for path in (
            Path(sys.executable).parent,
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        ))
    from PyInstaller.__main__ import run
    run([
        "--noconfirm", "--distpath", options.distpath, "--workpath", options.workpath,
        f"water_house_{options.build_type}.spec",
    ])


if __name__ == "__main__":
    main()
