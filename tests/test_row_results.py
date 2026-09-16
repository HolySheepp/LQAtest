"""條目表的結果攤平、篩選與跳轉。

這幾段決定使用者在表格裡看到什麼、上下鍵會跳到哪，
寫錯了是「有問題的句子被藏起來」這種靜默錯誤，所以獨立測。
"""

from __future__ import annotations

from lqa.compare.classify import CompareResult
from lqa.gui.results import (ALL, FLAGGED, NOT_CAPTURED, RowResult, is_visible,
                             next_flagged, rows_from_result)
from lqa.model import CapturedLine, Category, ExpectedLine, Issue


def line(order: int, text: str = "hello") -> ExpectedLine:
    return ExpectedLine(order=order, dialogue_id=f"8020{order:04d}", target_en=text)


def result_with(*pairs: tuple[ExpectedLine, list[Issue]]) -> CompareResult:
    expected = [exp for exp, _ in pairs]
    issues = [issue for _, group in pairs for issue in group]
    return CompareResult(issues=issues, expected=expected, captured=[])


def issue(category: Category, exp: ExpectedLine, body: str = "",
          detail: str = "") -> Issue:
    captured = CapturedLine(seq=0, timestamp=0.0, body_text=body) if body else None
    return Issue(category=category, expected=exp, captured=captured, detail=detail)


class TestRowsFromResult:
    def test_each_issue_lands_on_its_own_row(self):
        a, b = line(0), line(1)
        rows = rows_from_result(result_with(
            (a, [issue(Category.PASS, a)]),
            (b, [issue(Category.MISMATCH, b)]),
        ))
        assert rows[0].label == "一致"
        assert rows[1].label == "不一致"

    def test_speaker_issue_joins_the_same_row(self):
        """發話者錯誤是獨立追加的，不能自己佔一列。"""
        a = line(0)
        rows = rows_from_result(result_with(
            (a, [issue(Category.PASS, a), issue(Category.SPEAKER, a)])))
        assert rows[0].label == "發話者錯誤"
        assert rows[0].flagged

    def test_pass_only_row_is_not_flagged(self):
        a = line(0)
        rows = rows_from_result(result_with((a, [issue(Category.PASS, a)])))
        assert not rows[0].flagged

    def test_duplicate_dialogue_ids_do_not_collide(self):
        """同一個頁簽裡ID可能重複，掛錯列會把結果寫到別人身上。"""
        a, b = line(0), line(1)
        b.dialogue_id = a.dialogue_id
        rows = rows_from_result(result_with(
            (a, [issue(Category.PASS, a, body="one")]),
            (b, [issue(Category.MISMATCH, b, body="two")]),
        ))
        assert rows[0].captured.body_text == "one"
        assert rows[1].captured.body_text == "two"

    def test_extra_has_no_row_to_land_on(self):
        """畫面有、文本沒有 —— 沒有對應的列，不能硬塞。"""
        extra = Issue(category=Category.EXTRA, expected=None,
                      captured=CapturedLine(seq=0, timestamp=0.0))
        assert rows_from_result(
            CompareResult(issues=[extra], expected=[], captured=[])) == {}

    def test_detail_keeps_only_real_problems(self):
        a = line(0)
        rows = rows_from_result(result_with((a, [
            issue(Category.PASS, a, detail="一致"),
            issue(Category.SPEAKER, a, detail="畫面是 Cyan，文本是 Mint"),
        ])))
        assert rows[0].detail == "畫面是 Cyan，文本是 Mint"


class TestNotCaptured:
    def test_not_captured_is_not_a_flag(self):
        """使用者自己跳過的不算疑慮，混進去會把真正要看的淹掉。"""
        a = line(0)
        rows = rows_from_result(result_with(
            (a, [issue(Category.NOT_CAPTURED, a)])))
        assert rows[0].not_captured
        assert not rows[0].flagged


class TestFilter:
    def setup_method(self):
        self.rows = {
            0: RowResult([Issue(category=Category.PASS)]),
            1: RowResult([Issue(category=Category.TRUNCATED)]),
            2: RowResult([Issue(category=Category.NOT_CAPTURED)]),
        }

    def test_all_shows_everything_including_unanalysed(self):
        assert is_visible(self.rows[0], ALL)
        assert is_visible(None, ALL)

    def test_flagged_shows_only_problems(self):
        assert not is_visible(self.rows[0], FLAGGED)
        assert is_visible(self.rows[1], FLAGGED)
        assert not is_visible(self.rows[2], FLAGGED)

    def test_not_captured_filter_shows_only_gaps(self):
        assert not is_visible(self.rows[1], NOT_CAPTURED)
        assert is_visible(self.rows[2], NOT_CAPTURED)

    def test_unanalysed_row_is_hidden_when_filtering(self):
        assert not is_visible(None, FLAGGED)


class TestJumpBetweenFlagged:
    def setup_method(self):
        self.rows = {
            0: RowResult([Issue(category=Category.PASS)]),
            1: RowResult([Issue(category=Category.MISMATCH)]),
            2: RowResult([Issue(category=Category.PASS)]),
            3: RowResult([Issue(category=Category.NOT_CAPTURED)]),
            4: RowResult([Issue(category=Category.TRUNCATED)]),
        }

    def test_skips_over_clean_and_uncaptured_rows(self):
        assert next_flagged(1, 5, self.rows, 1) == 4

    def test_goes_backwards(self):
        assert next_flagged(4, 5, self.rows, -1) == 1

    def test_stays_put_at_the_last_one(self):
        """沒有下一條就停在原地，不要繞回開頭讓人以為還有。"""
        assert next_flagged(4, 5, self.rows, 1) == 4

    def test_no_selection_starts_from_the_top(self):
        assert next_flagged(-1, 5, self.rows, 1) == 1

    def test_no_selection_going_up_starts_from_the_bottom(self):
        assert next_flagged(-1, 5, self.rows, -1) == 4

    def test_nothing_flagged_stays_put(self):
        rows = {0: RowResult([Issue(category=Category.PASS)])}
        assert next_flagged(0, 1, rows, 1) == 0


class TestShotName:
    """哪一條要顯示哪張截圖。

    解析前也要看得到 —— 拍完馬上點條目確認拍到什麼是最自然的動作。
    這裡失敗的症狀是「截圖那格永遠空白」，不會報錯，所以獨立測。
    """

    def setup_method(self):
        from lqa.gui.results import shot_name

        self.shot_name = shot_name
        self.shots = {0: "shots/00000.png", 2: "shots/00002.png"}

    def test_falls_back_to_the_file_on_disk_before_analysis(self):
        assert self.shot_name(None, self.shots, 0) == "shots/00000.png"

    def test_uses_the_recorded_path_after_analysis(self):
        captured = CapturedLine(seq=0, timestamp=0.0, screenshot="shots/00007.png")
        assert self.shot_name(captured, self.shots, 0) == "shots/00007.png"

    def test_recorded_line_without_a_shot_still_falls_back(self):
        """紀錄裡沒帶截圖路徑（舊 session）時不該就這樣放棄。"""
        captured = CapturedLine(seq=0, timestamp=0.0)
        assert self.shot_name(captured, self.shots, 2) == "shots/00002.png"

    def test_nothing_for_an_entry_that_was_never_shot(self):
        assert self.shot_name(None, self.shots, 1) == ""

    def test_no_shots_at_all(self):
        assert self.shot_name(None, {}, 0) == ""
