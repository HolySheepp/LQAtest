"""OCR 引擎介面。

刻意抽象一層，之後要換 PaddleOCR、Windows.Media.Ocr 或雲端 API
都只需要新增一個實作，不動錄製流程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class OcrLine:
    text: str
    confidence: float
    box: tuple[int, int, int, int] | None = None   # x_min, y_min, x_max, y_max


@dataclass
class OcrResult:
    text: str = ""
    confidence: float = 0.0
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class OcrEngine(ABC):
    @abstractmethod
    def read(self, image: np.ndarray) -> OcrResult:
        """辨識一張圖，回傳合併後的文字。"""


def build_engine(name: str, lang: str = "en") -> OcrEngine:
    if name == "rapidocr":
        from .rapidocr_engine import RapidOcrEngine  # noqa: PLC0415

        return RapidOcrEngine(lang=lang)
    raise ValueError(f"未知的 OCR 引擎：{name}")
