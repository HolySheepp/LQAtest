"""RapidOCR（ONNXRuntime）引擎。

選它的理由：純 ONNX、pip 裝完即用、不需要額外環境，英文與中日韓都有模型。
之後若要追求極限精度再換 PaddleOCR。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

from .base import OcrEngine, OcrLine, OcrResult, order_lines


class RapidOcrEngine(OcrEngine):
    def __init__(
        self,
        lang: str = "en",
        join_with: str = " ",
        threads: int = 3,
        det_limit_type: str = "max",
        det_limit_side_len: int = 960,
    ):
        self.lang = lang
        self.join_with = join_with
        self.threads = threads
        self._engine = self._create(threads, det_limit_type, det_limit_side_len)

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

    @staticmethod
    def _thread_params(threads: int) -> dict[str, Any]:
        """限制 onnxruntime 的執行緒數。

        預設 0 代表用滿所有核心，在 20 核機器上會有上千趴的 CPU 使用率，
        把模擬器餓死。限制之後 CPU 降一半而速度不受影響。
        """
        if threads <= 0:
            return {}
        return {
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }

    @staticmethod
    def _det_params(limit_type: str, side_len: int) -> dict[str, Any]:
        """文字偵測階段的縮放規則。**這是整個 OCR 最大的效能開關。**

        RapidOCR 預設 min/736：把**短邊**放大到 736。對白框只有 404x161，
        短邊會被放大 4.6 倍成 1846x736，於是偵測一張要 2.9 秒，
        而辨識本身只要 0.15 秒 —— 時間全花在放大後的偵測上。

        改成限制長邊（max/960）之後，404x161 完全不需要縮放，
        同一張圖 97ms，快十三倍，而且辨識結果一字不差。
        """
        return {"Det.limit_type": limit_type, "Det.limit_side_len": side_len}

    @classmethod
    def _create(cls, threads: int, limit_type: str, side_len: int) -> Any:
        params = {**cls._thread_params(threads), **cls._det_params(limit_type, side_len)}
        try:
            from rapidocr import RapidOCR  # noqa: PLC0415

            cls._quiet()
            return RapidOCR(params=params) if params else RapidOCR()
        except ImportError:
            pass
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415

            cls._quiet()
            # 舊版沒有 params，靠環境變數限制
            if threads > 0:
                os.environ.setdefault("OMP_NUM_THREADS", str(threads))
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
