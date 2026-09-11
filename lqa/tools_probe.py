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
from .imageio import imread, imwrite

# 遮罩覆蓋率高於此值，多半是 ROI 框到背景或門檻太鬆
_COVERAGE_WARN = 0.15

# --sources 會逐一試這些，挑辨識最準的
OCR_SOURCES = ("masked_gray", "mask", "gray", "color")


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError("診斷需要 opencv：pip install opencv-python") from exc
    return cv2


def _draw_rois(cv2, frame: np.ndarray, profile: Profile) -> np.ndarray:
    """把 ROI 畫在整張畫面上，用來確認框的範圍對不對。

    「文字貼齊邊緣」的提示光看數字判斷不出是真超框還是 ROI 框太小，
    看這張圖最快：文字要完整落在框內，而且和框邊留一點空隙。
    """
    canvas = frame.copy()
    boxes = (
        ("body", profile.body_roi, (0, 220, 0)),
        ("speaker", profile.speaker_roi, (255, 160, 0)),
    )
    for label, roi, color in boxes:
        if not roi:
            continue
        x, y, w, h = roi
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, 1)
        cv2.putText(canvas, label, (x, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1, cv2.LINE_AA)
    return canvas


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
    compare_sources: bool = False,
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

    # 亮度分布：用來客觀挑門檻，不必靠猜
    value = tm.value_channel(crop)
    p50, p90, p99 = (int(np.percentile(value, q)) for q in (50, 90, 99))
    print(f"  亮度分布     中位數={p50}  90%={p90}  99%={p99}  最大={int(value.max())}")
    if int(value.max()) < 200:
        print(f"  [注意] 整塊最亮才 {int(value.max())}，文字不是預期的 #fefefe(254)。"
              "可能是擷取時被套了濾鏡/調光，或 ROI 沒框到文字")
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
        print(f"  [警告] 文字貼齊 ROI {edges}，很可能是框太小正在切掉文字。"
              "打開 ROI標示.png 確認文字有沒有完整落在框內")

    stem = f"{index:02d}_{label}"
    imwrite(out_dir / f"{stem}_原圖.png", crop)
    imwrite(out_dir / f"{stem}_遮罩.png", mask)

    # 存下真正送進 OCR 的那張圖。OCR 讀不好時，答案幾乎都在這張圖上，
    # 看原圖和遮罩是看不出來的。
    ocr_image = _ocr_input(crop, mask, cfg, ocr_source)
    imwrite(out_dir / f"{stem}_送進OCR.png", ocr_image)
    print(f"  送進 OCR     {ocr_source}  放大 {cfg.upscale} 倍  "
          f"-> {ocr_image.shape[1]} x {ocr_image.shape[0]}")

    if engine is None:
        print("  OCR          未安裝，略過")
    else:
        result = engine.read(ocr_image)
        print(f"  OCR 讀到     {result.text!r}")
        print(f"  信心值       {result.confidence:.3f}  ({len(result.lines)} 行)")
        low = [ln for ln in result.lines if ln.confidence < 0.9]
        for ln in low:
            print(f"  [低信心] {ln.confidence:.3f}  {ln.text!r}")
        if result.is_empty and pixels >= cfg.min_text_pixels:
            print("  [警告] 遮罩有文字像素但 OCR 讀不出來。"
                  "試試把 mask.upscale 調大，或把 ocr.source 改成 gray")

        if compare_sources:
            print("")
            print("  各種取字方式比較（挑辨識最準的填進 profile 的 ocr.source）：")
            for name in OCR_SOURCES:
                candidate = _ocr_input(crop, mask, cfg, name)
                imwrite(out_dir / f"{stem}_送進OCR_{name}.png", candidate)
                outcome = engine.read(candidate)
                marker = " <-- 目前使用" if name == ocr_source else ""
                print(f"    {name:<12} {outcome.confidence:.3f}  "
                      f"{outcome.text!r}{marker}")
    print("")


def run_probe(
    profile_path: Path,
    out_dir: Path,
    samples: int = 1,
    interval: float = 1.0,
    image: Optional[Path] = None,
    compare_sources: bool = False,
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
        imwrite(out_dir / f"{index:02d}_全畫面.png", frame)
        imwrite(out_dir / f"{index:02d}_ROI標示.png", _draw_rois(cv2, frame, profile))
        _describe(cv2, "對白框", frame, profile.body_roi, profile.mask,
                  engine, out_dir, index, profile.ocr.source, compare_sources)
        _describe(cv2, "姓名框", frame, profile.speaker_roi,
                  profile.effective_speaker_mask(), engine, out_dir, index,
                  profile.ocr.source, compare_sources)

    if image is not None:
        frame = imread(image)
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
    print("  *_ROI標示.png   文字要完整落在框內，而且和框邊留一點空隙")
    print("  *_遮罩.png      應該只剩文字筆畫，背景全黑，而且每個字都在")
    print("  *_送進OCR.png   OCR 讀不好時，答案通常在這張圖上")
    return 0
