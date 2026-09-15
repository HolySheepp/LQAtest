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
from .ocr.base import OcrResult
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


def report_overlap(profile: Profile) -> bool:
    """檢查對白框與姓名框有沒有重疊。

    重疊的話姓名框會吃到第一行對白、對白框會吃到姓名的下緣，
    兩邊的 OCR 都會被污染，而且看遮罩圖不一定看得出來。
    """
    body, speaker = profile.body_roi, profile.speaker_roi
    if not body or not speaker:
        return False
    bx, by, bw, bh = body
    sx, sy, sw, sh = speaker
    overlap_w = min(bx + bw, sx + sw) - max(bx, sx)
    overlap_h = min(by + bh, sy + sh) - max(by, sy)
    if overlap_w <= 0 or overlap_h <= 0:
        return False
    print(f"  [警告] 對白框與姓名框重疊 {overlap_w}x{overlap_h} px。"
          f"姓名框下緣 {sy + sh}、對白框上緣 {by}，"
          "兩邊的 OCR 會互相污染，請重新框選讓它們分開")
    return True


# 每一邊各自的擴張方式：(往左移, 往上移, 往右加寬, 往下加高)
# 往左/往上擴張時只移動起點，寬高由「原本的邊界保持不動」推回來，
# 不能再另外加 pad —— 那會讓兩邊同時變大，把隔壁的元件也框進來。
SIDE_OFFSETS = {
    "左": (1, 0, 0, 0),
    "右": (0, 0, 1, 0),
    "上": (0, 1, 0, 0),
    "下": (0, 0, 0, 1),
}


def report_margins(frame: np.ndarray, roi: Rect, mask: np.ndarray) -> dict[str, int]:
    """印出框內文字距離四邊的留白，並回傳數值。"""
    from .detect import textmask as tm

    _, _, w, h = roi
    box = tm.content_bbox(mask)
    if box is None:
        return {}
    x0, y0, x1, y1 = box
    margins = {"上": y0, "下": h - 1 - y1, "左": x0, "右": w - 1 - x1}
    print("  框內留白     " + "  ".join(f"{s} {g}" for s, g in margins.items()) + " px")
    return margins


def report_fit(
    label: str,
    frame: np.ndarray,
    roi: Rect,
    cfg: MaskConfig,
    engine,
    ocr_source: str,
    base: OcrResult,
    margins: dict[str, int],
    pad: int = 30,
) -> None:
    """檢查 ROI 有沒有把文字切掉，靠的是直接證據而不是猜。

    做法：把 ROI 單獨往某一個方向擴張後重跑 OCR。
    如果擴張後讀到更多字，就證明那一邊原本切掉了東西，
    而且能直接告訴使用者被切掉的是什麼。

    之前試過用像素量、密度之類的啟發式判斷框外那塊是不是被切掉的文字，
    都不可靠：姓名框上方的角色立繪會被誤報，而框把一行字攔腰切斷時
    外面那半反而比框內還多。改看 OCR 結果就沒有這些模糊地帶 ——
    立繪 OCR 不出東西，被切掉的字會。
    """
    from .compare.normalize import match_key
    from .detect import textmask as tm
    from .record.recorder import _ocr_input

    if engine is None or not margins:
        return

    height, width = frame.shape[:2]
    x, y, w, h = roi
    base_len = len(match_key(base.text))
    base_lines = max(1, len(base.lines))
    findings: list[str] = []

    for side, (left, up, right, down) in SIDE_OFFSETS.items():
        if margins.get(side, 99) > 3:
            continue  # 文字根本沒頂到這一邊，不可能被切
        nx = max(0, x - left * pad)
        ny = max(0, y - up * pad)
        nw = min(width - nx, w + (x - nx) + right * pad)
        nh = min(height - ny, h + (y - ny) + down * pad)
        if nw <= 0 or nh <= 0 or (nx, ny, nw, nh) == roi:
            continue

        grown = tm.crop(frame, (nx, ny, nw, nh))
        grown_mask = tm.build_mask(grown, cfg)
        result = engine.read(_ocr_input(grown, grown_mask, cfg, ocr_source))
        gained = len(match_key(result.text)) - base_len
        if gained < 2:
            continue
        # 多出來的字如果自成新的一行，那是隔壁的另一段文字（姓名框下方就是對白），
        # 不是原本這行被切掉的尾巴。真正被切掉時行數不會變。
        if len(result.lines) > base_lines:
            continue
        findings.append(f"{side}(多讀到 {gained} 個字元：{result.text!r})")

    if findings:
        print(f"  [警告] {label}的框正在切掉文字。往外擴 {pad}px 後 "
              + "、".join(findings))
        print("         請重新校準把框放大")
        return

    tight = [s for s, gap in margins.items() if gap <= 3]
    if tight:
        print(f"  [提示] 文字離 {'、'.join(tight)} 邊只剩不到 3px，"
              "擴大後沒有多讀到字，但仍建議留 10px 以上餘裕")


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

    margins = report_margins(frame, roi, mask)

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

        report_fit(label, frame, roi, cfg, engine, ocr_source, result, margins)

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
        report_overlap(profile)
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
        capture = open_capture(
            profile.window_title, profile.capture_region, profile.capture_backend,
            roi=profile.body_roi,
        )
        kind = type(capture).__name__
        note = {
            "HybridCapture": "平常用便宜的螢幕擷取，偵測到被蓋住才改用 PrintWindow",
            "PrintWindowCapture": "一律請視窗自己畫，被蓋住也抓得到但模擬器會變慢",
            "MssCapture": "只抓螢幕區域，模擬器被蓋住會抓到遮擋內容",
        }.get(kind, "")
        print(f"擷取後端：{kind}（{note}）")
        if getattr(capture, "last_path", "") or hasattr(capture, "covered_frames"):
            print(f"          本次走的路徑：{getattr(capture, 'last_path', '?')}")
        print("")
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
