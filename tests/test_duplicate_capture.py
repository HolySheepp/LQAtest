"""同一句被重複擷取的防護。

實測發現的機制：變動比例的分母是文字像素量，短句的遮罩只有一千多像素，
光是抗鋸齒邊緣抖動就會讓比例衝到 3% 以上，逼近 0.04 的門檻，
於是被誤判成「畫面變了」而重新觸發，同一句記錄兩次。
（實測資料：含頭像的左半 8102 px 變動 0.0014，純文字的右半 1029 px 變動 0.0327。）

兩層防護：
  1. 變動要同時滿足比例與絕對像素量，擋掉小分母造成的假變動
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
from lqa.detect.stability import StabilityTracker
from lqa.ocr.base import OcrEngine, OcrResult

CFG = MaskConfig(method="value", bright_threshold=170, min_text_pixels=40, upscale=1)


def mask_with(pixels: int, noise: int = 0, seed: int = 0) -> np.ndarray:
    """做一張含指定前景量的遮罩，noise 指定要翻轉幾個邊緣像素。"""
    mask = np.zeros((100, 400), np.uint8)
    flat = mask.reshape(-1)
    flat[:pixels] = 255
    if noise:
        rng = np.random.default_rng(seed)
        idx = rng.choice(np.arange(pixels, pixels + 4000), size=noise, replace=False)
        flat[idx] = 255
    return mask


class TestSmallMaskNoise:
    """短句的遮罩很小，比例會被放大。"""

    def feed_all(self, tracker, masks):
        fired = 0
        for i, m in enumerate(masks):
            if tracker.feed(m, i * 100.0) is not None:
                fired += 1
        return fired

    # 短句：600 像素的遮罩，抖動 30 像素 -> 比例 5% 已超過 0.04 門檻，
    # 但絕對量只有 30。真實資料裡純文字區只有 1029 像素，就是這個量級。
    SHORT_LINE = 600
    FLICKER = 30

    def frames(self):
        base = mask_with(self.SHORT_LINE)
        shifted = mask_with(self.SHORT_LINE, noise=self.FLICKER, seed=1)
        return [base] * 4 + [shifted] * 8      # 抖動後停在新的穩定狀態

    def test_edge_flicker_on_a_short_line_does_not_retrigger(self):
        cfg = StabilityConfig(stable_frames=2, min_gap_ms=0, min_changed_pixels=40)
        tracker = StabilityTracker(cfg, CFG.min_text_pixels)
        assert self.feed_all(tracker, self.frames()) == 1

    def test_ratio_alone_would_have_retriggered(self):
        """對照組：沒有絕對量下限的話，同樣的抖動就會記錄第二次。"""
        cfg = StabilityConfig(stable_frames=2, min_gap_ms=0, min_changed_pixels=1)
        tracker = StabilityTracker(cfg, CFG.min_text_pixels)
        assert self.feed_all(tracker, self.frames()) == 2

    def test_a_real_new_line_still_fires(self):
        cfg = StabilityConfig(stable_frames=2, min_gap_ms=0, min_changed_pixels=40)
        tracker = StabilityTracker(cfg, CFG.min_text_pixels)
        frames = [mask_with(self.SHORT_LINE)] * 4 + [mask_with(2500)] * 4
        assert self.feed_all(tracker, frames) == 2

    def test_blank_state_is_reported_on_the_event(self):
        """旗標在觸發當下就會清掉，所以必須隨事件傳遞。"""
        cfg = StabilityConfig(stable_frames=2, min_gap_ms=0, min_changed_pixels=40)
        tracker = StabilityTracker(cfg, CFG.min_text_pixels)
        events = []
        frames = ([mask_with(2000)] * 4 + [mask_with(0)] * 4 + [mask_with(2000)] * 4)
        for i, m in enumerate(frames):
            event = tracker.feed(m, i * 100.0)
            if event is not None:
                events.append(event)
        assert len(events) == 2
        assert events[0].blanked_before is True      # 錄製開始前視同空白
        assert events[1].blanked_before is True      # 中間確實淨空過


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


def frame_with(pixels: int) -> np.ndarray:
    frame = np.zeros((100, 400, 3), np.uint8)
    flat = frame.reshape(-1, 3)
    flat[:pixels] = (254, 254, 254)
    return frame


@pytest.fixture
def profile():
    return Profile(
        name="t", window_title=None, capture_region=(0, 0, 400, 100),
        body_roi=(0, 0, 400, 100), mask=CFG,
        stability=StabilityConfig(poll_interval_ms=1, stable_frames=2,
                                  min_gap_ms=0, min_changed_pixels=40),
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
        big, bigger = frame_with(3000), frame_with(6000)
        frames = [big] * 5 + [bigger] * 5
        store, recorder = self._record(profile, tmp_path, frames,
                                       ["Same line", "Same line"])
        assert store.count == 1

    def test_different_text_is_recorded_twice(self, profile, tmp_path):
        big, bigger = frame_with(3000), frame_with(6000)
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
        big, bigger = frame_with(3000), frame_with(6000)
        frames = [big] * 5 + [bigger] * 5
        store, _ = self._record(profile, tmp_path, frames,
                                ["Take it easy.", "Take it easy"])
        assert store.count == 1
