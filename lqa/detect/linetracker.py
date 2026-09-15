"""逐句偵測：記住每一句最完整的樣子，等下一句開始時才吐出來。

為什麼不是「等畫面靜止才擷取」：
    舊做法要求連續 N 幀遮罩不變才擷取，實測漏掉了將近一半的句子，
    而且漏的幾乎都是短句（Found it. / ! / (Where's Nev...?)）。
    短句打字快、使用者點得也快，那段靜止期根本不存在；
    模擬器變卡時每輪迴圈更久，等於把門檻拉得更高。
    刪節號造成的打字停頓又會偽裝成靜止，於是錄到半句（Though..）。

改用的判準：
    打字機只會「增加」筆畫，不會刪掉已經顯示的字。
    所以同一句之內遮罩的前景像素單調增加，
    而換到下一句時舊的筆畫會大量消失。

    1. 每一幀都和「目前這句看過最完整的那一幀」比較
    2. 像素更多就取代它（打字中）
    3. 舊筆畫大量消失就代表換句了 -> 把保留的那一幀吐出來

好處是不需要任何靜止期。只要一句話在顯示完整之後被取樣到**一次**，
就會被完整記錄下來，取樣再慢也只是降低取樣次數而不是整句漏掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..config import StabilityConfig


@dataclass
class LineEvent:
    """一句可以拿去 OCR 的內容，取自它顯示得最完整的那一幀。"""

    frame: np.ndarray          # 整張畫面，姓名框與截圖都要用
    mask: np.ndarray
    text_pixels: int
    samples: int               # 這句總共被取樣幾次，太少代表可能沒抓到完整狀態
    grew_until_end: bool       # 直到換句前像素都還在增加，八成沒顯示完
    blanked_before: bool       # 這句之前對白框曾經淨空


def _removed_pixels(previous: np.ndarray, current: np.ndarray) -> int:
    """previous 有、current 沒有的像素數。打字只會增加，換句才會大量減少。"""
    return int(np.count_nonzero(previous & ~current))


class LineTracker:
    def __init__(self, cfg: StabilityConfig, min_text_pixels: int):
        self.cfg = cfg
        self.min_text_pixels = min_text_pixels
        self._best_frame: Optional[np.ndarray] = None
        self._best_mask: Optional[np.ndarray] = None
        self._best_pixels = 0
        self._samples = 0
        self._grew_recently = False
        self._blanked = True

    def reset(self) -> None:
        self._best_frame = None
        self._best_mask = None
        self._best_pixels = 0
        self._samples = 0
        self._grew_recently = False
        self._blanked = True

    def _removal_limit(self) -> int:
        """要消失多少像素才算換句。

        打字中的消失量只有抗鋸齒抖動那幾個像素；換句時整句會被換掉，
        所以用「目前這句的一定比例」當門檻，短句長句都適用。
        """
        return max(self.cfg.min_changed_pixels,
                   int(self._best_pixels * self.cfg.line_change_ratio))

    def _take(self) -> Optional[LineEvent]:
        if self._best_frame is None or self._best_mask is None:
            return None
        event = LineEvent(
            frame=self._best_frame,
            mask=self._best_mask,
            text_pixels=self._best_pixels,
            samples=self._samples,
            grew_until_end=self._grew_recently,
            blanked_before=self._blanked,
        )
        self._best_frame = None
        self._best_mask = None
        self._best_pixels = 0
        self._samples = 0
        self._grew_recently = False
        self._blanked = False
        return event

    def _remember(self, frame: np.ndarray, mask: np.ndarray, pixels: int) -> None:
        self._samples += 1
        if pixels > self._best_pixels:
            self._best_frame = frame.copy()
            self._best_mask = mask.copy()
            self._best_pixels = pixels
            self._grew_recently = True
        else:
            self._grew_recently = False

    def feed(
        self, frame: np.ndarray, mask: np.ndarray
    ) -> Optional[LineEvent]:
        """餵一幀。回傳非 None 代表上一句已經結束，內容是它最完整的樣子。"""
        pixels = int(np.count_nonzero(mask))

        # 對白框淨空（過場、黑幕）：把上一句結算掉
        if pixels < self.min_text_pixels:
            event = self._take()
            self._blanked = True
            return event

        if self._best_mask is not None:
            removed = _removed_pixels(self._best_mask, mask)
            if removed >= self._removal_limit():
                event = self._take()
                self._remember(frame, mask, pixels)
                return event

        self._remember(frame, mask, pixels)
        return None

    def flush(self) -> Optional[LineEvent]:
        """錄製結束時把最後一句結算出來，否則它永遠等不到下一句。"""
        return self._take()
