"""手動截圖流程。

自動偵測「打字結束」沒有可靠訊號：試過等畫面靜止、看變動比例、
看筆畫消失量，每一種都在某類句子上失敗 —— 短句沒有靜止期、
刪節號的停頓偽裝成結束。使用者的眼睛有這個訊號，程式沒有，
所以改由使用者按鍵決定。

離線辨識時要處理兩種多餘的截圖：
  重複  連按兩次或畫面沒推進
  半句  打字中途按下，或先點一下跳過動畫再點一次推進
"""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from lqa.capture.base import CaptureBackend
from lqa.config import MaskConfig, OcrConfig, Profile
from lqa.model import CapturedLine
from lqa.ocr.base import OcrEngine, OcrResult
from lqa.record.reader import clean, is_partial_of


def line(text: str, seq: int = 0) -> CapturedLine:
    return CapturedLine(seq=seq, timestamp=0.0, body_text=text)


class TestIsPartialOf:
    def test_prefix_is_partial(self):
        assert is_partial_of("Though..", "Though... thanks for helping me.")

    def test_complete_line_is_not_partial_of_itself(self):
        assert not is_partial_of("It was nothing.", "It was nothing.")

    def test_different_lines_are_not_partial(self):
        assert not is_partial_of("Found it.", "It was nothing.")

    def test_punctuation_only_growth_is_not_enough(self):
        """差一兩個字元可能只是 OCR 雜訊，不該當成半句。"""
        assert not is_partial_of("Take it easy", "Take it easy.")

    def test_later_shorter_is_not_partial(self):
        assert not is_partial_of("A longer sentence here", "A longer")

    def test_empty_strings(self):
        assert not is_partial_of("", "anything")
        assert not is_partial_of("anything", "")


class TestClean:
    def test_adjacent_duplicates_are_merged(self):
        kept, dupes, partials = clean([line("Same line"), line("Same line")])
        assert [c.body_text for c in kept] == ["Same line"]
        assert (dupes, partials) == (1, 0)

    def test_partial_is_replaced_by_the_complete_one(self):
        kept, dupes, partials = clean([
            line("Though.."),
            line("Though... thanks for helping me through all this."),
        ])
        assert kept[0].body_text.endswith("all this.")
        assert (dupes, partials) == (0, 1)

    def test_distinct_lines_are_all_kept(self):
        texts = ["Found it.", "A total score of 328.", "Congratulations."]
        kept, dupes, partials = clean([line(t) for t in texts])
        assert [c.body_text for c in kept] == texts
        assert (dupes, partials) == (0, 0)

    def test_non_adjacent_repeat_is_kept(self):
        """劇情真的又講了一次同樣的話，中間隔著別句就不該合併。"""
        texts = ["Nev...", "Found it.", "Nev..."]
        kept, _, _ = clean([line(t) for t in texts])
        assert len(kept) == 3

    def test_sequence_numbers_are_renumbered(self):
        kept, _, _ = clean([line("A", 0), line("A", 1), line("B", 2)])
        assert [c.seq for c in kept] == [0, 1]

    def test_ocr_noise_counts_as_duplicate(self):
        kept, dupes, _ = clean([line("Take it easy."), line("Take it easy")])
        assert len(kept) == 1
        assert dupes == 1

    def test_empty_input(self):
        assert clean([]) == ([], 0, 0)


class FakeCapture(CaptureBackend):
    def __init__(self):
        self.grabs = 0
        self.reason = None

    def region(self):
        return (0, 0, 40, 20)

    def grab(self):
        self.grabs += 1
        return np.full((20, 40, 3), self.grabs, np.uint8)

    def unavailable(self):
        return self.reason


class FakeKeys:
    """代替真的按鍵，照腳本吐出按鍵事件。"""

    def __init__(self, script: list[list[str]]):
        self.script = list(script)

    def pressed(self):
        return self.script.pop(0) if self.script else []


@pytest.fixture
def profile():
    return Profile(
        name="t", window_title=None, capture_region=(0, 0, 40, 20),
        body_roi=(0, 0, 40, 20), mask=MaskConfig(min_text_pixels=1),
        ocr=OcrConfig(),
    )


class TestShooter:
    def _run(self, profile, tmp_path, script):
        from lqa.record import shooter as shooter_module
        from lqa.record.shooter import Shooter
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        capture = FakeCapture()
        keys = FakeKeys(script)
        shooter_module.KeyWatcher = lambda _names: keys

        s = Shooter(profile, store, capture=capture, poll_interval_ms=1)
        stop = threading.Event()

        def stopper():
            while keys.script:
                time.sleep(0.002)
            time.sleep(0.02)
            stop.set()

        thread = threading.Thread(target=stopper, daemon=True)
        thread.start()
        try:
            total = s.run(stop_event=stop)
        finally:
            store.close()
            thread.join(timeout=1)
        return total, store

    def test_each_press_saves_one_shot(self, profile, tmp_path):
        total, store = self._run(profile, tmp_path, [["f9"], [], ["f9"], ["f9"]])
        assert total == 3
        assert len(store.list_shots()) == 3

    def test_undo_removes_the_last_shot(self, profile, tmp_path):
        total, store = self._run(profile, tmp_path, [["f9"], ["f9"], ["f10"]])
        assert total == 1
        assert len(store.list_shots()) == 1

    def test_undo_with_nothing_to_undo_is_harmless(self, profile, tmp_path):
        total, store = self._run(profile, tmp_path, [["f10"], ["f9"]])
        assert total == 1

    def test_shots_are_png_for_lossless_ocr(self, profile, tmp_path):
        _, store = self._run(profile, tmp_path, [["f9"]])
        assert store.list_shots()[0].suffix == ".png"

    def test_unavailable_capture_is_skipped(self, profile, tmp_path):
        from lqa.record import shooter as shooter_module
        from lqa.record.shooter import Shooter
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        capture = FakeCapture()
        capture.reason = "視窗已最小化"
        shooter_module.KeyWatcher = lambda _names: FakeKeys([["f9"]])
        s = Shooter(profile, store, capture=capture, poll_interval_ms=1)
        assert s.shoot() is None
        assert s.count == 0
        store.close()


class ConstantEngine(OcrEngine):
    """依序回傳腳本裡的文字。測試用的 profile 沒有姓名框，所以一張圖一次呼叫。"""

    def __init__(self, texts: list[str]):
        self.texts = texts
        self.calls = 0

    def read(self, image):
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return OcrResult(text=text, confidence=0.9)


class TestReadSession:
    def test_reads_every_shot_and_writes_lines(self, profile, tmp_path):
        from lqa.record.reader import read_session
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "t")
        for i in range(3):
            store.save_shot(np.full((20, 40, 3), 254, np.uint8), i)
        store.write_meta({"profile": profile.to_dict()})
        store.close()

        engine = ConstantEngine(["One.", "Two.", "Three."])
        lines = read_session(store.dir, profile=profile, engine=engine)
        assert [c.body_text for c in lines] == ["One.", "Two.", "Three."]
        assert (store.dir / "lines.jsonl").exists()

    def test_missing_shots_raises(self, tmp_path, profile):
        from lqa.record.reader import read_session
        from lqa.record.store import SessionStore

        store = SessionStore(tmp_path, "empty")
        store.close()
        with pytest.raises(FileNotFoundError):
            read_session(store.dir, profile=profile, engine=ConstantEngine(["x"]))
