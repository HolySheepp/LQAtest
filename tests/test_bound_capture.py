"""把截圖綁定到文本條目。

介面版拍攝時畫面上會高亮「接下來要拍哪一條」，所以每張截圖對應哪一條
是已知的。這讓比對從「模糊比對加序列對齊」變成「逐條對答案」，
也讓使用者可以當場用熱鍵修正拍多或拍少，而不是事後補救。
"""

from __future__ import annotations

import numpy as np
import pytest

from lqa.capture.base import CaptureBackend
from lqa.record.bound import BoundCapture
from lqa.record.store import SessionStore


class FakeCapture(CaptureBackend):
    def __init__(self):
        self.grabs = 0
        self.reason = None

    def region(self):
        return (0, 0, 20, 10)

    def grab(self):
        self.grabs += 1
        return np.full((10, 20, 3), self.grabs, np.uint8)

    def unavailable(self):
        return self.reason


@pytest.fixture
def bound(tmp_path):
    store = SessionStore(tmp_path, "t")
    capture = FakeCapture()
    yield BoundCapture(store, capture, total=5), store, capture
    store.close()


class TestCursor:
    def test_shooting_advances_the_cursor(self, bound):
        b, _, _ = bound
        assert b.shoot() == 0
        assert b.state.cursor == 1

    def test_skip_leaves_the_entry_empty(self, bound):
        b, _, _ = bound
        b.skip()
        assert b.state.cursor == 1
        assert b.state.shots == {}

    def test_back_lets_the_next_shot_overwrite(self, bound):
        """不小心對兩個條目拍到同一畫面時的修正方式。"""
        b, store, _ = bound
        b.shoot()                      # 第 0 條
        b.shoot()                      # 第 1 條（拍錯了）
        first = store.dir / b.state.shots[1]
        b.back()
        assert b.state.cursor == 1
        b.shoot()                      # 覆蓋第 1 條
        assert b.state.cursor == 2
        assert len(b.state.shots) == 2
        assert (store.dir / b.state.shots[1]) == first

    def test_cursor_never_goes_below_zero(self, bound):
        b, _, _ = bound
        b.back()
        b.back()
        assert b.state.cursor == 0

    def test_cursor_stops_at_the_end(self, bound):
        b, _, _ = bound
        for _ in range(8):
            b.shoot()
        assert b.state.cursor == 5
        assert b.state.finished

    def test_shooting_past_the_end_does_nothing(self, bound):
        b, _, capture = bound
        for _ in range(5):
            b.shoot()
        before = capture.grabs
        assert b.shoot() is None
        assert capture.grabs == before

    def test_move_to_jumps_anywhere(self, bound):
        """雙擊條目直接跳過去補拍。"""
        b, _, _ = bound
        b.move_to(3)
        assert b.shoot() == 3


class TestShots:
    def test_file_name_encodes_the_entry_index(self, bound):
        b, _, _ = bound
        b.move_to(2)
        b.shoot()
        assert b.state.shots[2].endswith("00002.png")

    def test_missing_lists_entries_without_a_shot(self, bound):
        b, _, _ = bound
        b.shoot()          # 0
        b.skip()           # 1 留空
        b.shoot()          # 2
        assert b.missing() == [1, 3, 4]

    def test_discard_removes_the_file(self, bound):
        b, store, _ = bound
        b.shoot()
        path = store.dir / b.state.shots[0]
        assert path.exists()
        assert b.discard(0)
        assert not path.exists()
        assert b.missing()[0] == 0

    def test_discard_unknown_entry(self, bound):
        b, _, _ = bound
        assert not b.discard(3)

    def test_unavailable_capture_records_nothing(self, bound):
        b, _, capture = bound
        capture.reason = "視窗已最小化"
        assert b.shoot() is None
        assert b.state.shots == {}
        assert b.state.cursor == 0


class TestBoundCompare:
    """綁定之後比對就是逐條對答案，不再需要序列對齊。"""

    def _lines(self, texts, indices=None):
        from lqa.model import CapturedLine

        indices = indices if indices is not None else range(len(texts))
        return [CapturedLine(seq=i, timestamp=0.0, expected_index=idx, body_text=t)
                for i, (idx, t) in enumerate(zip(indices, texts))]

    def test_each_shot_is_compared_against_its_own_entry(self, sample_xlsx):
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import Category

        expected = load_script(sample_xlsx)
        captured = self._lines([e.target_en for e in expected])
        result = compare(expected, captured)
        assert result.problems == []

    def test_a_skipped_entry_is_reported_as_missing(self, sample_xlsx):
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import Category

        expected = load_script(sample_xlsx)
        keep = [i for i in range(len(expected)) if i != 4]
        captured = self._lines([expected[i].target_en for i in keep], keep)
        result = compare(expected, captured)
        missing = [i for i in result.problems if i.category is Category.MISSING]
        assert len(missing) == 1
        assert missing[0].dialogue_id == expected[4].dialogue_id

    def test_similar_neighbours_are_not_swapped(self, sample_xlsx):
        """逐條配對的關鍵好處：位置綁死，不會被相似句子拉走。"""
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import Category

        expected = load_script(sample_xlsx)
        texts = [e.target_en for e in expected]
        texts[2] = expected[3].target_en      # 第 2 條拍到第 3 條的內容
        captured = self._lines(texts)
        result = compare(expected, captured)
        flagged = {i.dialogue_id for i in result.problems
                   if i.category is Category.MISMATCH}
        assert expected[2].dialogue_id in flagged

    def test_order_issues_are_impossible_when_bound(self, sample_xlsx):
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import Category

        expected = load_script(sample_xlsx)
        captured = self._lines([e.target_en for e in expected])
        result = compare(expected, captured)
        assert not [i for i in result.issues if i.category is Category.ORDER]
