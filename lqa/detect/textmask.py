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


def _hysteresis_mask(channel: np.ndarray, low: int, high: int) -> np.ndarray:
    """雙門檻遲滯：高門檻找種子，低門檻取範圍，只留連通到種子的部分。

    遊戲的對白字不是均勻的純色，而是「純白核心 -> 灰白過渡 -> 灰黑描邊」
    這種帶抗鋸齒與描邊的結構。單一門檻對這種字本質上就不管用：
    門檻高只留下核心，字會被挖空；門檻低才收得到完整筆畫，
    但背景也一起進來。

    遲滯門檻同時解決兩邊：核心一定過得了高門檻，所以每個筆畫都有種子；
    過渡區過得了低門檻而且和核心相連，所以筆畫是完整的；
    背景就算亮到過得了低門檻，因為連不到任何種子而被丟掉。
    """
    cv2 = _require_cv2()
    candidates = (channel >= low).astype(np.uint8)
    seeds = channel >= high
    if not seeds.any():
        return np.zeros_like(channel, dtype=np.uint8)

    count, labels = cv2.connectedComponents(candidates, connectivity=8)
    keep = np.zeros(count, dtype=bool)
    keep[np.unique(labels[seeds])] = True
    keep[0] = False                      # 0 是背景標籤
    return (keep[labels] * 255).astype(np.uint8)


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
    elif cfg.method == "hysteresis":
        channel = value_channel(image)
        if cfg.blur and cfg.blur >= 3:
            channel = cv2.medianBlur(channel, cfg.blur | 1)
        mask = _hysteresis_mask(channel, cfg.bright_threshold, cfg.seed_threshold)
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


def text_pixel_count(mask: np.ndarray) -> int:
    return int(np.count_nonzero(mask))


def value_median(image: np.ndarray) -> int:
    """這塊區域的亮度中位數。

    用中位數而不是平均：對白框是純黑底加白字，白字只佔一小部分像素，
    中位數幾乎不受影響（實測 0），平均卻會被拉到 16~25。
    """
    return int(np.median(value_channel(image)))


def looks_like_cutscene(image: np.ndarray, limit: int) -> bool:
    """對白框在不在。

    對白框是純黑的，所以框在的時候這塊區域的亮度中位數幾乎是 0；
    過場動畫時黑底會整個消失，露出底下的畫面，中位數就跳到上百
    （實測：純黑框 0、過場畫面 137）。

    limit <= 0 代表不檢查。對白框是半透明漸層的遊戲底色本來就不黑
    （實測中位數 34~68），硬套這個判準會把所有對白都當成過場擋掉，
    所以預設關閉，由使用者看著實際數值決定要不要開。
    """
    if limit <= 0:
        return False
    return value_median(image) > limit


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
