import time

import pytest

from lqa.compare.classify import CompareConfig, compare
from lqa.compare.script_loader import load_script, load_speaker_map
from lqa.model import CapturedLine, Category

from conftest import SPEAKER_MAP


def cap(seq: int, body: str, speaker: str = "") -> CapturedLine:
    return CapturedLine(
        seq=seq, timestamp=time.time(), body_text=body, speaker_text=speaker
    )


def capture_all(expected, use_speaker: bool = False) -> list[CapturedLine]:
    """模擬一場完全正確的錄製。"""
    return [
        cap(i, e.target_en, e.speaker_en if use_speaker else "")
        for i, e in enumerate(expected)
    ]


def categories_for(result, dialogue_id: str) -> set[Category]:
    return {i.category for i in result.issues if i.dialogue_id == dialogue_id}


@pytest.fixture
def expected(sample_xlsx):
    return load_script(sample_xlsx, SPEAKER_MAP)


class TestPass:
    def test_perfect_run_has_no_problems(self, expected):
        result = compare(expected, capture_all(expected))
        assert result.problems == []
        assert result.summary()["PASS"] == len(expected)

    def test_ocr_punctuation_noise_still_passes(self, expected):
        captured = capture_all(expected)
        captured[4].body_text = "Take it easy, Do your best that's all that matters"
        result = compare(expected, captured)
        assert result.problems == []


class TestUntranslated:
    def test_chinese_on_screen_is_flagged(self, expected):
        captured = capture_all(expected)
        captured[4].body_text = expected[4].source_zh
        result = compare(expected, captured)
        assert Category.UNTRANSLATED in categories_for(result, "80208005")

    def test_untranslated_line_reports_its_dialogue_id(self, expected):
        captured = capture_all(expected)
        captured[4].body_text = expected[4].source_zh
        result = compare(expected, captured)
        issue = next(i for i in result.problems if i.category is Category.UNTRANSLATED)
        assert issue.dialogue_id == "80208005"


class TestTruncated:
    def test_prefix_of_translation_is_truncation(self, expected):
        captured = capture_all(expected)
        full = expected[10].target_en
        captured[10].body_text = full[: int(len(full) * 0.6)]
        result = compare(expected, captured)
        assert Category.TRUNCATED in categories_for(result, "80208118")

    def test_truncation_records_the_missing_tail(self, expected):
        captured = capture_all(expected)
        full = expected[10].target_en
        captured[10].body_text = full[: int(len(full) * 0.6)]
        result = compare(expected, captured)
        issue = next(i for i in result.problems if i.category is Category.TRUNCATED)
        assert issue.extras["missing_tail"]
        assert "disappoint" in issue.extras["missing_tail"]

    def test_legitimate_ellipsis_ending_is_not_truncation(self, expected):
        """專案譯文大量以 '...' 結尾，不能因此誤判超框。"""
        result = compare(expected, capture_all(expected))
        assert not [i for i in result.problems if i.category is Category.TRUNCATED]


class TestMismatch:
    def test_different_text_in_same_slot(self, expected):
        captured = capture_all(expected)
        captured[7].body_text = "Whatever happens, I am going to handle this on my own."
        result = compare(expected, captured)
        assert Category.MISMATCH in categories_for(result, "80208008")


class TestMissing:
    def test_line_never_shown_is_missing(self, expected):
        captured = [c for c in capture_all(expected) if c.seq != 8]
        for i, c in enumerate(captured):
            c.seq = i
        result = compare(expected, captured)
        assert Category.MISSING in categories_for(result, "80208009")


class TestExtra:
    def test_unknown_line_on_screen(self, expected):
        captured = capture_all(expected)
        captured.insert(5, cap(0, "This sentence is nowhere in the localization sheet."))
        for i, c in enumerate(captured):
            c.seq = i
        result = compare(expected, captured)
        assert any(i.category is Category.EXTRA for i in result.problems)


class TestOrder:
    def test_swapped_lines_flagged_as_order_issue(self, expected):
        captured = capture_all(expected)
        captured[7], captured[8] = captured[8], captured[7]
        for i, c in enumerate(captured):
            c.seq = i
        result = compare(expected, captured)
        assert any(i.category is Category.ORDER for i in result.problems)


class TestSpeaker:
    def test_wrong_speaker_name_flagged(self, expected):
        captured = capture_all(expected, use_speaker=True)
        captured[2].speaker_text = "Nev"      # 文本應為 Suqing
        result = compare(expected, captured)
        assert Category.SPEAKER in categories_for(result, "80208003")

    def test_correct_speaker_is_silent(self, expected):
        result = compare(expected, capture_all(expected, use_speaker=True))
        assert not [i for i in result.problems if i.category is Category.SPEAKER]

    def test_narration_rows_are_not_checked(self, expected):
        """旁白列沒有發話者，不該因為畫面沒名字就報錯。"""
        captured = capture_all(expected, use_speaker=True)
        result = compare(expected, captured)
        speaker_ids = {i.dialogue_id for i in result.problems
                       if i.category is Category.SPEAKER}
        assert "80208001" not in speaker_ids

    def test_speaker_check_can_be_disabled(self, expected):
        captured = capture_all(expected, use_speaker=True)
        captured[2].speaker_text = "Nev"
        cfg = CompareConfig(check_speaker=False)
        result = compare(expected, captured, cfg)
        assert not [i for i in result.problems if i.category is Category.SPEAKER]

    def test_unmapped_speaker_does_not_false_alarm(self, sample_xlsx):
        """對照表缺英文名時應該略過，而不是報一堆假錯。"""
        expected = load_script(sample_xlsx, {"蘇青": "Suqing"})   # 缺 涅維
        captured = capture_all(expected, use_speaker=True)
        result = compare(expected, captured)
        assert not [i for i in result.problems if i.category is Category.SPEAKER]


class TestReport:
    def test_writes_xlsx_and_csv(self, expected, tmp_path):
        from lqa.compare.report import write_csv, write_xlsx

        captured = capture_all(expected)
        captured[4].body_text = expected[4].source_zh
        result = compare(expected, captured)

        xlsx = write_xlsx(result, tmp_path / "r.xlsx")
        csv_path = write_csv(result, tmp_path / "r.csv")
        assert xlsx.exists() and xlsx.stat().st_size > 0
        assert "未翻譯" in csv_path.read_text(encoding="utf-8-sig")
