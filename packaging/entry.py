"""打包後的進入點：直接開介面。

不用 lqa/__main__.py，那個是命令列的入口，點兩下執行檔會看到用法說明。

帶 --self-test 參數的話不開介面，改成檢查這份打包是不是完整的：
辨識模型在不在、引擎載不載得起來、設定要寫到哪裡。打包過的程式沒有
主控台視窗，所以結果寫進檔案。同事那邊跑不起來時，這是最快的下手處。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def self_test() -> int:
    from lqa import paths

    report = paths.base_dir() / "self-test.txt"
    lines: list[str] = []

    def say(text: str) -> None:
        lines.append(text)

    say(f"Python {sys.version.split()[0]}　打包狀態 {paths.is_frozen()}")
    say(f"資源目錄 {paths.resource_dir()}")
    say(f"設定目錄 {paths.base_dir() / 'config'}")

    ok = True
    try:
        from PySide6 import QtCore

        say(f"Qt {QtCore.qVersion()}")
    except Exception as exc:
        ok = False
        say(f"Qt 載入失敗：{exc}")

    try:
        import rapidocr

        models = list(Path(rapidocr.__file__).parent.rglob("*.onnx"))
        say(f"辨識模型 {len(models)} 個："
            + "、".join(f"{m.name} {m.stat().st_size // 1024 // 1024}MB"
                        for m in models))
        if not models:
            ok = False
            say("找不到模型檔，辨識一定會失敗")
    except Exception as exc:
        ok = False
        say(f"rapidocr 載入失敗：{exc}")

    try:
        import numpy as np

        from lqa.config import Profile
        from lqa.ocr.base import engine_for
        from lqa.paths import config_path

        profile_path = config_path("profile.json")
        profile = (Profile.load(profile_path) if profile_path.exists()
                   else Profile(window_title="測試", body_roi=(0, 0, 40, 20)))
        start = time.perf_counter()
        engine = engine_for(profile)
        say(f"辨識引擎載入完成（{time.perf_counter() - start:.1f} 秒）")
        blank = np.full((40, 160), 255, np.uint8)
        start = time.perf_counter()
        engine.read(blank)
        say(f"試跑一次辨識：成功（{time.perf_counter() - start:.1f} 秒）")
    except Exception as exc:
        ok = False
        say(f"辨識引擎有問題：{type(exc).__name__}: {exc}")

    say("")
    say("結果：一切正常" if ok else "結果：有問題，請把這個檔案回傳")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    from lqa.gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
