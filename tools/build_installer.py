"""把打包好的資料夾做成安裝精靈。

要先跑過 tools/build_app.py。安裝檔會出現在 dist/LQA-Checker-setup.exe。

    .venv/Scripts/python.exe tools/build_installer.py

需要 Inno Setup（免費）。沒裝的話這裡會講怎麼裝。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "dist" / "LQA Checker"
SCRIPT = ROOT / "packaging" / "installer.iss"

import os

# winget 裝的會在使用者目錄底下，不是 Program Files
CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_compiler() -> Path | None:
    for path in CANDIDATES:
        if path.exists():
            return path
    return None


def main() -> int:
    if not PAYLOAD.exists():
        print("找不到 dist/LQA Checker/，請先跑 tools/build_app.py")
        return 1
    compiler = find_compiler()
    if compiler is None:
        print("找不到 Inno Setup。安裝方式：")
        print("  winget install --id JRSoftware.InnoSetup")
        return 1

    sys.path.insert(0, str(ROOT))
    from lqa import __version__

    print(f"> {compiler} /DAppVersion={__version__} {SCRIPT}")
    result = subprocess.run(
        [str(compiler), f"/DAppVersion={__version__}", str(SCRIPT)], cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    setup = ROOT / "dist" / f"LQA-Checker-{__version__}-setup.exe"
    if setup.exists():
        print(f"\n安裝檔：{setup}（{setup.stat().st_size / 1e6:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
