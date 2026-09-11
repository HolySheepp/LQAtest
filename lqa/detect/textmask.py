"""從 ROI 抽出「只剩文字」的二值遮罩。

為什麼需要這一層：對白框背景會隨場景變動（漸變變暗的畫面仍看得到底下的動態），
直接拿原始像素做 frame diff 的話，背景一動就會誤判成「文字變了」，一路狂觸發 OCR。
先把 ROI 轉成只剩文字筆畫的遮罩，被壓暗的背景就會被濾掉。

取字方式見 MaskConfig 的說明；預設的 value（RGB 三通道最大值）能同時
收得到白色對白、<color> 變色字與淺藍發話者名。
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


def hex_to_bgr(value: str) -> tuple[int, int, int]:
    """"#ff8a00" -> (0, 138, 255)，注意 OpenCV 是 BGR 順序。"""
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"無法解析顏色：{value}")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (b, g, r)


def value_channel(image: np.ndarray) -> np.ndarray:
    """RGB 三通道取最大值，等同 HSV 的 V。

    比灰階好用的原因：灰階是加權平均，會低估飽和色。
    #ff8a00 的灰階值只有 157，但 V 是 255，和白字同一個量級，
    所以同一個門檻就能同時收白字與變色字。
    """
    if image.ndim == 2:
        return image
    return image[:, :, :3].max(axis=2)


def _colorkey_mask(image: np.ndarray, cfg: MaskConfig) -> np.ndarray:
    """只保留與指定顏色夠接近的像素。"""
    if image.ndim == 2:
        raise ValueError("colorkey 需要彩色影像")
    if not cfg.text_colors:
        raise ValueError("method=colorkey 但 text_colors 是空的")

    pixels = image[:, :, :3].astype(np.int16)
    tol_sq = float(cfg.color_tolerance) ** 2
    mask = np.zeros(image.shape[:2], dtype=bool)
    for color in cfg.text_colors:
        target = np.array(hex_to_bgr(color), dtype=np.int16)
        delta = pixels - target
        mask |= np.einsum("ijk,ijk->ij", delta, delta) <= tol_sq
    return (mask * 255).astype(np.uint8)


def build_mask(image: np.ndarray, cfg: MaskConfig) -> np.ndarray:
    """回傳 uint8 遮罩，文字為 255、其餘為 0。

    image 可為彩色或灰階，尺寸為 ROI 大小（未放大）。
    """
    cv2 = _require_cv2()

    if cfg.method == "colorkey":
        mask = _colorkey_mask(image, cfg)
    elif cfg.method == "value":
        channel = value_channel(image)
        if cfg.blur and cfg.blur >= 3:
            channel = cv2.medianBlur(channel, cfg.blur | 1)
        _, mask = cv2.threshold(channel, cfg.bright_threshold, 255, cv2.THRESH_BINARY)
    else:
        gray = to_gray(image)
        if cfg.blur and cfg.blur >= 3:
            gray = cv2.medianBlur(gray, cfg.blur | 1)
        if cfg.clahe:
            gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)

        if cfg.method == "bright":
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


def masked_value(image: np.ndarray, mask: np.ndarray, grow: int = 1) -> np.ndarray:
    """用遮罩把背景挖掉，但**保留文字本身的灰階層次**。

    純二值遮罩送進 OCR 會把抗鋸齒邊緣一起砍掉，小字很容易糊成一團
    （實測把 failed 讀成 falled）。這裡改成：遮罩範圍內保留原始亮度、
    範圍外一律填白，等於「乾淨背景 + 原本的筆畫」。

    grow 會把遮罩稍微膨脹，把門檻邊緣那圈半亮的抗鋸齒像素一起納進來，
    這正是讓字看起來銳利的部分。
    """
    cv2 = _require_cv2()
    region = mask
    if grow > 0:
        region = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=grow)
    value = value_channel(image)
    # 文字是亮的，OCR 習慣白底黑字，所以反過來
    return np.where(region > 0, 255 - value, 255).astype(np.uint8)


def upscale_for_ocr(image: np.ndarray, factor: int) -> np.ndarray:
    """OCR 前放大。小字放大 2~3 倍對辨識率影響很大。"""
    if factor <= 1:
        return image
    cv2 = _require_cv2()
    # 等比例放大。fx 與 fy 一定要相同，否則字會被拉長變形。
    return cv2.resize(
        image, None, fx=factor, fy=factor, interpolation=cv2.INTER_LANCZOS4
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
