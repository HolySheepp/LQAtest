"""校準工具：框選 ROI 並即時預覽文字遮罩。

遮罩預覽是這個工具最重要的部分。半透明對白框加上會動的背景，
二值化參數調不對的話，變動偵測會一直誤觸發，或是根本抓不到文字。
在這裡調到「畫面只剩文字筆畫、背景乾乾淨淨」再存檔。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import MaskConfig, Profile, Rect

_METHODS = ("otsu", "adaptive", "bright")

_HELP = """
遮罩預覽操作：
  1 / 2 / 3   切換二值化方式 (otsu / adaptive / bright)
  i           反相（淺底深字時開）
  c           開關 CLAHE 局部對比強化
  [ / ]       調整 bright 門檻
  - / =       調整 OCR 放大倍率
  s           存檔並離開
  q / Esc     放棄離開
目標：畫面上只剩文字筆畫，背景越乾淨越好。
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
    print(f"請用滑鼠框選：{title}（框好按 Enter 確認，按 c 取消）")
    box = cv2.selectROI(title, frame, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(title)
    x, y, w, h = (int(v) for v in box)
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def run_calibration(
    profile_path: Path,
    window_title: Optional[str] = None,
    region: Optional[Rect] = None,
) -> int:
    cv2 = _require_cv2()
    from .capture.mss_backend import open_capture
    from .detect import textmask as tm

    capture = open_capture(window_title, region)
    frame = capture.grab()
    resolved = capture.region()
    capture.close()
    print(f"擷取來源：{resolved}  （w={resolved[2]}, h={resolved[3]}）")

    body_roi = _select_roi(cv2, frame, "1. 對白框（必選）")
    if body_roi is None:
        print("沒有選取對白框，取消。")
        return 2
    speaker_roi = _select_roi(cv2, frame, "2. 姓名框（可略過，直接按 Enter）")

    mask_cfg = MaskConfig()
    print(_HELP)

    win = "遮罩預覽"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    saved = False
    while True:
        body = tm.crop(frame, body_roi)
        mask = tm.build_mask(body, mask_cfg)
        info = (
            f"{mask_cfg.method} invert={mask_cfg.invert} clahe={mask_cfg.clahe} "
            f"bright={mask_cfg.bright_threshold} upscale={mask_cfg.upscale} "
            f"pixels={tm.text_pixel_count(mask)}"
        )
        preview = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        cv2.putText(preview, info, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (0, 200, 255), 1, cv2.LINE_AA)
        cv2.imshow(win, preview)

        key = cv2.waitKey(50) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("s"):
            saved = True
            break
        if key in (ord("1"), ord("2"), ord("3")):
            mask_cfg.method = _METHODS[key - ord("1")]
        elif key == ord("i"):
            mask_cfg.invert = not mask_cfg.invert
        elif key == ord("c"):
            mask_cfg.clahe = not mask_cfg.clahe
        elif key == ord("["):
            mask_cfg.bright_threshold = max(0, mask_cfg.bright_threshold - 5)
        elif key == ord("]"):
            mask_cfg.bright_threshold = min(255, mask_cfg.bright_threshold + 5)
        elif key == ord("-"):
            mask_cfg.upscale = max(1, mask_cfg.upscale - 1)
        elif key == ord("="):
            mask_cfg.upscale = min(4, mask_cfg.upscale + 1)

    cv2.destroyAllWindows()
    if not saved:
        print("已取消，未寫入 profile。")
        return 2

    profile = Profile(
        name=profile_path.stem,
        window_title=window_title,
        capture_region=None if window_title else resolved,
        body_roi=body_roi,
        speaker_roi=speaker_roi,
        mask=mask_cfg,
    )
    profile.save(profile_path)
    print(f"已寫入 profile：{profile_path}")
    print(f"  對白框 {body_roi}")
    print(f"  姓名框 {speaker_roi or '(未設定，將不檢查發話者)'}")
    return 0
