"""錄製階段：只負責把畫面上的文字一句一句記下來，不做任何比對。

這樣切分是刻意的 —— 一邊玩一邊比對會有兩個問題：
  1. 比對需要「整段跑完」才能判斷順序與缺句
  2. 即時比對失敗時很難回頭重跑

所以錄製只管記錄，比對留到離線階段，可以反覆重跑、調參數而不用重玩一次。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np

from ..capture.base import CaptureBackend
from ..capture.mss_backend import open_capture
from ..config import MaskConfig, Profile
from ..detect import textmask as tm
from ..detect.stability import StabilityTracker
from ..model import CapturedLine
from ..ocr.base import OcrEngine, OcrResult, build_engine
from .store import SessionStore

LineCallback = Callable[[CapturedLine], None]


def _ocr_input(image: np.ndarray, mask: np.ndarray, cfg: MaskConfig, source: str) -> np.ndarray:
    """依設定決定送進 OCR 的影像。

    預設用二值遮罩：對白框半透明、背景會動，二值化後乾淨很多。
    若某些字體在二值化後反而破碎，可改成 gray 或 color。
    """
    if source == "color":
        base = image
    elif source == "gray":
        base = tm.to_gray(image)
    else:
        # OCR 模型習慣「白底黑字」，遮罩是白字黑底，要反相
        base = 255 - mask
    return tm.upscale_for_ocr(base, cfg.upscale)


class Recorder:
    def __init__(
        self,
        profile: Profile,
        store: SessionStore,
        engine: Optional[OcrEngine] = None,
        capture: Optional[CaptureBackend] = None,
        on_line: Optional[LineCallback] = None,
    ):
        profile.validate()
        self.profile = profile
        self.store = store
        self.on_line = on_line
        self._engine = engine or build_engine(profile.ocr.engine, profile.ocr.lang)
        self._capture = capture or open_capture(
            profile.window_title, profile.capture_region
        )
        self._tracker = StabilityTracker(profile.stability, profile.mask.min_text_pixels)
        self._seq = 0

    def _read(self, image: np.ndarray, mask: np.ndarray, cfg: MaskConfig) -> OcrResult:
        return self._engine.read(_ocr_input(image, mask, cfg, self.profile.ocr.source))

    def _capture_line(self, frame: np.ndarray, stable_ms: int) -> CapturedLine:
        p = self.profile
        body_img = tm.crop(frame, p.body_roi)
        body_mask = tm.build_mask(body_img, p.mask)
        body = self._read(body_img, body_mask, p.mask)

        speaker_text = ""
        if p.speaker_roi:
            spk_cfg = p.effective_speaker_mask()
            spk_img = tm.crop(frame, p.speaker_roi)
            spk_mask = tm.build_mask(spk_img, spk_cfg)
            if tm.text_pixel_count(spk_mask) >= max(8, spk_cfg.min_text_pixels // 4):
                speaker_text = self._read(spk_img, spk_mask, spk_cfg).text

        bottom, right = tm.touches_edges(body_mask, p.ocr.edge_margin_px)
        line = CapturedLine(
            seq=self._seq,
            timestamp=time.time(),
            body_text=body.text,
            speaker_text=speaker_text,
            body_conf=body.confidence,
            stable_ms=stable_ms,
            touches_bottom=bottom,
            touches_right=right,
        )
        line.screenshot = self.store.save_screenshot(
            frame, self._seq, p.ocr.screenshot_quality
        )
        return line

    def run(
        self,
        max_lines: Optional[int] = None,
        max_seconds: Optional[float] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> int:
        """主迴圈。回傳錄到的句數。以 Ctrl+C 或 stop_event 結束。"""
        p = self.profile
        interval = p.stability.poll_interval_ms / 1000.0
        started = time.time()

        self.store.write_meta(
            {
                "profile": p.to_dict(),
                "capture_region": list(self._capture.region()),
            }
        )

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                if max_seconds is not None and time.time() - started > max_seconds:
                    break
                if max_lines is not None and self._seq >= max_lines:
                    break

                loop_start = time.perf_counter()
                frame = self._capture.grab()
                body_img = tm.crop(frame, p.body_roi)
                mask = tm.build_mask(body_img, p.mask)

                event = self._tracker.feed(mask, time.time() * 1000.0)
                if event is not None:
                    line = self._capture_line(frame, event.stable_ms)
                    if line.body_text.strip():
                        self.store.append(line)
                        self._seq += 1
                        if self.on_line:
                            self.on_line(line)

                elapsed = time.perf_counter() - loop_start
                time.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            pass
        finally:
            self._capture.close()

        return self._seq
