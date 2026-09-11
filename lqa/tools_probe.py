"""單張診斷：用目前的 profile 抓一張畫面，把每一步的中間結果都吐出來。

這是校準完之後、正式錄製之前該跑的東西。錄製本身不會告訴你
「遮罩把字吃掉了」或「ROI 框到背景了」，但這裡會。

輸出：
  終端機   ROI 尺寸、遮罩覆蓋率、OCR 讀到什麼、信心值、警告
  圖檔     原始畫面、ROI 原圖、ROI 遮罩，可以直接打開看
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np

from .config import MaskConfig, Profile, Rect

# 遮罩覆蓋率高於此值，多半是 ROI 框到背景或門檻太鬆
_COVERAGE_WARN = 0.15


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("診斷需要 opencv：pip install opencv-python") from exc
    return cv2


def _describe(
    cv2,
    label: str,
    frame: np.ndarray,
    roi: Optional[Rect],
    cfg: MaskConfig,
    engine,
    out_dir: Path,
    index: int,
    ocr_source: str,
) -> None:
    from .detect import textmask as tm
    from .record.recorder import _ocr_input

    print(f"--- {label} ---")
    if roi is None:
        print("  未設定 ROI，略過")
        print("")
        return

    crop = tm.crop(frame, roi)
    if crop.size == 0:
        print(f"  ROI {roi} 超出擷取範圍，請重新校準")
        print("")
        return

    mask = tm.build_mask(crop, cfg)
    pixels = tm.text_pixel_count(mask)
    coverage = pixels / mask.size

    print(f"  ROI          {roi}  ({crop.shape[1]} x {crop.shape[0]})")
    print(f"  取字方式     {cfg.method}"
          + (f"  容許色距={cfg.color_tolerance}" if cfg.method == "colorkey"
             else f"  門檻={cfg.bright_threshold}"))
    print(f"  文字像素     {pixels}  覆蓋率 {coverage:.2%}")

    if pixels < cfg.min_text_pixels:
        print(f"  [警告] 文字像素低於 min_text_pixels ({cfg.min_text_pixels})，"
              "這一幀會被當成空畫面。可能是門檻太高、ROI 框錯，或畫面上真的沒字")
    elif coverage > _COVERAGE_WARN:
        print(f"  [警告] 覆蓋率偏高，ROI 可能框到背景或門檻太鬆。"
              "打開遮罩圖確認是不是只剩文字筆畫")

    bottom, right = tm.touches_edges(mask, 3)
    if bottom or right:
        edges = "、".join(e for e, hit in (("下緣", bottom), ("右緣", right)) if hit)
        print(f"  [提示] 文字貼齊 {edges}，可能是超框，也可能只是 ROI 框太緊")

    stem = f"{index:02d}_{label}"
    cv2.imwrite(str(out_dir / f"{stem}_原圖.png"), crop)
    cv2.imwrite(str(out_dir / f"{stem}_遮罩.png"), mask)

    if engine is None:
        print("  OCR          未安裝，略過")
    else:
        result = engine.read(_ocr_input(crop, mask, cfg, ocr_source))
        print(f"  OCR 讀到     {result.text!r}")
        print(f"  信心值       {result.confidence:.3f}  ({len(result.lines)} 行)")
        if result.is_empty and pixels >= cfg.min_text_pixels:
            print("  [警告] 遮罩有文字像素但 OCR 讀不出來。"
                  "試試把 mask.upscale 調大，或把 ocr.source 改成 gray")
    print("")


def run_probe(
    profile_path: Path,
    out_dir: Path,
    samples: int = 1,
    interval: float = 1.0,
    image: Optional[Path] = None,
) -> int:
    """image 有給的話就診斷這張圖，不去抓螢幕。

    用途是拿現成的遊戲截圖調參數，不必一邊開著模擬器。
    注意截圖的尺寸必須和 profile 校準時的擷取範圍一致，ROI 座標才對得上。
    """
    cv2 = _require_cv2()
    from .capture.mss_backend import open_capture

    profile = Profile.load(profile_path)
    profile.validate()

    try:
        from .ocr.base import build_engine

        engine = build_engine(profile.ocr.engine, profile.ocr.lang)
    except ImportError as exc:
        print(f"提醒：{exc}")
        print("      只做遮罩診斷，不跑 OCR。")
        print("")
        engine = None

    out_dir.mkdir(parents=True, exist_ok=True)

    def inspect(frame: np.ndarray, index: int, source: str) -> None:
        print(f"=== 第 {index} 張  來源 {source}  尺寸 "
              f"{frame.shape[1]} x {frame.shape[0]} ===")
        cv2.imwrite(str(out_dir / f"{index:02d}_全畫面.png"), frame)
        _describe(cv2, "對白框", frame, profile.body_roi, profile.mask,
                  engine, out_dir, index, profile.ocr.source)
        _describe(cv2, "姓名框", frame, profile.speaker_roi,
                  profile.effective_speaker_mask(), engine, out_dir, index,
                  profile.ocr.source)

    if image is not None:
        frame = cv2.imread(str(image), cv2.IMREAD_COLOR)
        if frame is None:
            raise FileNotFoundError(f"讀不到圖檔：{image}")
        inspect(frame, 1, str(image))
    else:
        capture = open_capture(profile.window_title, profile.capture_region)
        try:
            for index in range(1, samples + 1):
                if index > 1:
                    time.sleep(interval)
                inspect(capture.grab(), index, str(capture.region()))
        finally:
            capture.close()

    print(f"圖檔已存到：{out_dir.resolve()}")
    print("打開 *_遮罩.png 確認：應該只剩文字筆畫，背景全黑，而且每個字都在。")
    return 0
