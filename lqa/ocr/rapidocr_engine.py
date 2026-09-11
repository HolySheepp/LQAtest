"""RapidOCR（ONNXRuntime）引擎。

選它的理由：純 ONNX、pip 裝完即用、不需要額外環境，英文與中日韓都有模型。
之後若要追求極限精度再換 PaddleOCR。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .base import OcrEngine, OcrLine, OcrResult


class RapidOcrEngine(OcrEngine):
    def __init__(self, lang: str = "en", join_with: str = " "):
        self.lang = lang
        self.join_with = join_with
        self._engine = self._create()

    @staticmethod
    def _create() -> Any:
        try:
            from rapidocr import RapidOCR  # noqa: PLC0415

            return RapidOCR()
        except ImportError:
            pass
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

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

        # v2：具名物件，帶 txts / scores / boxes
        txts = getattr(raw, "txts", None)
        if txts is not None:
            scores = getattr(raw, "scores", None) or []
            boxes = getattr(raw, "boxes", None) or []
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

        # 依垂直位置排序，多行對白才不會亂序
        def sort_key(line: OcrLine) -> tuple[float, float]:
            if line.box is None:
                return (0.0, 0.0)
            return (float(line.box[1]), float(line.box[0]))

        lines.sort(key=sort_key)
        text = self.join_with.join(ln.text.strip() for ln in lines if ln.text.strip())
        confidence = sum(ln.confidence for ln in lines) / len(lines)
        return OcrResult(text=text.strip(), confidence=confidence, lines=lines)
