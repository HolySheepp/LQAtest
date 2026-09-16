"""打包成可以直接交給同事的資料夾。

產出 dist/LQA Checker/，裡面有執行檔、設定範例和使用說明。
同一份東西壓成 zip 就是 zip 版，解壓即用；要做安裝版再用 Inno Setup
套一層即可（安裝版本質上就是把這個資料夾複製到 Program Files 再建捷徑）。

    .venv/Scripts/python.exe tools/build_app.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "LQA Checker"

README = """LQA Checker

直接執行「LQA Checker.exe」，不必安裝 Python 或任何其他東西。

第一次使用：
  1. 開啟雷電模擬器並進到遊戲對白畫面
  2. 開軟體 -> 設定 -> 進階 -> 開啟調試視窗，確認取字範圍對得上
     （範圍是照解析度定的，如果你的模擬器視窗大小不同就要重框）
  3. 設定裡指定翻譯文本與發話者對照表
  4. 選一個頁簽 -> 開始錄製 -> 在遊戲裡每看完一句按 F9 -> F12 結束
  5. 按「開始解析」

所有資料都放在這個資料夾裡：
  config/     設定與校準參數
  projects/   截圖與解析結果
  logs/       執行記錄，出問題時把這裡的檔案附上

整個資料夾複製到別台機器就能接著用。
"""


def run(*args: str) -> None:
    print(">", " ".join(args))
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    for folder in (ROOT / "build", ROOT / "dist"):
        if folder.exists():
            shutil.rmtree(folder)

    run(sys.executable, "-m", "PyInstaller", "--noconfirm",
        str(ROOT / "packaging" / "lqa.spec"))

    if not DIST.exists():
        print("打包失敗：找不到輸出資料夾")
        return 1

    # 設定：帶著校準參數出貨，同事拿到就能用；使用者資料不放
    config = DIST / "config"
    config.mkdir(exist_ok=True)
    profile = ROOT / "config" / "profile.json"
    source = profile if profile.exists() else ROOT / "config" / "profile.example.json"
    shutil.copy(source, config / "profile.json")
    for example in (ROOT / "config").glob("*.example.*"):
        shutil.copy(example, config / example.name)
    (DIST / "使用說明.txt").write_text(README, encoding="utf-8")
    # 自我檢查的輸出是跑出來的東西，不該跟著出貨
    (DIST / "self-test.txt").unlink(missing_ok=True)

    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\n資料夾版：{DIST}（{size / 1e6:.0f} MB）")

    sys.path.insert(0, str(ROOT))
    from lqa import __version__

    archive = ROOT / "dist" / f"LQA-Checker-{__version__}-portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(DIST.rglob("*")):
            if path.is_file():
                zf.write(path, Path("LQA Checker") / path.relative_to(DIST))
    print(f"zip 版：{archive}（{archive.stat().st_size / 1e6:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
