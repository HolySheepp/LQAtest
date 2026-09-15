"""把截圖綁定到翻譯文本的條目。

介面版的截圖流程會在畫面上高亮「接下來要拍的是哪一條」，
所以每張截圖對應到哪一條是**已知的**，不需要事後靠序列對齊猜。

這讓比對從「模糊比對加對齊」變成「逐條對答案」：
  - 沒有順序不一致的判斷問題，位置本來就綁死
  - 沒拍到的條目就是沒拍到，不會被誤判成缺句
  - 使用者多拍/少拍時可以當場用熱鍵修正，而不是事後補救

游標的三個動作：
  shoot  拍下目前這條，游標往下移
  skip   不拍，游標往下移（那條留空，使用者自己複核）
  back   游標往回一條，下一次拍攝會覆蓋該條原本的截圖
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..capture.base import CaptureBackend
from .store import SessionStore


@dataclass
class BoundState:
    """目前的拍攝進度。"""

    cursor: int = 0
    total: int = 0
    shots: dict[int, str] = field(default_factory=dict)

    @property
    def taken(self) -> int:
        return len(self.shots)

    @property
    def finished(self) -> bool:
        return self.cursor >= self.total


class BoundCapture:
    def __init__(self, store: SessionStore, capture: CaptureBackend, total: int):
        self.store = store
        self.capture = capture
        self.state = BoundState(total=total)

    # --- 游標 ---

    def move_to(self, index: int) -> int:
        self.state.cursor = max(0, min(self.state.total, index))
        return self.state.cursor

    def skip(self) -> int:
        """跳過這一條（留空），游標往下移。"""
        return self.move_to(self.state.cursor + 1)

    def back(self) -> int:
        """游標往回一條。下一次拍攝會覆蓋該條原本的截圖。"""
        return self.move_to(self.state.cursor - 1)

    # --- 拍攝 ---

    def shoot(self, frame: Optional[np.ndarray] = None) -> Optional[int]:
        """拍下目前游標所在的條目。回傳被寫入的條目索引。"""
        if self.state.finished:
            return None
        if frame is None:
            frame = self.capture.grab()
            if self.capture.unavailable():
                return None
        index = self.state.cursor
        self.state.shots[index] = self.store.save_shot(frame, index)
        self.move_to(index + 1)
        return index

    def discard(self, index: int) -> bool:
        """刪掉某一條的截圖。"""
        rel = self.state.shots.pop(index, None)
        if rel is None:
            return False
        self.store.remove_shot(rel)
        return True

    # --- 收尾 ---

    def missing(self) -> list[int]:
        """沒有截圖的條目索引。"""
        return [i for i in range(self.state.total) if i not in self.state.shots]
