"""離線辨識：把手動截圖轉成文字。

和錄製分開的理由和當初一樣 —— 參數調整後可以重跑辨識，不必重玩一次。
差別是現在連 OCR 都不在遊玩當下做，遊玩時只存原圖。

除了逐張 OCR，這裡還做兩件清理：

  重複      連按兩次或畫面沒推進時會有兩張一樣的，文字相同就合併
  半句      打字中途按下截圖，或先點一下跳過動畫再點一次推進，
            會拍到同一句的前半。前一張的文字若是後一張的前綴，
            代表那是同一句的未完成狀態，保留完整的那張

這兩種清理都只在「相鄰」的截圖之間進行，不會跨句誤刪。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from ..compare.normalize import match_key, similarity
from ..config import Profile
from ..detect import textmask as tm
from ..imageio import imread
from ..model import CapturedLine
from ..ocr.base import OcrEngine, engine_for
from ..priority import low_priority
from .recorder import _ocr_input
from .store import SessionStore, session_meta, session_profile, session_shots

ProgressCallback = Callable[[int, int, CapturedLine], None]


def _read_regions(
    frame, profile: Profile, engine: OcrEngine
) -> tuple[str, str, float, bool, bool]:
    body_img = tm.crop(frame, profile.body_roi)
    body_mask = tm.build_mask(body_img, profile.mask)
    body = engine.read(_ocr_input(body_img, body_mask, profile.mask, profile.ocr.source))

    speaker_text = ""
    if profile.speaker_roi:
        cfg = profile.effective_speaker_mask()
        img = tm.crop(frame, profile.speaker_roi)
        mask = tm.build_mask(img, cfg)
        if tm.text_pixel_count(mask) >= max(8, cfg.min_text_pixels // 4):
            speaker_text = engine.read(_ocr_input(img, mask, cfg, profile.ocr.source)).text

    bottom, right = tm.touches_edges(body_mask, profile.ocr.edge_margin_px)
    return body.text, speaker_text, body.confidence, bottom, right


def is_partial_of(earlier: str, later: str) -> bool:
    """earlier 是不是 later 的未完成狀態（前綴且明顯較短）。

    門檻抓得保守：只有在「完全是前綴」且短了至少三個字元時才算，
    免得把兩句剛好開頭相同的對白誤併成一句。
    """
    a, b = match_key(earlier), match_key(later)
    if not a or not b or len(a) >= len(b) - 2:
        return False
    return b.startswith(a)


def clean(lines: list[CapturedLine], repeat_threshold: float = 0.97) -> tuple[list[CapturedLine], int, int]:
    """合併相鄰的重複與半句。回傳 (結果, 去重數, 去半句數)。"""
    kept: list[CapturedLine] = []
    duplicates = partials = 0
    for line in lines:
        if kept:
            previous = kept[-1]
            if similarity(previous.body_text, line.body_text) >= repeat_threshold:
                duplicates += 1
                continue
            if is_partial_of(previous.body_text, line.body_text):
                kept[-1] = line          # 用完整的取代半句
                partials += 1
                continue
        kept.append(line)
    for index, line in enumerate(kept):
        line.seq = index
    return kept, duplicates, partials


def read_session(
    session_dir: str | Path,
    profile: Optional[Profile] = None,
    engine: Optional[OcrEngine] = None,
    on_progress: Optional[ProgressCallback] = None,
    do_clean: bool = True,
) -> list[CapturedLine]:
    """把一個 session 的截圖全部辨識成文字並寫入 lines.jsonl。"""
    session = Path(session_dir)
    shots = session_shots(session)
    if not shots:
        raise FileNotFoundError(f"{session} 裡沒有任何截圖（shots/*.png）")

    # 綁定模式的檔名就是條目索引，辨識完直接帶著對應關係，比對不必再對齊
    meta = session_meta(session) or {}
    bound = meta.get("mode") == "bound"

    if profile is None:
        stored = session_profile(session)
        if stored is None:
            raise FileNotFoundError(
                f"{session}/meta.json 裡沒有 profile，請用 --profile 指定"
            )
        profile = Profile.from_dict(stored)
    engine = engine or engine_for(profile)

    lines: list[CapturedLine] = []
    # 解析通常和遊玩同時進行，讓出排程給模擬器比早幾秒跑完重要
    with low_priority(profile.ocr.low_priority):
        for index, path in enumerate(shots):
            frame = imread(path)
            if frame is None:
                print(f"  讀不到 {path.name}，略過")
                continue
            body, speaker, confidence, bottom, right = _read_regions(frame, profile, engine)
            line = CapturedLine(
                seq=index,
                expected_index=int(path.stem) if bound else -1,
                timestamp=path.stat().st_mtime,
                body_text=body,
                speaker_text=speaker,
                body_conf=confidence,
                screenshot=f"shots/{path.name}",
                touches_bottom=bottom,
                touches_right=right,
            )
            lines.append(line)
            if on_progress:
                on_progress(index + 1, len(shots), line)

    duplicates = partials = 0
    # 綁定模式每張圖已經是一條，合併相鄰重複反而會弄丟正確答案
    if do_clean and not bound:
        lines, duplicates, partials = clean(lines)
    if duplicates or partials:
        print(f"  清理：合併重複 {duplicates} 張、半句 {partials} 張")

    path = session / "lines.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        import json  # noqa: PLC0415

        for line in lines:
            fh.write(json.dumps(line.to_dict(), ensure_ascii=False) + "\n")
    return lines
