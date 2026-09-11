"""以 PrintWindow 直接向視窗要畫面，不受其他視窗遮擋影響。

為什麼需要這個：mss 抓的是「螢幕上那塊區域」，只要有任何視窗蓋在
模擬器上面，抓到的就是那個視窗的內容，而且完全不會報錯 ——
校準會框到別的東西，錄製會錄到一堆垃圾。

PrintWindow 是請視窗自己把內容畫進我們給的 DC，所以被蓋住也沒關係。
硬體加速的視窗常常會回黑畫面，但雷電模擬器實測可行（PP-OCR 讀得出
對白內容，和 mss 抓到的完全一致），所以預設走這條路，
抓不到內容時再自動退回 mss。

視窗仍然必須是「還原」狀態，最小化的視窗沒有可繪製的內容。
但不需要移到最前面。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional

import numpy as np

from ..config import Rect
from .base import CaptureBackend
from .window import (
    WindowMinimized,
    client_rect_on_screen,
    ensure_dpi_aware,
    find_window,
    is_minimized,
)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# PrintWindow 旗標。PW_RENDERFULLCONTENT 才抓得到硬體加速的內容。
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def print_window(hwnd: int, width: int, height: int) -> Optional[np.ndarray]:
    """請視窗把自己畫出來，回傳 BGR 影像。失敗回 None。"""
    if width <= 0 or height <= 0:
        return None
    window_dc = _user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    mem_dc = _gdi32.CreateCompatibleDC(window_dc)
    bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not mem_dc or not bitmap:
        _user32.ReleaseDC(hwnd, window_dc)
        return None
    _gdi32.SelectObject(mem_dc, bitmap)
    try:
        if not _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            return None
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height        # 負值代表 top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                ctypes.byref(info), DIB_RGB_COLORS):
            return None
        frame = np.frombuffer(buffer, np.uint8).reshape(height, width, 4)
        return frame[:, :, :3].copy()
    finally:
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd, window_dc)


def looks_blank(frame: Optional[np.ndarray]) -> bool:
    """判斷是不是全黑或整片單色。硬體加速視窗抓失敗時就是這個樣子。

    用標準差而不是色階數：對白場景常常是「很暗的底加白字」，
    色階數可能只有兩三種，但那是有效畫面。真正抓失敗的畫面是平的，
    標準差會趨近 0。
    """
    if frame is None or frame.size == 0:
        return True
    sample = frame[::8, ::8].astype(np.float32)
    return float(sample.mean()) < 2.0 or float(sample.std()) < 1.0


class PrintWindowCapture(CaptureBackend):
    def __init__(self, window_title: str, refresh_region_every: int = 60):
        ensure_dpi_aware()
        self._title = window_title
        self._hwnd: Optional[int] = None
        self._region: Rect = (0, 0, 0, 0)
        self._refresh_every = max(1, refresh_region_every)
        self._tick = 0
        self._unavailable: Optional[str] = None
        self._locate()

    def _locate(self) -> None:
        hwnd = find_window(self._title)
        if not hwnd:
            raise RuntimeError(f"找不到標題含有『{self._title}』的視窗")
        if is_minimized(hwnd):
            raise WindowMinimized(
                f"視窗「{self._title}」已最小化，抓不到畫面。"
                "請把模擬器還原（不必移到最前面）後再試。"
            )
        self._hwnd = hwnd
        self._region = client_rect_on_screen(hwnd)
        self._unavailable = None

    def region(self) -> Rect:
        return self._region

    def unavailable(self) -> Optional[str]:
        return self._unavailable

    def _maybe_relocate(self) -> None:
        self._tick += 1
        if self._tick % self._refresh_every:
            return
        try:
            self._locate()
        except (WindowMinimized, RuntimeError) as exc:
            self._unavailable = str(exc)

    def grab(self) -> np.ndarray:
        self._maybe_relocate()
        assert self._hwnd is not None
        if self._hwnd and is_minimized(self._hwnd):
            self._unavailable = (
                f"視窗「{self._title}」已最小化，抓不到畫面。"
                "請把模擬器還原（不必移到最前面）後再試。"
            )
        _, _, width, height = self._region
        frame = print_window(self._hwnd, width, height)
        if frame is None:
            self._unavailable = f"PrintWindow 抓取失敗（視窗「{self._title}」）"
            return np.zeros((max(height, 1), max(width, 1), 3), np.uint8)
        if self._unavailable and "PrintWindow" in self._unavailable:
            self._unavailable = None
        return frame


def can_use_print_window(window_title: str) -> bool:
    """實際抓一張看看內容是不是有效的，用來決定要不要走這條路。"""
    try:
        capture = PrintWindowCapture(window_title)
    except (WindowMinimized, RuntimeError):
        return False
    try:
        return not looks_blank(capture.grab())
    finally:
        capture.close()
