"""校準工具：框選 ROI 並即時預覽文字遮罩。

分成兩個獨立的 ROI，各自調各自的遮罩參數：

  對白框   文字通常是 #fefefe，但 <color=#xxxxxx> 標記會讓部分字變色
  姓名框   發話者可能是 #fefefe 也可能是 #5dbcfe

兩者顏色不同，所以參數分開存（profile 的 mask 與 speaker_mask）。

遮罩預覽是這個工具最重要的部分。對白框背景是漸變變暗的，
參數調不對的話，變動偵測會誤觸發，或是變色字被整段濾掉導致 OCR 缺字。
在這裡調到「畫面只剩文字筆畫、背景乾乾淨淨」再存檔。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np

from .config import MaskConfig, OcrConfig, Profile, Rect, StabilityConfig

# 預覽時的切換順序，value 擺第一個因為那是建議值
_METHODS = ("hysteresis", "value", "colorkey", "otsu", "adaptive")

_HELP = """
遮罩預覽操作：
  1 ~ 5       切換取字方式 (hysteresis / value / colorkey / otsu / adaptive)
  [ / ]       調整低門檻（colorkey 時調容許色距）
  ; / '       調整 hysteresis 的高門檻（種子）
  i           反相（淺底深字時開）
  c           開關 CLAHE（只對 otsu/adaptive 有意義）
  - / =       調整 OCR 放大倍率
  s           存檔並進入下一步
  q / Esc     放棄離開

建議用 hysteresis（預設）。遊戲的字是「純白核心 -> 灰白過渡 -> 灰黑描邊」的結構，
單一門檻要嘛把字挖空、要嘛連背景一起收；遲滯門檻用高門檻找核心當種子、
低門檻取完整筆畫，只留連通到種子的部分。
目標：上半部原圖對照下，下半部只剩完整的文字筆畫，背景乾乾淨淨。
"""


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:
        raise ImportError(
            "校準工具需要 opencv 完整版（headless 版沒有 GUI）："
            "pip install opencv-python"
        ) from exc
    return cv2


def _select_roi(cv2, frame, title: str) -> Optional[Rect]:
    print(f"請用滑鼠框選：{title}（框好按 Enter 確認，不需要則直接按 Enter）")
    box = cv2.selectROI(title, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def _compose_preview(cv2, roi_image: np.ndarray, mask: np.ndarray, info: str) -> np.ndarray:
    """上半部原圖、下半部遮罩，方便直接對照哪些字被吃掉了。"""
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    divider = np.full((2, roi_image.shape[1], 3), (0, 140, 255), dtype=np.uint8)
    stacked = np.vstack([roi_image[:, :, :3], divider, mask_bgr])
    cv2.putText(stacked, info, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 200, 255), 1, cv2.LINE_AA)
    return stacked


def _tune_mask(
    cv2,
    frame: np.ndarray,
    roi: Rect,
    window_name: str,
    initial: MaskConfig,
) -> Optional[MaskConfig]:
    """互動調整一組遮罩參數。回傳 None 代表使用者放棄。"""
    from .detect import textmask as tm

    cfg = replace(initial)
    roi_image = tm.crop(frame, roi)
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    try:
        while True:
            try:
                mask = tm.build_mask(roi_image, cfg)
            except ValueError as exc:
                print(f"  參數無效：{exc}")
                cfg.method = "value"
                continue

            if cfg.method == "colorkey":
                knob = f"tol={cfg.color_tolerance}"
            elif cfg.method == "hysteresis":
                knob = f"low={cfg.bright_threshold} seed={cfg.seed_threshold}"
            else:
                knob = f"thr={cfg.bright_threshold}"
            info = (
                f"{cfg.method} {knob} invert={cfg.invert} clahe={cfg.clahe} "
                f"upscale={cfg.upscale} pixels={tm.text_pixel_count(mask)}"
            )
            cv2.imshow(window_name, _compose_preview(cv2, roi_image, mask, info))

            key = cv2.waitKey(50) & 0xFF
            if key in (ord("q"), 27):
                return None
            if key == ord("s"):
                return cfg
            if ord("1") <= key <= ord("5"):
                cfg.method = _METHODS[key - ord("1")]
            elif key == ord("i"):
                cfg.invert = not cfg.invert
            elif key == ord("c"):
                cfg.clahe = not cfg.clahe
            elif key == ord("["):
                if cfg.method == "colorkey":
                    cfg.color_tolerance = max(5, cfg.color_tolerance - 5)
                else:
                    cfg.bright_threshold = max(0, cfg.bright_threshold - 5)
            elif key == ord("]"):
                if cfg.method == "colorkey":
                    cfg.color_tolerance = min(255, cfg.color_tolerance + 5)
                else:
                    cfg.bright_threshold = min(255, cfg.bright_threshold + 5)
            elif key == ord(";"):
                cfg.seed_threshold = max(cfg.bright_threshold + 5,
                                         cfg.seed_threshold - 5)
            elif key == ord("'"):
                cfg.seed_threshold = min(255, cfg.seed_threshold + 5)
            elif key == ord("-"):
                cfg.upscale = max(1, cfg.upscale - 1)
            elif key == ord("="):
                cfg.upscale = min(4, cfg.upscale + 1)
    finally:
        cv2.destroyWindow(window_name)


def run_calibration(
    profile_path: Path,
    window_title: Optional[str] = None,
    region: Optional[Rect] = None,
    text_colors: Optional[list[str]] = None,
) -> int:
    cv2 = _require_cv2()
    from .capture.mss_backend import open_capture

    # 已經有 profile 就沿用裡面調好的參數，只換 ROI。
    # 否則每次重框都會把辛苦調出來的門檻、放大倍率重設回預設值。
    existing: Optional[Profile] = None
    if profile_path.exists():
        try:
            existing = Profile.load(profile_path)
            print(f"沿用現有 profile 的參數（門檻 {existing.mask.bright_threshold}、"
                  f"放大 {existing.mask.upscale} 倍），只重新框選範圍。")
        except (OSError, ValueError) as exc:
            print(f"現有 profile 讀不起來（{exc}），改用預設值。")

    backend = existing.capture_backend if existing else "auto"
    capture = open_capture(window_title, region, backend)
    frame = capture.grab()
    resolved = capture.region()
    capture.close()
    print(f"擷取來源：{resolved}  （w={resolved[2]}, h={resolved[3]}）"
          f"  後端 {type(capture).__name__}")

    body_roi = _select_roi(cv2, frame, "1. 對白框（必選）")
    if body_roi is None:
        print("沒有選取對白框，取消。")
        return 2
    speaker_roi = _select_roi(cv2, frame, "2. 姓名框（可略過，直接按 Enter）")

    print(_HELP)

    if existing is not None:
        body_initial = replace(existing.mask)
    else:
        body_initial = MaskConfig(text_colors=list(text_colors or ["#fefefe"]))
    print("--- 調整對白框遮罩 ---")
    body_mask = _tune_mask(cv2, frame, body_roi, "對白框遮罩", body_initial)
    if body_mask is None:
        print("已取消，未寫入 profile。")
        return 2

    speaker_mask = None
    if speaker_roi is not None:
        if existing is not None and existing.speaker_mask is not None:
            speaker_initial = replace(existing.speaker_mask)
        else:
            # 發話者可能是白色也可能是淺藍，colorkey 模式預設兩色都收
            speaker_initial = replace(
                body_mask,
                text_colors=sorted({*(text_colors or []), "#fefefe", "#5dbcfe"}),
                min_text_pixels=max(8, body_mask.min_text_pixels // 4),
            )
        print("--- 調整姓名框遮罩 ---")
        speaker_mask = _tune_mask(cv2, frame, speaker_roi, "姓名框遮罩", speaker_initial)
        if speaker_mask is None:
            print("已取消，未寫入 profile。")
            return 2

    cv2.destroyAllWindows()

    profile = Profile(
        name=profile_path.stem,
        window_title=window_title,
        capture_region=None if window_title else resolved,
        capture_backend=backend,
        body_roi=body_roi,
        speaker_roi=speaker_roi,
        mask=body_mask,
        speaker_mask=speaker_mask,
        stability=replace(existing.stability) if existing else StabilityConfig(),
        ocr=replace(existing.ocr) if existing else OcrConfig(),
    )
    profile.save(profile_path)
    print(f"已寫入 profile：{profile_path}")
    print(f"  對白框 {body_roi}  取字方式 {body_mask.method}")
    if speaker_roi:
        print(f"  姓名框 {speaker_roi}  取字方式 {speaker_mask.method}")
    else:
        print("  姓名框 未設定，將不檢查發話者")

    _post_check(frame, profile)
    return 0


def _post_check(frame, profile: Profile) -> None:
    """校準完直接檢查一次，不要等使用者自己去跑 probe 才發現框錯了。"""
    from .detect import textmask as tm
    from .tools_probe import report_margins, report_overlap

    print("")
    print("--- 框選檢查 ---")
    report_overlap(profile)
    for label, roi, cfg in (
        ("對白框", profile.body_roi, profile.mask),
        ("姓名框", profile.speaker_roi, profile.effective_speaker_mask()),
    ):
        if roi is None:
            continue
        crop = tm.crop(frame, roi)
        if crop.size == 0:
            print(f"  {label} 超出擷取範圍")
            continue
        print(f"  [{label}]")
        report_margins(frame, roi, tm.build_mask(crop, cfg))
    print("")
    print("四邊留白最好都有 10px 以上。接著執行 lqa probe，")
    print("它會實際跑 OCR，並用擴框重測的方式確認有沒有切到字。")
