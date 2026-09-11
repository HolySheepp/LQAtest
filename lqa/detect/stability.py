"""穩定幀偵測。

遊戲有逐字打字機效果，直接看到變動就 OCR 會抓到半句話。
所以要等「文字遮罩連續 N 幀不再變動」才擷取一次。

流程：
    畫面變了 -> dirty = True -> 連續 N 幀沒變 -> 觸發一次擷取 -> dirty = False
    要等到下一次真正的變動才會再觸發，避免同一句重複記錄。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..config import StabilityConfig
from .textmask import mask_diff_ratio, text_pixel_count


@dataclass
class StableEvent:
    """一次可以拿去 OCR 的穩定畫面。"""

    mask: np.ndarray
    stable_ms: int
    text_pixels: int
    diff_since_last: float


class StabilityTracker:
    def __init__(self, cfg: StabilityConfig, min_text_pixels: int):
        self.cfg = cfg
        self.min_text_pixels = min_text_pixels
        self._prev: Optional[np.ndarray] = None
        self._last_emitted: Optional[np.ndarray] = None
        self._stable_frames = 0
        self._dirty = False
        self._last_change_ms = 0.0
        self._last_emit_ms = -1e9

    def reset(self) -> None:
        self._prev = None
        self._last_emitted = None
        self._stable_frames = 0
        self._dirty = False

    def feed(self, mask: np.ndarray, now_ms: float) -> Optional[StableEvent]:
        """餵一張遮罩。回傳非 None 代表這一刻可以擷取。"""
        diff = mask_diff_ratio(self._prev, mask)
        self._prev = mask

        if diff > self.cfg.diff_threshold:
            self._stable_frames = 0
            self._last_change_ms = now_ms
            if diff > self.cfg.rearm_threshold:
                self._dirty = True
            return None

        self._stable_frames += 1
        if not self._dirty:
            return None
        if self._stable_frames < self.cfg.stable_frames:
            return None
        if now_ms - self._last_emit_ms < self.cfg.min_gap_ms:
            return None

        pixels = text_pixel_count(mask)
        if pixels < self.min_text_pixels:
            # 空畫面（過場、黑幕）：清掉 dirty，等下次真的出現文字
            self._dirty = False
            self._last_emitted = None
            return None

        # 和上次擷取的內容一樣就不重複記錄（例如點擊沒推進）
        if mask_diff_ratio(self._last_emitted, mask) <= self.cfg.diff_threshold:
            self._dirty = False
            return None

        self._dirty = False
        self._last_emitted = mask.copy()
        self._last_emit_ms = now_ms
        return StableEvent(
            mask=mask,
            stable_ms=int(now_ms - self._last_change_ms),
            text_pixels=pixels,
            diff_since_last=diff,
        )
