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
from ..compare.normalize import similarity
from ..capture.mss_backend import open_capture
from ..config import MaskConfig, Profile
from ..detect import textmask as tm
from ..detect.linetracker import LineEvent, LineTracker
from ..model import CapturedLine
from ..ocr.base import OcrEngine, OcrResult, build_engine
from .store import SessionStore

LineCallback = Callable[[CapturedLine], None]


def _ocr_input(image: np.ndarray, mask: np.ndarray, cfg: MaskConfig, source: str) -> np.ndarray:
    """依設定決定送進 OCR 的影像。

    預設 masked_gray：用遮罩挖掉背景，但保留文字本身的灰階層次。
    純二值（mask）會把抗鋸齒邊緣砍掉，小字容易糊掉而誤判。

      masked_gray  乾淨背景 + 原本的筆畫。預設，辨識率最好
      mask         純二值。背景極髒時才用
      gray         整塊灰階，完全不遮。字與背景對比夠高時可用
      color        原圖直送
    """
    if source == "color":
        base = image
    elif source == "gray":
        base = tm.to_gray(image)
    elif source == "mask":
        # OCR 模型習慣「白底黑字」，遮罩是白字黑底，要反相
        base = 255 - mask
    else:
        base = tm.masked_value(image, mask, grow=cfg.ocr_grow)
    return tm.upscale_for_ocr(base, cfg.upscale)


class Recorder:
    def __init__(
        self,
        profile: Profile,
        store: SessionStore,
        engine: Optional[OcrEngine] = None,
        capture: Optional[CaptureBackend] = None,
        on_line: Optional[LineCallback] = None,
        repeat_threshold: float = 0.97,
    ):
        profile.validate()
        self.profile = profile
        self.store = store
        self.on_line = on_line
        self._engine = engine or build_engine(profile.ocr.engine, profile.ocr.lang)
        self._capture = capture or open_capture(
            profile.window_title, profile.capture_region, profile.capture_backend
        )
        self._tracker = LineTracker(profile.stability, profile.mask.min_text_pixels)
        self._seq = 0
        self._last_warning: Optional[str] = None
        self.repeat_threshold = repeat_threshold
        self._last_text = ""
        self._repeats = 0

    def _read(self, image: np.ndarray, mask: np.ndarray, cfg: MaskConfig) -> OcrResult:
        return self._engine.read(_ocr_input(image, mask, cfg, self.profile.ocr.source))

    def _is_repeat(self, line: CapturedLine, blanked_before: bool) -> bool:
        """同一句被重複擷取。

        遮罩層的去重擋不掉所有情況：變動比例的分母是文字量，
        短句的遮罩只有一千多像素，抗鋸齒抖動就可能被當成新的一句。
        比對 OCR 文字是最直接的判準 —— 字一樣就是同一句。

        例外是對白框中間淨空過（過場、黑幕），那代表劇情真的又講了一次，
        這種重複要保留。
        """
        if not self._last_text or blanked_before:
            return False
        if similarity(self._last_text, line.body_text) < self.repeat_threshold:
            return False
        self._repeats += 1
        return True

    def _capture_line(self, event: LineEvent) -> CapturedLine:
        p = self.profile
        frame = event.frame
        body_img = tm.crop(frame, p.body_roi)
        body_mask = event.mask
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
            samples=event.samples,
            still_growing=event.grew_until_end,
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

                # 視窗被最小化時抓到的是垃圾畫面。跳過而不是中斷錄製，
                # 使用者還原視窗後就能接著錄，前面錄到的也不會遺失。
                reason = self._capture.unavailable()
                if reason:
                    if reason != self._last_warning:
                        self._last_warning = reason
                        print(f"[暫停] {reason}")
                    self._tracker.reset()
                    time.sleep(interval)
                    continue
                if self._last_warning:
                    self._last_warning = None
                    print("[繼續] 已重新抓到畫面")

                body_img = tm.crop(frame, p.body_roi)
                mask = tm.build_mask(body_img, p.mask)
                self._emit(self._tracker.feed(frame, mask))

                elapsed = time.perf_counter() - loop_start
                time.sleep(max(0.0, interval - elapsed))
        except KeyboardInterrupt:
            pass
        finally:
            # 最後一句還壓在追蹤器裡等下一句，沒有 flush 就會遺失
            self._emit(self._tracker.flush())
            self._capture.close()

        if self._repeats:
            print(f"（已略過 {self._repeats} 次重複觸發）")
        return self._seq

    def _emit(self, event: Optional[LineEvent]) -> None:
        if event is None:
            return
        line = self._capture_line(event)
        if not line.body_text.strip():
            return
        if self._is_repeat(line, event.blanked_before):
            return
        self.store.append(line)
        self._seq += 1
        self._last_text = line.body_text
        if self.on_line:
            self.on_line(line)
