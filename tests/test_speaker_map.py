"""發話者對照表的讀取測試。

這張表是跨檔案共用的，格式不由我們決定，所以要能吃下常見的幾種寫法。
它同時是發話者檢查的正確答案來源：文本給中文名，畫面顯示英文名，
比對前必須先透過這張表把中文名換成英文名。
"""

from __future__ import annotations

import pytest

from lqa.compare.script_loader import load_speaker_map


def write_csv(tmp_path, rows: list[list[str]], name: str = "speakers.csv"):
    path = tmp_path / name
    path.write_text("\n".join(",".join(r) for r in rows), encoding="utf-8")
    return path


def write_xlsx(tmp_path, rows: list[list[str]], name: str = "speakers.xlsx"):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


class TestHeaderDetection:
    def test_default_header(self, tmp_path):
        path = write_csv(tmp_path, [["名字", "English"], ["蘇青", "Suqing"]])
        assert load_speaker_map(path) == {"蘇青": "Suqing"}

    def test_chinese_english_header_variant(self, tmp_path):
        path = write_csv(tmp_path, [["中文名", "英文名"], ["涅維", "Nev"]])
        assert load_speaker_map(path) == {"涅維": "Nev"}

    def test_speaker_name_header_variant(self, tmp_path):
        path = write_csv(tmp_path, [["角色名", "Name_EN"], ["可可", "Coco"]])
        assert load_speaker_map(path) == {"可可": "Coco"}

    def test_columns_found_by_header_not_position(self, tmp_path):
        """英文欄不在第二欄時也要找得到。"""
        path = write_csv(
            tmp_path,
            [["中文名", "備註", "英文名"], ["蘇青", "女主角", "Suqing"]],
        )
        assert load_speaker_map(path) == {"蘇青": "Suqing"}

    def test_header_row_not_on_first_line(self, tmp_path):
        path = write_csv(
            tmp_path,
            [["角色對照表", ""], ["", ""], ["中文名", "英文名"], ["涅維", "Nev"]],
        )
        assert load_speaker_map(path) == {"涅維": "Nev"}

    def test_falls_back_to_two_column_layout(self, tmp_path):
        """完全沒有可辨識的標題時，當成第一欄中文、第二欄英文。"""
        path = write_csv(tmp_path, [["蘇青", "Suqing"], ["涅維", "Nev"]])
        assert load_speaker_map(path) == {"蘇青": "Suqing", "涅維": "Nev"}


class TestFormats:
    def test_reads_xlsx(self, tmp_path):
        path = write_xlsx(tmp_path, [["中文名", "英文名"], ["涅維", "Nev"], ["可可", "Coco"]])
        assert load_speaker_map(path) == {"涅維": "Nev", "可可": "Coco"}

    def test_reads_tsv(self, tmp_path):
        path = tmp_path / "speakers.tsv"
        path.write_text("名字\tEnglish\n蘇青\tSuqing\n", encoding="utf-8")
        assert load_speaker_map(path) == {"蘇青": "Suqing"}


class TestEdgeCases:
    def test_blank_entries_are_skipped(self, tmp_path):
        path = write_csv(
            tmp_path,
            [["名字", "English"], ["蘇青", "Suqing"], ["涅維", ""], ["", "Ghost"]],
        )
        assert load_speaker_map(path) == {"蘇青": "Suqing"}

    def test_blank_lines_are_ignored(self, tmp_path):
        path = write_csv(
            tmp_path, [["名字", "English"], ["", ""], ["蘇青", "Suqing"]]
        )
        assert load_speaker_map(path) == {"蘇青": "Suqing"}

    def test_first_entry_wins_on_duplicates(self, tmp_path):
        path = write_csv(
            tmp_path,
            [["名字", "English"], ["蘇青", "Suqing"], ["蘇青", "SuChing"]],
        )
        assert load_speaker_map(path) == {"蘇青": "Suqing"}

    def test_none_path_returns_empty(self):
        assert load_speaker_map(None) == {}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_speaker_map(tmp_path / "nope.csv")


class TestUsedAsAnswerKey:
    """確認整條鏈路：中文名 -> 對照表 -> 英文名 -> 和畫面比對。"""

    def test_matching_english_name_passes(self, tmp_path, sample_xlsx):
        import time

        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import CapturedLine, Category

        mapping = load_speaker_map(
            write_csv(tmp_path, [["中文名", "英文名"], ["蘇青", "Suqing"], ["涅維", "Nev"]])
        )
        expected = load_script(sample_xlsx, mapping)
        captured = [
            CapturedLine(seq=i, timestamp=time.time(), body_text=e.target_en,
                         speaker_text=e.speaker_en)
            for i, e in enumerate(expected)
        ]
        result = compare(expected, captured)
        assert not [i for i in result.problems if i.category is Category.SPEAKER]

    def test_wrong_english_name_is_flagged(self, tmp_path, sample_xlsx):
        import time

        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import CapturedLine, Category

        mapping = load_speaker_map(
            write_csv(tmp_path, [["中文名", "英文名"], ["蘇青", "Suqing"], ["涅維", "Nev"]])
        )
        expected = load_script(sample_xlsx, mapping)
        captured = [
            CapturedLine(seq=i, timestamp=time.time(), body_text=e.target_en,
                         speaker_text=e.speaker_en)
            for i, e in enumerate(expected)
        ]
        # 第 3 句文本是 蘇青 -> Suqing，畫面卻顯示 Nev
        captured[2].speaker_text = "Nev(11201)"
        result = compare(expected, captured)
        flagged = [i for i in result.problems if i.category is Category.SPEAKER]
        assert len(flagged) == 1
        assert flagged[0].dialogue_id == "80208003"
        assert "Suqing" in flagged[0].detail and "Nev" in flagged[0].detail

    def test_blank_english_column_turns_the_check_off_silently(self, tmp_path,
                                                               sample_xlsx):
        """對照表有名字但英文欄空白時，發話者根本不會被檢查。

        這是刻意的（沒有正確答案就不該亂報錯），但從介面上完全看不出來 ——
        使用者會以為發話者已經查過了。unknown_speakers 就是用來提醒這件事的，
        所以這裡一併確認它講得出缺了哪些。
        """
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script, unknown_speakers
        from lqa.model import CapturedLine, Category

        mapping = load_speaker_map(
            write_csv(tmp_path, [["中文名", "英文名"], ["蘇青", ""], ["涅維", ""]]))
        expected = load_script(sample_xlsx, mapping)
        captured = [
            CapturedLine(seq=i, timestamp=0.0, body_text=e.target_en,
                         speaker_text="CompletelyWrongName(999)")
            for i, e in enumerate(expected)
        ]
        result = compare(expected, captured)
        assert not [i for i in result.problems if i.category is Category.SPEAKER]
        assert unknown_speakers(expected), "應該要講得出對照表缺了哪些名字"


class TestHandWrittenFormats:
    """這份表是人手打的，不是程式產生的。

    打完一份看起來沒問題、卻一個名字都對不上，是最難自己查出來的錯 ——
    而且症狀是「發話者完全沒被檢查」，安靜得像是本來就沒問題。
    所以收的格式盡量寬。
    """

    def write(self, tmp_path, name: str, text: str):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return load_speaker_map(path)

    EXPECTED = {"蘇青": "Suqing", "涅維": "Nev"}

    def test_plain_txt_with_half_width_commas(self, tmp_path):
        assert self.write(tmp_path, "a.txt", "蘇青,Suqing\n涅維,Nev\n") == self.EXPECTED

    def test_full_width_comma(self, tmp_path):
        """中文輸入法打出來的逗號是全形，只認半形等於叫人自己踩坑。"""
        assert self.write(tmp_path, "a.txt", "蘇青，Suqing\n涅維，Nev\n") == self.EXPECTED

    def test_markdown_table(self, tmp_path):
        """從文件裡貼出來最常見的就是表格，連分隔線一起認。"""
        text = ("# 發話者對照\n\n| 中文 | English |\n|---|---|\n"
                "| 蘇青 | Suqing |\n| 涅維 | Nev |\n")
        assert self.write(tmp_path, "a.md", text) == self.EXPECTED

    def test_markdown_without_a_table(self, tmp_path):
        assert self.write(tmp_path, "a.md", "蘇青, Suqing\n\n涅維 , Nev\n") == self.EXPECTED

    def test_comment_lines_are_skipped(self, tmp_path):
        assert self.write(tmp_path, "a.txt",
                          "# 之後再補\n蘇青,Suqing\n涅維,Nev\n") == self.EXPECTED

    def test_spaces_around_the_names_are_trimmed(self, tmp_path):
        assert self.write(tmp_path, "a.txt",
                          "  蘇青 ,  Suqing  \n涅維,Nev\n") == self.EXPECTED

    def test_tabs_still_work(self, tmp_path):
        assert self.write(tmp_path, "a.tsv",
                          "蘇青\tSuqing\n涅維\tNev\n") == self.EXPECTED

    def test_xlsx_is_first_column_chinese_second_english(self, tmp_path):
        assert load_speaker_map(
            write_xlsx(tmp_path, [["蘇青", "Suqing"], ["涅維", "Nev"]])) == self.EXPECTED

    def test_names_without_an_english_side_are_skipped(self, tmp_path):
        """只填了一半的行不算數，但不該讓整份表讀不出來。"""
        assert self.write(tmp_path, "a.txt",
                          "蘇青,Suqing\n校長,\n涅維,Nev\n") == self.EXPECTED

    def test_unsupported_extension_says_what_is_allowed(self, tmp_path):
        path = tmp_path / "a.docx"
        path.write_text("蘇青,Suqing", encoding="utf-8")
        with pytest.raises(ValueError, match="xlsx"):
            load_speaker_map(path)


class TestTableConflicts:
    """同一個中文名被填了兩種英文名。

    採用第一個，但一定要講出來 —— 這幾乎都是表本身填錯，
    而且錯的那一半會安靜地永遠對不上，看起來像是遊戲有問題。
    """

    def test_clean_table_reports_nothing(self, tmp_path):
        from lqa.compare.script_loader import read_speaker_map

        table = read_speaker_map(
            write_csv(tmp_path, [["蘇青", "Suqing"], ["涅維", "Nev"]]))
        assert table.conflicts == {}

    def test_conflicting_entries_are_listed(self, tmp_path):
        from lqa.compare.script_loader import read_speaker_map

        table = read_speaker_map(write_csv(tmp_path, [
            ["蘇青", "Suqing"], ["涅維", "Nev"], ["蘇青", "Cyan"]]))
        assert table.conflicts == {"蘇青": ["Suqing", "Cyan"]}

    def test_the_first_one_still_wins(self, tmp_path):
        from lqa.compare.script_loader import read_speaker_map

        table = read_speaker_map(write_csv(tmp_path, [
            ["蘇青", "Suqing"], ["蘇青", "Cyan"]]))
        assert table.names["蘇青"] == "Suqing"

    def test_repeated_but_identical_rows_are_not_a_conflict(self, tmp_path):
        """大表難免重複貼到，內容一樣就不算問題。"""
        from lqa.compare.script_loader import read_speaker_map

        table = read_speaker_map(write_csv(tmp_path, [
            ["蘇青", "Suqing"], ["蘇青", "Suqing"]]))
        assert table.conflicts == {}

    def test_one_english_name_for_several_chinese_names_is_fine(self, tmp_path):
        """大廚和廚師都翻成 Chef 是正常的，不該報成衝突。"""
        from lqa.compare.script_loader import read_speaker_map

        table = read_speaker_map(write_csv(tmp_path, [
            ["大廚", "Chef"], ["廚師", "Chef"]]))
        assert table.conflicts == {}


class TestInGameNameConsistency:
    """對照表沒有這個名字時，仍然查得出「同一個角色前後譯名不一致」。

    不需要正確答案 —— 拿畫面自己和自己比就看得出來。
    這正是對照表補不齊時最容易漏掉的一類問題。
    """

    def _run(self, sample_xlsx, speaker_texts, mapping=None):
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import CapturedLine

        expected = load_script(sample_xlsx, mapping or {})
        captured = [
            CapturedLine(seq=i, timestamp=0.0, body_text=e.target_en,
                         speaker_text=speaker_texts(i, e))
            for i, e in enumerate(expected)
        ]
        return expected, compare(expected, captured)

    def test_two_names_for_one_character_are_flagged(self, sample_xlsx):
        from lqa.model import Category

        # 蘇青 前面叫 Suqing，後面忽然變成 Cyan
        seen = {"n": 0}

        def speaker(i, e):
            if e.speaker_zh != "蘇青":
                return e.speaker_zh
            seen["n"] += 1
            return "Cyan(1)" if seen["n"] > 1 else "Suqing(1)"

        _expected, result = self._run(sample_xlsx, speaker)
        flagged = [i for i in result.problems
                   if i.category is Category.SPEAKER_VARIANT]
        assert flagged, "同一個角色兩種譯名應該要被抓到"
        assert "蘇青" in flagged[0].detail
        assert "Suqing" in flagged[0].detail and "Cyan" in flagged[0].detail

    def test_a_consistent_name_is_not_flagged(self, sample_xlsx):
        from lqa.model import Category

        _expected, result = self._run(
            sample_xlsx, lambda i, e: f"{e.speaker_zh}(1)" if e.speaker_zh else "")
        assert not [i for i in result.problems
                    if i.category is Category.SPEAKER_VARIANT]

    def test_ocr_noise_is_not_treated_as_a_different_name(self, sample_xlsx):
        """把 Suqing 讀成 Suqinq 是辨識誤差，不是譯名不一致。"""
        from lqa.model import Category

        seen = {"n": 0}

        def speaker(i, e):
            if e.speaker_zh != "蘇青":
                return e.speaker_zh
            seen["n"] += 1
            return "Suqinq(1)" if seen["n"] > 1 else "Suqing(1)"

        _expected, result = self._run(sample_xlsx, speaker)
        assert not [i for i in result.problems
                    if i.category is Category.SPEAKER_VARIANT]

    def test_names_with_an_answer_key_are_left_to_the_normal_check(self, sample_xlsx):
        """對照表查得到的交給發話者錯誤，不要同一件事報兩次。"""
        from lqa.model import Category

        seen = {"n": 0}

        def speaker(i, e):
            if e.speaker_zh != "蘇青":
                return e.speaker_zh
            seen["n"] += 1
            return "Cyan(1)" if seen["n"] > 1 else "Suqing(1)"

        _expected, result = self._run(sample_xlsx, speaker, {"蘇青": "Suqing"})
        assert not [i for i in result.problems
                    if i.category is Category.SPEAKER_VARIANT]
        assert [i for i in result.problems if i.category is Category.SPEAKER]


class TestSpeakerChecksNeedTheLineToMatchFirst:
    """對白本身就對不上時，發話者比對沒有意義。

    這張截圖和這個條目的配對本來就不成立，拿它的發話者去比只會在
    「順序不一致」上面再疊一筆發話者錯誤 —— 實測一個沒對齊的錄製
    會多出 26 筆這種衍生訊息，把真正的問題整個淹掉。
    """

    def _result(self, sample_xlsx, body_for, speaker_for, mapping):
        from lqa.compare.classify import compare
        from lqa.compare.script_loader import load_script
        from lqa.model import CapturedLine

        expected = load_script(sample_xlsx, mapping)
        captured = [
            CapturedLine(seq=i, timestamp=0.0, body_text=body_for(i, e),
                         speaker_text=speaker_for(i, e))
            for i, e in enumerate(expected)
        ]
        return expected, compare(expected, captured)

    def test_no_speaker_issue_piled_on_a_mismatched_line(self, sample_xlsx):
        from lqa.model import Category

        mapping = {"蘇青": "Suqing", "涅維": "Nev"}
        expected, result = self._result(
            sample_xlsx,
            lambda i, e: "Totally unrelated sentence." if i == 2 else e.target_en,
            lambda i, e: "WrongName(1)" if i == 2 else e.speaker_en,
            mapping)
        wrong_line = expected[2].dialogue_id
        kinds = {i.category for i in result.problems if i.dialogue_id == wrong_line}
        assert Category.SPEAKER not in kinds
        assert kinds == {Category.MISMATCH}

    def test_a_matching_line_with_a_wrong_speaker_is_still_flagged(self, sample_xlsx):
        """關掉的只是衍生訊息，真正的「對白正確但發話者錯」還是要抓。"""
        from lqa.model import Category

        mapping = {"蘇青": "Suqing", "涅維": "Nev"}
        expected, result = self._result(
            sample_xlsx, lambda i, e: e.target_en,
            lambda i, e: "WrongName(1)" if i == 2 else e.speaker_en, mapping)
        kinds = {i.category for i in result.problems
                 if i.dialogue_id == expected[2].dialogue_id}
        assert kinds == {Category.SPEAKER}

    def test_truncated_lines_still_get_checked(self, sample_xlsx):
        """超框是文字被截斷，但確實是這一句，發話者仍有參考價值。"""
        from lqa.model import Category

        mapping = {"蘇青": "Suqing", "涅維": "Nev"}
        expected, result = self._result(
            sample_xlsx,
            lambda i, e: e.target_en[:len(e.target_en) // 2] if i == 2 else e.target_en,
            lambda i, e: "WrongName(1)" if i == 2 else e.speaker_en, mapping)
        kinds = {i.category for i in result.problems
                 if i.dialogue_id == expected[2].dialogue_id}
        assert Category.SPEAKER in kinds
