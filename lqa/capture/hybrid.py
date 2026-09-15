"""預設擷取後端：平常走便宜的路，被蓋住時才走貴的。

實測（輪詢 60ms，模擬器 dnplayer.exe 的 CPU 使用率）：

    不擷取（基準）        0.4 %
    PrintWindow 輪詢     5.4 %      約七倍
    mss 輪詢             0.8 %      和基準沒有差別

PrintWindow 的成本落在模擬器身上 —— 它強迫整個視窗重繪，
所以我們自己的行程只看到 4.4ms 對 1.8ms 的 CPU 差距，看不出全貌。
使用者回報「開了腳本模擬器會卡」就是這個。

但 mss 抓的是螢幕區域，被別的視窗蓋住就會抓到蓋在上面的東西，
而且完全不報錯。所以每次擷取前先用 WindowFromPoint 檢查有沒有被蓋住
（只要十幾微秒），沒被蓋就用 mss，被蓋住才退回 PrintWindow。

結果是正常使用時模擬器完全不受影響，而使用者把別的視窗拉到前面時
畫面依然正確，不必在「會卡」和「會抓錯」之間二選一。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import Rect
from .base import CaptureBackend
from .gdi_backend import print_window
from .window import (
    WindowMinimized,
    client_rect_on_screen,
    ensure_dpi_aware,
    find_window,
    is_covered,
    is_minimized,
)


class HybridCapture(CaptureBackend):
    def __init__(self, window_title: str, roi: Optional[Rect] = None,
                 refresh_region_every: int = 30):
        ensure_dpi_aware()
        try:
            import mss  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ImportError("擷取功能需要 mss：pip install mss") from exc

        self._sct = mss.MSS()
        self._title = window_title
        # 只檢查真正在乎的那塊（對白框）有沒有被蓋住。
        # 整個視窗被蓋住一角但對白框沒事的話，沒必要付 PrintWindow 的代價。
        self._roi = roi
        self._refresh_every = max(1, refresh_region_every)
        self._tick = 0
        self._hwnd: Optional[int] = None
        self._region: Rect = (0, 0, 0, 0)
        self._unavailable: Optional[str] = None
        self.last_path = ""
        self.covered_frames = 0
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

    def _watch_rect(self) -> Rect:
        """要檢查遮擋的螢幕區域。有給 ROI 就只看 ROI。"""
        x, y, w, h = self._region
        if not self._roi:
            return self._region
        rx, ry, rw, rh = self._roi
        return (x + rx, y + ry, rw, rh)

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
        if is_minimized(self._hwnd):
            self._unavailable = (
                f"視窗「{self._title}」已最小化，抓不到畫面。"
                "請把模擬器還原（不必移到最前面）後再試。"
            )
        elif self._unavailable:
            self._unavailable = None

        x, y, w, h = self._region
        if is_covered(self._hwnd, self._watch_rect()):
            self.covered_frames += 1
            self.last_path = "printwindow"
            frame = print_window(self._hwnd, w, h)
            if frame is not None:
                return frame
            # PrintWindow 失敗就退回螢幕擷取，內容可能不對但至少不會中斷
            self.last_path = "mss(fallback)"
        else:
            self.last_path = "mss"

        raw = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        return np.asarray(raw)[:, :, :3]

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
