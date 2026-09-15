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


def order_lines(lines: list[OcrLine]) -> list[OcrLine]:
    """把偵測框排成人類閱讀順序：先分行，行內再由左到右。

    不能直接按 y 座標排序。OCR 會把一行對白切成好幾個框，
    同一行各框的 y_min 會因為字母升部與降部差好幾個像素，
    嚴格按 y 排就會讓相鄰兩行交錯，整句被打散。

    做法是先用框高推出容差，把垂直中心夠接近的框歸成同一行，
    行間按 y 排、行內按 x 排。
    """
    boxed = [ln for ln in lines if ln.box is not None]
    unboxed = [ln for ln in lines if ln.box is None]
    if not boxed:
        return list(lines)

    def y_center(ln: OcrLine) -> float:
        return (ln.box[1] + ln.box[3]) / 2.0  # type: ignore[index]

    heights = sorted(ln.box[3] - ln.box[1] for ln in boxed)  # type: ignore[index]
    median_height = heights[len(heights) // 2]
    tolerance = max(4.0, median_height * 0.6)

    boxed.sort(key=y_center)
    rows: list[list[OcrLine]] = []
    row_centers: list[float] = []
    for item in boxed:
        center = y_center(item)
        if rows and abs(center - row_centers[-1]) <= tolerance:
            rows[-1].append(item)
        else:
            rows.append([item])
            row_centers.append(center)

    ordered: list[OcrLine] = []
    for row in rows:
        row.sort(key=lambda ln: ln.box[0])  # type: ignore[index]
        ordered.extend(row)
    return ordered + unboxed


class OcrEngine(ABC):
    @abstractmethod
    def read(self, image: np.ndarray) -> OcrResult:
        """辨識一張圖，回傳合併後的文字。"""


def build_engine(name: str, lang: str = "en", threads: int = 3,
                 det_limit_type: str = "max", det_limit_side_len: int = 960) -> OcrEngine:
    if name == "rapidocr":
        from .rapidocr_engine import RapidOcrEngine  # noqa: PLC0415

        return RapidOcrEngine(lang=lang, threads=threads,
                              det_limit_type=det_limit_type,
                              det_limit_side_len=det_limit_side_len)
    raise ValueError(f"未知的 OCR 引擎：{name}")


def engine_for(profile) -> OcrEngine:
    """依 profile 建立引擎。集中一處，免得各呼叫端漏傳效能參數。"""
    ocr = profile.ocr
    return build_engine(ocr.engine, ocr.lang, ocr.threads,
                        ocr.det_limit_type, ocr.det_limit_side_len)
