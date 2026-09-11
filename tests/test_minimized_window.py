"""視窗最小化的處理。

最小化的視窗 GetClientRect 會回出 (-32000, -32000) 這類垃圾座標，
照抓的話會擷取到完全無關的畫面，而且沒有任何錯誤訊息。
錄製途中如果不小心最小化，整段就會默默錄成垃圾。
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lqa.capture.base import CaptureBackend
from lqa.capture.window import WindowMinimized
from lqa.config import MaskConfig, OcrConfig, Profile, StabilityConfig
from lqa.ocr.base import OcrEngine, OcrResult


class FakeCapture(CaptureBackend):
    """可以切換「最小化」狀態的假擷取後端。"""

    def __init__(self, frame: np.ndarray):
        self.frame = frame
        self.reason: str | None = None
        self.grabs = 0

    def region(self):
        return (0, 0, self.frame.shape[1], self.frame.shape[0])

    def grab(self) -> np.ndarray:
        self.grabs += 1
        return self.frame

    def unavailable(self):
        return self.reason


class FakeEngine(OcrEngine):
    def __init__(self):
        self.reads = 0

    def read(self, image):
        self.reads += 1
        return OcrResult(text="hello", confidence=0.99)


@pytest.fixture
def profile():
    return Profile(
        name="t",
        window_title=None,
        capture_region=(0, 0, 200, 100),
        body_roi=(0, 0, 200, 100),
        mask=MaskConfig(method="value", min_text_pixels=10, upscale=1),
        stability=StabilityConfig(poll_interval_ms=1, stable_frames=2, min_gap_ms=0),
        ocr=OcrConfig(),
    )


def bright_frame() -> np.ndarray:
    frame = np.full((100, 200, 3), 10, np.uint8)
    frame[40:60, 20:180] = 254
    return frame


class TestExceptionType:
    def test_minimized_is_a_runtime_error_subclass(self):
        """CLI 既有的錯誤處理是接 RuntimeError，不能漏接。"""
        assert issubclass(WindowMinimized, RuntimeError)


class TestBackendContract:
    def test_default_backend_reports_available(self):
        cap = FakeCapture(bright_frame())
        assert cap.unavailable() is None


class TestRecorderSkipsUnavailableFrames:
    def _run(self, profile, capture, tmp_path, frames: int):
        from lqa.record.recorder import Recorder
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        engine = FakeEngine()
        recorder = Recorder(profile, store, engine=engine, capture=capture)
        stop = threading.Event()

        def stopper():
            while capture.grabs < frames:
                time.sleep(0.005)
            stop.set()

        thread = threading.Thread(target=stopper, daemon=True)
        thread.start()
        try:
            recorder.run(stop_event=stop, max_seconds=5)
        finally:
            store.close()
            thread.join(timeout=1)
        return engine, store

    def test_no_ocr_while_window_is_minimized(self, profile, tmp_path):
        capture = FakeCapture(bright_frame())
        capture.reason = "視窗已最小化"
        engine, store = self._run(profile, capture, tmp_path, frames=10)
        assert engine.reads == 0, "最小化期間不該做任何 OCR"
        assert store.count == 0, "最小化期間不該寫入任何句子"

    def test_records_normally_when_available(self, profile, tmp_path):
        capture = FakeCapture(bright_frame())
        engine, store = self._run(profile, capture, tmp_path, frames=10)
        assert engine.reads >= 1
        assert store.count >= 1

    def test_resumes_after_window_is_restored(self, profile, tmp_path, capsys):
        capture = FakeCapture(bright_frame())
        capture.reason = "視窗已最小化"

        from lqa.record.recorder import Recorder
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        engine = FakeEngine()
        recorder = Recorder(profile, store, engine=engine, capture=capture)
        stop = threading.Event()

        def restore_then_stop():
            while capture.grabs < 5:
                time.sleep(0.005)
            capture.reason = None            # 使用者把視窗還原
            while capture.grabs < 20:
                time.sleep(0.005)
            stop.set()

        thread = threading.Thread(target=restore_then_stop, daemon=True)
        thread.start()
        try:
            recorder.run(stop_event=stop, max_seconds=5)
        finally:
            store.close()
            thread.join(timeout=1)

        out = capsys.readouterr().out
        assert "[暫停]" in out
        assert "[繼續]" in out
        assert store.count >= 1, "還原之後應該要能繼續錄到句子"
