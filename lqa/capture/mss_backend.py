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
from .window import resolve_region


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

        self._mss = mss.mss()
        self._window_title = window_title
        self._fallback = capture_region
        self._region = resolve_region(window_title, capture_region)
        self._refresh_every = max(1, refresh_region_every)
        self._tick = 0

    def region(self) -> Rect:
        return self._region

    def _maybe_refresh_region(self) -> None:
        """視窗可能被移動，定期重新定位一次。"""
        self._tick += 1
        if self._window_title and self._tick % self._refresh_every == 0:
            try:
                self._region = resolve_region(self._window_title, self._fallback)
            except RuntimeError:
                pass  # 視窗暫時消失就沿用舊座標

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
    backend: str = "mss",
) -> CaptureBackend:
    """建立擷取後端。目前只實作 mss，之後新增後端從這裡分流。"""
    if backend == "mss":
        return MssCapture(window_title, capture_region)
    raise ValueError(f"未知的擷取後端：{backend}")
