"""從 ROI 抽出「只剩文字」的二值遮罩。

為什麼需要這一層：遊戲對白框是半透明的，後面的背景會動。
如果直接拿原始像素做 frame diff，背景一動就會誤判成「文字變了」，
一路狂觸發 OCR。先把 ROI 二值化只留高對比的文字筆畫，
半透明層後面那些低對比的背景大多會被濾掉。
"""

from __future__ import annotations

import numpy as np

from ..config import MaskConfig, Rect


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "錄製功能需要 opencv：pip install opencv-python"
        ) from exc
    return cv2


def crop(frame: np.ndarray, roi: Rect | None) -> np.ndarray:
    if roi is None:
        return frame
    x, y, w, h = roi
    return frame[y : y + h, x : x + w]


def to_gray(image: np.ndarray) -> np.ndarray:
    cv2 = _require_cv2()
    if image.ndim == 2:
        return image
    channels = image.shape[2]
    if channels == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def build_mask(image: np.ndarray, cfg: MaskConfig) -> np.ndarray:
    """回傳 uint8 遮罩，文字為 255、其餘為 0。

    image 可為彩色或灰階，尺寸為 ROI 大小（未放大）。
    """
    cv2 = _require_cv2()
    gray = to_gray(image)

    if cfg.blur and cfg.blur >= 3:
        gray = cv2.medianBlur(gray, cfg.blur | 1)

    if cfg.clahe:
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

    if cfg.method == "bright":
        # 文字比背景亮很多時最穩：直接砍亮度門檻
        _, mask = cv2.threshold(gray, cfg.bright_threshold, 255, cv2.THRESH_BINARY)
    elif cfg.method == "adaptive":
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, -8
        )
    else:  # otsu
        _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    if cfg.invert:
        mask = cv2.bitwise_not(mask)

    # 去掉單點雜訊，避免背景閃動被當成文字
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    return mask


def upscale_for_ocr(image: np.ndarray, factor: int) -> np.ndarray:
    """OCR 前放大。小字放大 2~3 倍對辨識率影響很大。"""
    if factor <= 1:
        return image
    cv2 = _require_cv2()
    return cv2.resize(
        image, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC
    )


def mask_diff_ratio(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """以 ROI 面積正規化的差異比例。僅供診斷用。

    不要拿這個做變動偵測：文字只佔 ROI 面積的 1% 出頭，
    打完一整個字也才動到約 0.06% 的面積，訊號會被面積稀釋掉。
    """
    if a is None or b is None:
        return 1.0
    if a.shape != b.shape:
        return 1.0
    return float(np.count_nonzero(a != b)) / float(a.size or 1)


def mask_change_ratio(
    a: np.ndarray | None, b: np.ndarray | None, floor: int = 1
) -> float:
    """以**文字像素量**正規化的差異比例，這才是變動偵測該用的。

    分母取兩張遮罩中前景像素較多者，所以門檻與 ROI 大小、
    解析度、對白框尺寸都無關，換機器不必重調。
    """
    if a is None or b is None:
        return 1.0
    if a.shape != b.shape:
        return 1.0
    changed = np.count_nonzero(a != b)
    denom = max(np.count_nonzero(a), np.count_nonzero(b), floor)
    return float(changed) / float(denom)


def text_pixel_count(mask: np.ndarray) -> int:
    return int(np.count_nonzero(mask))


def content_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """遮罩中前景像素的外接框，用來判斷文字是否貼齊 ROI 邊緣（超框輔助訊號）。"""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def touches_edges(mask: np.ndarray, margin: int) -> tuple[bool, bool]:
    """回傳 (貼下緣, 貼右緣)。"""
    box = content_bbox(mask)
    if box is None:
        return False, False
    _, _, x_max, y_max = box
    h, w = mask.shape[:2]
    return (y_max >= h - 1 - margin), (x_max >= w - 1 - margin)
