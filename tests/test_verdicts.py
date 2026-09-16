"""人工判定：自動判定難免有誤報，人看過之後的結論才是最終答案。

失敗的症狀分兩種，都很難自己發現：標了沒存（重開軟體就沒了），
或是存了但重新解析之後被蓋掉。所以這兩件事分開測。
"""

from __future__ import annotations

from lqa.compare.classify import CompareResult
from lqa.gui.results import RowResult, is_visible, next_flagged, rows_from_result
from lqa.model import Category, ExpectedLine, Issue
from lqa.record.project import Project


def line(order: int) -> ExpectedLine:
    return ExpectedLine(order=order, dialogue_id=f"8020{order:04d}",
                        target_en="hello")


def result_with(*pairs):
    return CompareResult(issues=[i for _e, group in pairs for i in group],
                         expected=[e for e, _g in pairs], captured=[])


class TestOverrideWins:
    def test_marking_a_false_positive_as_pass_clears_the_flag(self):
        a = line(0)
        rows = rows_from_result(
            result_with((a, [Issue(category=Category.MISMATCH, expected=a)])),
            {0: "PASS"})
        assert not rows[0].flagged
        assert rows[0].label == "一致（人工）"

    def test_the_automatic_verdict_is_still_visible(self):
        """改判之後仍要看得到軟體原本怎麼判，否則無從回頭檢討。"""
        a = line(0)
        rows = rows_from_result(
            result_with((a, [Issue(category=Category.MISMATCH, expected=a)])),
            {0: "PASS"})
        assert rows[0].auto_label == "不一致"

    def test_marking_a_pass_as_wrong_flags_it(self):
        """反過來也要能標：軟體說一致但人看出問題。"""
        a = line(0)
        rows = rows_from_result(
            result_with((a, [Issue(category=Category.PASS, expected=a)])),
            {0: "MISMATCH"})
        assert rows[0].flagged and rows[0].label == "不一致（人工）"

    def test_override_follows_the_filter(self):
        a = line(0)
        rows = rows_from_result(
            result_with((a, [Issue(category=Category.MISMATCH, expected=a)])),
            {0: "PASS"})
        assert not is_visible(rows[0], "flagged")

    def test_override_is_skipped_by_the_jump_shortcut(self):
        """標成一致之後，Ctrl+上下就不該再停在它上面。"""
        rows = {
            0: RowResult([Issue(category=Category.MISMATCH)], Category.PASS),
            1: RowResult([Issue(category=Category.MISMATCH)]),
        }
        assert next_flagged(-1, 2, rows, 1) == 1

    def test_an_unknown_stored_category_is_ignored(self):
        """舊檔案可能存了現在已經沒有的分類，不能因此讀不出結果。"""
        a = line(0)
        rows = rows_from_result(
            result_with((a, [Issue(category=Category.MISMATCH, expected=a)])),
            {0: "SPEAKER_VARIANT"})
        assert rows[0].override is None
        assert rows[0].flagged


class TestVerdictStorage:
    def _project(self, tmp_path) -> Project:
        script = tmp_path / "文本.xlsx"
        script.write_bytes(b"x")
        return Project(str(script), root=tmp_path / "projects")

    def test_verdict_survives_reopening(self, tmp_path):
        project = self._project(tmp_path)
        project.set_verdict("AVG1", 3, "PASS", "80201004")
        again = Project(project.script_path, root=tmp_path / "projects")
        assert again.verdicts("AVG1") == {3: "PASS"}

    def test_setting_none_clears_it(self, tmp_path):
        project = self._project(tmp_path)
        project.set_verdict("AVG1", 3, "PASS", "80201004")
        project.set_verdict("AVG1", 3, None, "80201004")
        assert project.verdicts("AVG1") == {}

    def test_verdicts_are_per_sheet(self, tmp_path):
        project = self._project(tmp_path)
        project.set_verdict("AVG1", 0, "PASS", "a")
        project.set_verdict("AVG2", 1, "MISMATCH", "b")
        assert project.verdicts("AVG1") == {0: "PASS"}
        assert project.verdicts("AVG2") == {1: "MISMATCH"}

    def test_clearing_a_sheet_takes_the_verdicts_with_it(self, tmp_path):
        """截圖都刪了，根據那些截圖做的判定也不該留著。"""
        project = self._project(tmp_path)
        (project.sheet_dir("AVG1") / "shots").mkdir(parents=True)
        project.set_verdict("AVG1", 0, "PASS", "a")
        project.clear_sheet("AVG1")
        assert project.verdicts("AVG1") == {}
