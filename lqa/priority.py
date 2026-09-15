"""解析期間降低行程優先權。

離線解析通常和遊玩同時進行，使用者回報「按開始解析之後模擬器就卡了」。
OCR 本來就要吃掉好幾顆核心，讓出排程給模擬器比早幾秒跑完重要 ——
CPU 閒著時低優先權幾乎不影響速度，只有在搶資源時才會退讓。
"""

from __future__ import annotations

import ctypes
import sys
from contextlib import contextmanager
from typing import Iterator

_IS_WINDOWS = sys.platform == "win32"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NORMAL_PRIORITY_CLASS = 0x00000020


def _set(priority: int) -> bool:
    if not _IS_WINDOWS:
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), priority))


@contextmanager
def low_priority(enabled: bool = True) -> Iterator[None]:
    """在區塊內把行程降到低優先權，離開時還原。"""
    changed = _set(BELOW_NORMAL_PRIORITY_CLASS) if enabled else False
    try:
        yield
    finally:
        if changed:
            _set(NORMAL_PRIORITY_CLASS)
