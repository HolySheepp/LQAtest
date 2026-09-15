"""穩定幀偵測。

遊戲有逐字打字機效果，看到變動就 OCR 會抓到半句話，
所以要等「文字不再變動」才擷取一次。

這裡有兩個不直覺但必要的設計，都是實測數據逼出來的：

1. **比對整個視窗，不是只比前一幀。**
   打字打到空格時，那一幀的變動量是 0，只比前一幀會誤判成已經穩定。
   改成比對「現在」與「N 幀之前」，中間累積的字就藏不住了。

2. **差異用文字像素量正規化，不是用 ROI 面積。**
   文字只佔 ROI 面積約 1%，打完一整個字也才動到約 0.06% 的面積，
   用面積當分母的話訊號會被稀釋到跟背景雜訊同一個數量級。
   改用文字量當分母之後，門檻也不再隨解析度或對白框大小而改變。

實測分離度（合成畫面，半透明框加會動的背景）：
    背景移動、文字不動   約 0.007 ~ 0.018
    打字中的視窗累積量   約 0.13
預設門檻 0.04 落在中間，兩邊都有數倍餘裕。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..config import StabilityConfig
from .textmask import mask_change_ratio, mask_changed_pixels, text_pixel_count


@dataclass
class StableEvent:
    """一次可以拿去 OCR 的穩定畫面。"""

    mask: np.ndarray
    stable_ms: int
    text_pixels: int
    window_diff: float
    # 這次擷取之前，對白框曾經淨空過。用來分辨「重複觸發」與
    # 「劇情真的又講了一次同樣的話」。必須隨事件傳遞：
    # 旗標在觸發當下就會被清掉，事後再讀一定是 False。
    blanked_before: bool = False


class StabilityTracker:
    def __init__(self, cfg: StabilityConfig, min_text_pixels: int):
        self.cfg = cfg
        self.min_text_pixels = min_text_pixels
        self._window: deque[np.ndarray] = deque(maxlen=cfg.stable_frames + 1)
        self._prev: Optional[np.ndarray] = None
        self._last_emitted: Optional[np.ndarray] = None
        self._dirty = False
        self._last_change_ms = 0.0
        self._last_emit_ms = -1e9
        # 對白框曾經淨空過（過場、黑幕）。錄製端用它判斷「同一句又出現」
        # 是重複觸發，還是劇情真的連續講了兩次一樣的話。
        self.blanked_since_emit = True

    def reset(self) -> None:
        self._window.clear()
        self._prev = None
        self._last_emitted = None
        self._dirty = False
        self.blanked_since_emit = True

    def _ratio(self, a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
        return mask_change_ratio(a, b, self.min_text_pixels)

    def _really_changed(
        self, a: Optional[np.ndarray], b: Optional[np.ndarray], threshold: float
    ) -> bool:
        """比例與絕對像素量要同時達標才算真的變了。

        只看比例的話，短句（遮罩只有一千多像素）光是抗鋸齒邊緣抖動
        就會衝過門檻，被誤判成新的一句而重複記錄。
        """
        if self._ratio(a, b) <= threshold:
            return False
        return mask_changed_pixels(a, b) >= self.cfg.min_changed_pixels

    def feed(self, mask: np.ndarray, now_ms: float) -> Optional[StableEvent]:
        """餵一張遮罩。回傳非 None 代表這一刻可以擷取。"""
        self._window.append(mask)
        changed = self._really_changed(self._prev, mask, self.cfg.rearm_threshold)
        self._prev = mask

        if changed:
            self._dirty = True
            self._last_change_ms = now_ms

        if not self._dirty:
            return None
        # 視窗還沒填滿，無從判斷是否已經穩定
        if len(self._window) <= self.cfg.stable_frames:
            return None

        if self._really_changed(self._window[0], mask, self.cfg.diff_threshold):
            return None
        window_diff = self._ratio(self._window[0], mask)
        if now_ms - self._last_emit_ms < self.cfg.min_gap_ms:
            return None

        pixels = text_pixel_count(mask)
        if pixels < self.min_text_pixels:
            # 空畫面（過場、黑幕）：清掉狀態，等下次真的出現文字。
            # last_emitted 一併清掉，這樣空白之後重複出現的同一句仍會被記錄。
            self._dirty = False
            self._last_emitted = None
            self.blanked_since_emit = True
            return None

        # 和上次擷取的內容一樣就不重複記錄（例如點擊沒推進）
        if not self._really_changed(self._last_emitted, mask, self.cfg.diff_threshold):
            self._dirty = False
            return None

        self._dirty = False
        blanked_before = self.blanked_since_emit
        self.blanked_since_emit = False
        self._last_emitted = mask.copy()
        self._last_emit_ms = now_ms
        return StableEvent(
            mask=mask,
            stable_ms=int(now_ms - self._last_change_ms),
            text_pixels=pixels,
            window_diff=window_diff,
            blanked_before=blanked_before,
        )
