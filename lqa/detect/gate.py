"""判斷畫面上現在是不是「正在播對白」。

自動錄製最大的麻煩不是抓不到字，而是過場動畫。過場時對白框整個消失，
露出底下會動的畫面，那些內容被當成筆畫，追蹤器就以為一直在換句。

先前用「對白框區域的亮度中位數」當判準，但它分不出兩種黑：

    黑底對話框      中位數 0   -> 是對白
    整個畫面淡出成黑 中位數 0   -> 不是對白，卻被判成對白

而整個畫面變紅那種轉場，中位數又高得像對白框不在，於是兩邊都錯。

改成看**形狀**而不是看整體亮度：

    全黑區   對白框的底，必須是純黑。框不在就會露出畫面內容，不再全黑
    非全黑區 對白框以外的地方，必須**不是**純黑。整個畫面淡黑時這裡會全黑，
             靠它把「黑畫面」和「黑底對話框」分開

兩邊同時成立才算對白。再加一個持續時間：轉場途中難免有某一瞬間剛好
兩邊都成立，要求連續成立一小段時間就能把那種瞬間濾掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..config import GateConfig, Rect
from . import textmask as tm


def region_darkness(frame: np.ndarray, roi: Rect) -> int:
    """這塊區域有多暗。回傳第 99 百分位的亮度。

    用 p99 不用最大值：縮放與壓縮會讓純黑區出現幾顆雜點，
    一顆就翻盤的話判定會一直抖。也不用平均 —— 平均會被大面積的暗處
    拉低，一小塊亮的東西進來就看不出來了。
    """
    crop = tm.crop(frame, roi)
    if crop.size == 0:
        return 255          # 框到畫面外，當成不是黑的，寧可不擋
    return int(np.percentile(tm.value_channel(crop), 99))


def is_black(frame: np.ndarray, roi: Rect, tolerance: int) -> bool:
    return region_darkness(frame, roi) <= tolerance


def dialogue_showing(frame: np.ndarray, cfg: GateConfig) -> bool:
    """現在畫面上是不是正在播對白（不含持續時間的判斷）。"""
    if not cfg.configured:
        return True         # 沒設定就不擋，維持原本的行為
    if not all(is_black(frame, roi, cfg.tolerance) for roi in cfg.black_rois):
        return False
    if cfg.lit_rois and all(is_black(frame, roi, cfg.tolerance)
                            for roi in cfg.lit_rois):
        return False        # 該有東西的地方也全黑了，這是黑畫面不是對白框
    return True


def explain(frame: np.ndarray, cfg: GateConfig) -> str:
    """每個範圍現在多暗，給調試介面顯示用。"""
    parts = []
    for index, roi in enumerate(cfg.black_rois, 1):
        value = region_darkness(frame, roi)
        parts.append(f"全黑{index} {value}"
                     + ("" if value <= cfg.tolerance else "（不符）"))
    for index, roi in enumerate(cfg.lit_rois, 1):
        value = region_darkness(frame, roi)
        parts.append(f"非全黑{index} {value}"
                     + ("" if value > cfg.tolerance else "（不符）"))
    return "　".join(parts) if parts else "尚未設定範圍"


class DialogueGate:
    """加上持續時間的判斷。

    轉場途中難免有某一瞬間剛好兩邊都成立（例如畫面正好滑到一半）。
    要求連續成立一段時間，那種瞬間就過不了關。

    反過來，一旦成立就立刻放行、不成立也立刻收手 —— 對白消失是真的
    消失，沒有必要拖。
    """

    def __init__(self, cfg: GateConfig):
        self.cfg = cfg
        self._since: Optional[float] = None
        self.open = False

    def reset(self) -> None:
        self._since = None
        self.open = False

    def update(self, frame: np.ndarray, now_ms: float) -> bool:
        """餵一幀進來，回傳現在算不算對白畫面。"""
        if not dialogue_showing(frame, self.cfg):
            self._since = None
            self.open = False
            return False
        if self._since is None:
            self._since = now_ms
        self.open = (now_ms - self._since) >= self.cfg.hold_ms
        return self.open
