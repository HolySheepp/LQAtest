"""擷取後端介面。

Frame 一律是 numpy 陣列，BGR 或 BGRA，形狀 (h, w, c)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..config import Rect


class CaptureBackend(ABC):
    """所有擷取後端的共同介面。"""

    @abstractmethod
    def region(self) -> Rect:
        """回傳目前擷取來源在螢幕上的位置與大小。"""

    @abstractmethod
    def grab(self) -> np.ndarray:
        """抓一張畫面。"""

    def close(self) -> None:
        return None

    def __enter__(self) -> "CaptureBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
