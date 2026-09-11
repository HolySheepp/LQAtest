"""RapidOCR（ONNXRuntime）引擎。

選它的理由：純 ONNX、pip 裝完即用、不需要額外環境，英文與中日韓都有模型。
之後若要追求極限精度再換 PaddleOCR。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from .base import OcrEngine, OcrLine, OcrResult, order_lines


class RapidOcrEngine(OcrEngine):
    def __init__(self, lang: str = "en", join_with: str = " "):
        self.lang = lang
        self.join_with = join_with
        self._engine = self._create()

    @staticmethod
    def _quiet() -> None:
        """壓掉 RapidOCR 啟動時那一整排模型載入 INFO，免得蓋掉錄製的句子列表。

        不能只用 setLevel：RapidOCR 每個模組取 logger 時都會自己
        setLevel(INFO)，會把我們的設定蓋回去。改掛 filter，
        filter 不會被 setLevel 清掉。
        """
        logger = logging.getLogger("RapidOCR")
        if any(getattr(f, "_lqa_quiet", False) for f in logger.filters):
            return

        def drop_info(record: logging.LogRecord) -> bool:
            return record.levelno >= logging.WARNING

        drop_info._lqa_quiet = True  # type: ignore[attr-defined]
        logger.addFilter(drop_info)

    @classmethod
    def _create(cls) -> Any:
        try:
            from rapidocr import RapidOCR  # noqa: PLC0415

            cls._quiet()
            return RapidOCR()
        except ImportError:
            pass
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

            cls._quiet()
            return RapidOCR()
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "需要安裝 OCR 相依：pip install -r requirements-ocr.txt"
            ) from exc

    @staticmethod
    def _box_bounds(box: Any) -> tuple[int, int, int, int] | None:
        try:
            pts = np.asarray(box, dtype=float).reshape(-1, 2)
        except Exception:
            return None
        if pts.size == 0:
            return None
        return (
            int(pts[:, 0].min()),
            int(pts[:, 1].min()),
            int(pts[:, 0].max()),
            int(pts[:, 1].max()),
        )

    def _parse(self, raw: Any) -> list[OcrLine]:
        """吃下 RapidOCR v1 與 v2 兩種回傳格式。"""
        lines: list[OcrLine] = []

        # v2 / v3：具名結果物件，帶 txts / scores / boxes
        # 注意要用 hasattr 判斷，沒辨識到任何文字時 txts 會是 None 而不是空序列
        if hasattr(raw, "txts"):
            txts = getattr(raw, "txts", None) or []
            scores = getattr(raw, "scores", None)
            scores = [] if scores is None else list(scores)
            boxes = getattr(raw, "boxes", None)
            boxes = [] if boxes is None else list(boxes)
            for idx, text in enumerate(txts):
                conf = float(scores[idx]) if idx < len(scores) else 0.0
                box = self._box_bounds(boxes[idx]) if idx < len(boxes) else None
                lines.append(OcrLine(text=str(text), confidence=conf, box=box))
            return lines

        # v1：(result, elapse)，result 為 [[box, text, score], ...]
        payload = raw[0] if isinstance(raw, tuple) else raw
        if not payload:
            return lines
        for item in payload:
            try:
                box, text, score = item[0], item[1], item[2]
            except (TypeError, IndexError):
                continue
            lines.append(
                OcrLine(
                    text=str(text),
                    confidence=float(score),
                    box=self._box_bounds(box),
                )
            )
        return lines

    def read(self, image: np.ndarray) -> OcrResult:
        raw = self._engine(image)
        lines = self._parse(raw)
        if not lines:
            return OcrResult()

        lines = order_lines(lines)
        text = self.join_with.join(ln.text.strip() for ln in lines if ln.text.strip())
        confidence = sum(ln.confidence for ln in lines) / len(lines)
        return OcrResult(text=text.strip(), confidence=confidence, lines=lines)
