"""Unicode 安全的影像讀寫。

OpenCV 的 imread / imwrite 在 Windows 上走的是 ANSI API，
路徑只要含非 ASCII 字元就會壞掉：實測寫出來的檔名夾雜私用區字元，
大部分檔案根本沒落地，而且 imwrite 只回傳 False 不會丟例外，
所以錯誤會被整個吞掉。

這裡改走 imencode / imdecode 加上 Python 自己的檔案 I/O，
路徑由 Python 處理就沒有編碼問題。中文專案路徑、中文 session 名稱
都會經過這條路，所以專案內一律用這兩個函式，不要直接呼叫 cv2。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ImportError("需要 opencv：pip install opencv-python") from exc
    return cv2


def imwrite(path: str | Path, image: np.ndarray, quality: int | None = None) -> Path:
    """寫出影像。路徑含中文也沒問題。"""
    cv2 = _require_cv2()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    params: list[int] = []
    suffix = p.suffix.lower()
    if quality is not None and suffix in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]

    ok, buffer = cv2.imencode(suffix or ".png", image, params)
    if not ok:
        raise OSError(f"影像編碼失敗：{p}")
    p.write_bytes(buffer.tobytes())
    return p


def imread(path: str | Path, flags: int | None = None) -> np.ndarray | None:
    """讀入影像，讀不到回傳 None。路徑含中文也沒問題。"""
    cv2 = _require_cv2()
    p = Path(path)
    if not p.is_file():
        return None
    data = np.frombuffer(p.read_bytes(), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags)
