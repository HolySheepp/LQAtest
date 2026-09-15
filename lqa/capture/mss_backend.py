"""以 mss 擷取指定螢幕區域。

優點是零設定、相依輕，缺點是視窗被其他視窗遮住時會抓到遮擋內容。
先用這個把流程跑通；若雷電在你的機器上抓到黑畫面或需要背景擷取，
再換成 Windows Graphics Capture 後端（見 lqa/capture/README.md）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import Rect
from .base import CaptureBackend
from .window import WindowMinimized, resolve_region


class MssCapture(CaptureBackend):
    def __init__(
        self,
        window_title: Optional[str] = None,
        capture_region: Optional[Rect] = None,
        refresh_region_every: int = 60,
    ):
        try:
            import mss  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ImportError("擷取功能需要 mss：pip install mss") from exc

        self._mss = mss.MSS()
        self._window_title = window_title
        self._fallback = capture_region
        self._region = resolve_region(window_title, capture_region)
        self._refresh_every = max(1, refresh_region_every)
        self._tick = 0
        self._unavailable: Optional[str] = None

    def region(self) -> Rect:
        return self._region

    def unavailable(self) -> Optional[str]:
        """目前抓不到畫面的原因；正常時回 None。

        最小化是可復原的，所以不丟例外中斷錄製，改成讓呼叫端跳過這一幀。
        """
        return self._unavailable

    def _maybe_refresh_region(self) -> None:
        """視窗可能被移動或最小化，定期重新定位一次。"""
        self._tick += 1
        if not self._window_title or self._tick % self._refresh_every:
            return
        try:
            self._region = resolve_region(self._window_title, self._fallback)
            self._unavailable = None
        except WindowMinimized as exc:
            self._unavailable = str(exc)
        except RuntimeError as exc:
            # 視窗暫時找不到就沿用舊座標，但要記下來讓呼叫端知道畫面不可信
            self._unavailable = str(exc)

    def grab(self) -> np.ndarray:
        self._maybe_refresh_region()
        x, y, w, h = self._region
        raw = self._mss.grab({"left": x, "top": y, "width": w, "height": h})
        frame = np.asarray(raw)  # BGRA
        return frame[:, :, :3]   # 轉成 BGR

    def close(self) -> None:
        try:
            self._mss.close()
        except Exception:
            pass


def open_capture(
    window_title: Optional[str],
    capture_region: Optional[Rect],
    backend: str = "auto",
    roi: Optional[Rect] = None,
) -> CaptureBackend:
    """建立擷取後端。

    auto（預設）走混合模式：平常用便宜的 mss 抓螢幕區域，
    偵測到 ROI 被別的視窗蓋住才改用 PrintWindow。
    理由與實測數據見 capture/hybrid.py。

    沒有指定視窗標題（只給絕對座標）時只能用 mss。
    """
    if window_title:
        if backend == "auto":
            from .hybrid import HybridCapture

            return HybridCapture(window_title, roi)
        if backend == "printwindow":
            from .gdi_backend import PrintWindowCapture

            return PrintWindowCapture(window_title)
    if backend in ("auto", "mss"):
        return MssCapture(window_title, capture_region)
    raise ValueError(f"未知的擷取後端：{backend}")
