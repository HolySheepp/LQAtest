"""以視窗標題定位雷電模擬器，取得其工作區在螢幕上的座標。

用 ctypes 直接呼叫 user32，不額外依賴 pywin32。
綁定視窗而不是固定螢幕座標，這樣模擬器視窗被移動時 ROI 仍然有效。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

from ..config import Rect

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )


def ensure_dpi_aware() -> None:
    """關掉 DPI 虛擬化，否則在 125%/150% 縮放下拿到的座標會是錯的。"""
    if not _IS_WINDOWS:
        return
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            _user32.SetProcessDPIAware()
        except Exception:
            pass


def find_window(title_substring: str) -> Optional[int]:
    """回傳標題含有指定字串的可見視窗 handle。

    模擬器實例名稱常常很短（例如「測試」），容易誤中其他視窗，
    所以多個結果時優先挑真正的模擬器程序，其次排除多開管理器。
    """
    if not _IS_WINDOWS:
        return None
    target = title_substring.lower()
    matches = [w for w in list_windows() if target in w.title.lower()]
    if not matches:
        return None
    for candidate in matches:
        if candidate.is_emulator:
            return candidate.hwnd
    for candidate in matches:
        if not candidate.is_emulator_manager:
            return candidate.hwnd
    return matches[0].hwnd


@dataclass
class WindowInfo:
    """一個可見視窗的基本資料。

    光靠標題認不出模擬器：雷電的視窗標題是使用者自訂的實例名稱
    （可能是「測試」之類），而且多開管理器 LDMultiPlayer 也會出現在清單裡
    但那不是遊戲畫面。加上程序名稱與視窗尺寸才分得出來。
    """

    hwnd: int
    title: str
    process: str
    width: int
    height: int

    @property
    def is_emulator(self) -> bool:
        return self.process.lower() in EMULATOR_PROCESSES

    @property
    def is_emulator_manager(self) -> bool:
        return self.process.lower() in EMULATOR_MANAGERS


# 雷電模擬器實際顯示遊戲畫面的程序
EMULATOR_PROCESSES = {"dnplayer.exe", "ldplayer.exe"}
# 多開管理器，會被誤認成模擬器但抓不到遊戲畫面
EMULATOR_MANAGERS = {"dnmultiplayer.exe", "ldmultiplayer.exe", "dnmultiplayerex.exe"}


def _process_name(hwnd: int) -> str:
    """回傳視窗所屬程序的執行檔名稱，取不到就回空字串。"""
    if not _IS_WINDOWS:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        ):
            return ""
        return buf.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[WindowInfo]:
    """列出所有可見且有標題的視窗，校準時方便使用者挑。"""
    if not _IS_WINDOWS:
        return []
    ensure_dpi_aware()
    result: list[WindowInfo] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        try:
            _, _, width, height = client_rect_on_screen(hwnd)
        except RuntimeError:
            width = height = 0
        result.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                process=_process_name(hwnd),
                width=width,
                height=height,
            )
        )
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return result


def client_rect_on_screen(hwnd: int) -> Rect:
    """回傳視窗工作區（不含標題列與外框）在螢幕上的 [x, y, w, h]。"""
    if not _IS_WINDOWS:
        raise RuntimeError("僅支援 Windows")
    rect = wintypes.RECT()
    if not _user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetClientRect 失敗，hwnd={hwnd}")
    origin = wintypes.POINT(0, 0)
    if not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise RuntimeError(f"ClientToScreen 失敗，hwnd={hwnd}")
    return (origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)


def resolve_region(window_title: Optional[str], fallback: Optional[Rect]) -> Rect:
    """優先用視窗標題定位，找不到就用設定檔裡的絕對座標。"""
    ensure_dpi_aware()
    if window_title:
        hwnd = find_window(window_title)
        if hwnd:
            return client_rect_on_screen(hwnd)
        if fallback is None:
            raise RuntimeError(
                f"找不到標題含有『{window_title}』的視窗，且 profile 沒有提供 capture_region 備援座標"
            )
    if fallback is None:
        raise RuntimeError("profile 必須提供 window_title 或 capture_region 其中之一")
    return fallback
