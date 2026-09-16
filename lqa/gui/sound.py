"""完成音效。

用 Windows 內建的 MCI（winmm）播放，不另外裝套件：
PySide6 這邊裝的是 Essentials，沒有 QtMultimedia；而 winsound 只吃 WAV，
播不了 mp3。MCI 是系統本來就有的介面，mp3 直接送進去就能播。

播放是非同步的 —— 解析剛結束正是使用者要看結果的時候，
不能為了播一秒的音效把介面卡住。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

from ..logging_setup import get

log = get("gui.sound")

SOUND_DIR = Path(__file__).parent / "assets" / "sounds"
NONE = ""                      # 設定裡的「不播放」
DEFAULT = "啵_Bop"

_IS_WINDOWS = sys.platform == "win32"
_ALIAS = "lqa_finish"


def available() -> list[str]:
    """可以選的音效名稱。就是檔名去掉副檔名。"""
    if not SOUND_DIR.is_dir():
        return []
    return sorted(p.stem for p in SOUND_DIR.glob("*.mp3"))


def path_of(name: str) -> Path | None:
    if not name:
        return None
    candidate = SOUND_DIR / f"{name}.mp3"
    return candidate if candidate.exists() else None


def play(name: str) -> bool:
    """播一次。回傳有沒有真的播出去。

    失敗只記錄不丟例外：沒有音效裝置、檔案壞掉、或是解碼器不在，
    都不該讓「解析完成」這件事看起來像是出錯了。
    """
    path = path_of(name)
    if path is None or not _IS_WINDOWS:
        return False
    try:
        winmm = ctypes.windll.winmm
        winmm.mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR,
                                         ctypes.c_uint, wintypes.HANDLE]
        # 前一次可能還開著（連續解析兩個頁簽），先收掉再開新的
        winmm.mciSendStringW(f"close {_ALIAS}", None, 0, None)
        if winmm.mciSendStringW(
                f'open "{path}" type mpegvideo alias {_ALIAS}', None, 0, None):
            log.warning("音效開不起來：%s", path.name)
            return False
        # 不要用 play wait，那會卡住呼叫端
        if winmm.mciSendStringW(f"play {_ALIAS}", None, 0, None):
            log.warning("音效播不出來：%s", path.name)
            return False
        return True
    except OSError as exc:
        log.warning("音效失敗：%s", exc)
        return False
