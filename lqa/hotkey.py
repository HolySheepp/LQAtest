"""全域熱鍵偵測。

用 GetAsyncKeyState 輪詢，不掛全域鉤子也不需要額外套件。
輪詢按鍵狀態只要幾微秒，和擷取畫面完全不是同一個量級 ——
這正是手動截圖模式不會讓模擬器變卡的原因。

全域的意思是：焦點在模擬器上時照樣收得到，不必切回終端機。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Iterable

_IS_WINDOWS = sys.platform == "win32"
_user32 = ctypes.WinDLL("user32", use_last_error=True) if _IS_WINDOWS else None

# 只收錄不會和遊戲操作衝突的按鍵
VK_CODES: dict[str, int] = {
    **{f"f{n}": 0x70 + n - 1 for n in range(1, 13)},
    "space": 0x20,
    "enter": 0x0D,
    "esc": 0x1B,
    "backspace": 0x08,
    "tab": 0x09,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "mouse_middle": 0x04,
    "mouse_x1": 0x05,
    "mouse_x2": 0x06,
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},
    **{str(d): 0x30 + d for d in range(10)},
}


def resolve(name: str) -> int:
    key = name.strip().lower()
    if key not in VK_CODES:
        raise ValueError(
            f"不認得的按鍵『{name}』。可用：f1~f12、space、enter、esc、"
            "方向鍵、單一英數字、mouse_middle、mouse_x1、mouse_x2"
        )
    return VK_CODES[key]


class KeyWatcher:
    """偵測按鍵「剛被按下」的那一刻，不是「按著」。

    GetAsyncKeyState 的最高位代表目前是否按著，所以自己記住上一次的狀態，
    取上升緣。否則按著不放會被當成連續觸發。
    """

    def __init__(self, keys: Iterable[str]):
        self._codes = {name: resolve(name) for name in keys}
        self._down = {name: False for name in self._codes}

    @staticmethod
    def _is_down(code: int) -> bool:
        if not _IS_WINDOWS:
            return False
        return bool(_user32.GetAsyncKeyState(code) & 0x8000)

    def pressed(self) -> list[str]:
        """回傳這一輪剛被按下的按鍵名稱。"""
        fired: list[str] = []
        for name, code in self._codes.items():
            now = self._is_down(code)
            if now and not self._down[name]:
                fired.append(name)
            self._down[name] = now
        return fired
