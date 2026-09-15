"""同一句被重複擷取的防護。

換句的判準是「舊筆畫消失了多少」，而消失量的比例分母是文字像素量，
短句的遮罩只有一千多像素，抗鋸齒抖動就可能讓比例衝過門檻。
（實測資料：含頭像的左半 8102 px 變動 0.0014，純文字的右半 1029 px 變動 0.0327。）

兩層防護：
  1. 消失量要同時滿足比例與絕對像素量，擋掉小分母造成的假換句
  2. 擷取後比對 OCR 文字，字一樣就是同一句
中間若對白框淨空過，代表劇情真的又講了一次，那種重複要保留。
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lqa.capture.base import CaptureBackend
from lqa.config import MaskConfig, OcrConfig, Profile, StabilityConfig
from lqa.detect.linetracker import LineTracker
from lqa.ocr.base import OcrEngine, OcrResult

CFG = MaskConfig(method="value", bright_threshold=170, min_text_pixels=40, upscale=1)


def mask_with(pixels: int, offset: int = 0) -> np.ndarray:
    """做一張含指定前景量的遮罩。offset 讓前景落在不同位置，模擬換成另一句。"""
    mask = np.zeros((100, 400), np.uint8)
    flat = mask.reshape(-1)
    flat[offset:offset + pixels] = 255
    return mask


class TestFlickerDoesNotSplitLines:
    """短句的遮罩很小，抗鋸齒抖動很容易被誤判成換句。

    實測資料：含頭像的左半 8102 像素只變動 0.0014，
    純文字的右半 1029 像素卻變動到 0.0327。
    現在的判準看的是「舊筆畫消失了多少」，並且比例與絕對量要同時達標。
    """

    SHORT_LINE = 600
    FLICKER = 30

    def feed_all(self, tracker, masks):
        frame = np.zeros((10, 10, 3), np.uint8)
        events = [tracker.feed(frame, m) for m in masks]
        events = [e for e in events if e is not None]
        final = tracker.flush()
        if final is not None:
            events.append(final)
        return len(events)

    def test_edge_flicker_on_a_short_line_does_not_split(self):
        cfg = StabilityConfig(min_changed_pixels=40)
        tracker = LineTracker(cfg, CFG.min_text_pixels)
        base = mask_with(self.SHORT_LINE)
        shifted = mask_with(self.SHORT_LINE - self.FLICKER)   # 少了 30 個像素
        assert self.feed_all(tracker, [base] * 4 + [shifted] * 4) == 1

    def test_absolute_floor_is_what_saves_it(self):
        """對照組：沒有絕對量下限的話，30 像素就超過 25% 比例以外的保護。"""
        cfg = StabilityConfig(min_changed_pixels=1, line_change_ratio=0.02)
        tracker = LineTracker(cfg, CFG.min_text_pixels)
        base = mask_with(self.SHORT_LINE)
        shifted = mask_with(self.SHORT_LINE - self.FLICKER)
        assert self.feed_all(tracker, [base] * 4 + [shifted] * 4) == 2

    def test_a_real_new_line_still_splits(self):
        cfg = StabilityConfig(min_changed_pixels=40)
        tracker = LineTracker(cfg, CFG.min_text_pixels)
        frames = [mask_with(self.SHORT_LINE)] * 4 + [mask_with(2500, offset=4000)] * 4
        assert self.feed_all(tracker, frames) == 2

    def test_blank_state_is_reported_on_the_event(self):
        cfg = StabilityConfig(min_changed_pixels=40)
        tracker = LineTracker(cfg, CFG.min_text_pixels)
        frame = np.zeros((10, 10, 3), np.uint8)
        masks = [mask_with(2000)] * 3 + [mask_with(0)] * 3 + [mask_with(2000)] * 3
        events = [e for e in (tracker.feed(frame, m) for m in masks) if e is not None]
        events.append(tracker.flush())
        assert len(events) == 2
        assert all(e.blanked_before for e in events)


class FakeCapture(CaptureBackend):
    def __init__(self, frames):
        self.frames = frames
        self.index = 0

    def region(self):
        return (0, 0, 400, 100)

    def grab(self):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return frame


class ScriptedEngine(OcrEngine):
    """依畫面亮度總量決定回傳哪一句，用來模擬同句/不同句。"""

    def __init__(self, texts: dict[int, str]):
        self.texts = texts

    def read(self, image):
        key = int(image.sum() // 100000)
        return OcrResult(text=self.texts.get(key, "unknown"), confidence=0.99)


def frame_with(pixels: int, offset: int = 0) -> np.ndarray:
    """offset 讓亮區落在不同位置，這樣前後兩幀才會被判定成換了一句。"""
    frame = np.zeros((100, 400, 3), np.uint8)
    flat = frame.reshape(-1, 3)
    flat[offset:offset + pixels] = (254, 254, 254)
    return frame


@pytest.fixture
def profile():
    return Profile(
        name="t", window_title=None, capture_region=(0, 0, 400, 100),
        body_roi=(0, 0, 400, 100), mask=CFG,
        stability=StabilityConfig(poll_interval_ms=1, min_changed_pixels=40),
        ocr=OcrConfig(source="mask"),
    )


class FixedTextEngine(OcrEngine):
    def __init__(self, texts: list[str]):
        self.texts = texts
        self.calls = 0

    def read(self, image):
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return OcrResult(text=text, confidence=0.99)


class TestTextLevelDedup:
    def _record(self, profile, tmp_path, frames, texts):
        from lqa.record.recorder import Recorder
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        capture = FakeCapture(frames)
        engine = FixedTextEngine(texts)
        recorder = Recorder(profile, store, engine=engine, capture=capture)
        stop = threading.Event()

        def stopper():
            while capture.index < len(frames):
                time.sleep(0.005)
            stop.set()

        thread = threading.Thread(target=stopper, daemon=True)
        thread.start()
        try:
            recorder.run(stop_event=stop, max_seconds=5)
        finally:
            store.close()
            thread.join(timeout=1)
        return store, recorder

    def test_same_text_twice_is_recorded_once(self, profile, tmp_path):
        """遮罩因為抖動而被判成新畫面，但 OCR 讀到的是同一句。"""
        big, bigger = frame_with(3000), frame_with(3000, offset=5000)
        frames = [big] * 5 + [bigger] * 5
        store, recorder = self._record(profile, tmp_path, frames,
                                       ["Same line", "Same line"])
        assert store.count == 1

    def test_different_text_is_recorded_twice(self, profile, tmp_path):
        big, bigger = frame_with(3000), frame_with(3000, offset=5000)
        frames = [big] * 5 + [bigger] * 5
        store, _ = self._record(profile, tmp_path, frames,
                                ["First line", "Second line"])
        assert store.count == 2

    def test_repeat_after_a_blank_box_is_kept(self, profile, tmp_path):
        """劇情真的連著講兩次一樣的話：中間對白框會淨空，那種要保留。"""
        big = frame_with(3000)
        blank = frame_with(0)
        frames = [big] * 5 + [blank] * 5 + [big] * 6
        store, _ = self._record(profile, tmp_path, frames,
                                ["Nev...", "Nev..."])
        assert store.count == 2

    def test_near_identical_text_counts_as_repeat(self, profile, tmp_path):
        """OCR 有雜訊，差一兩個標點仍算同一句。"""
        big, bigger = frame_with(3000), frame_with(3000, offset=5000)
        frames = [big] * 5 + [bigger] * 5
        store, _ = self._record(profile, tmp_path, frames,
                                ["Take it easy.", "Take it easy"])
        assert store.count == 1
