from lqa.compare.script_loader import load_script, load_speaker_map, unknown_speakers


class TestLoadScript:
    def test_skips_rows_without_dialogue_id(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        # 尺規列、場景說明列、只有備註的轉場列都要被跳過
        assert len(lines) == 13
        assert all(ln.dialogue_id for ln in lines)

    def test_header_is_found_on_second_row(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        assert lines[0].dialogue_id == "80208001"
        assert lines[0].target_en.startswith("On the day of the second mock exam")

    def test_row_order_wins_over_id_order(self, sample_xlsx):
        """80208118/119 夾在 80208010 與 80208011 之間，期望順序必須照列順序。"""
        ids = [ln.dialogue_id for ln in load_script(sample_xlsx)]
        assert ids[9:13] == ["80208010", "80208118", "80208119", "80208011"]
        assert [ln.order for ln in load_script(sample_xlsx)] == list(range(13))

    def test_note_column_header_with_parentheses(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        assert lines[0].note == "黑幕淡入-"

    def test_narration_rows_have_no_speaker(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        assert lines[0].speaker_zh == ""
        assert lines[2].speaker_zh == "蘇青"

    def test_sheet_row_points_back_to_source(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        # 尺規列 1、標題列 2、場景列 3，第一句在第 4 列
        assert lines[0].sheet_row == 4


class TestSpeakerMap:
    def test_maps_chinese_to_english(self, sample_xlsx, speaker_csv):
        mapping = load_speaker_map(speaker_csv)
        lines = load_script(sample_xlsx, mapping)
        assert lines[2].speaker_zh == "蘇青"
        assert lines[2].speaker_en == "Suqing"

    def test_header_row_is_not_treated_as_entry(self, speaker_csv):
        mapping = load_speaker_map(speaker_csv)
        assert "名字" not in mapping
        assert mapping == {"蘇青": "Suqing", "涅維": "Nev"}

    def test_unknown_speakers_reported(self, sample_xlsx):
        lines = load_script(sample_xlsx, {"蘇青": "Suqing"})
        assert unknown_speakers(lines) == ["涅維"]

    def test_no_map_means_no_english_names(self, sample_xlsx):
        lines = load_script(sample_xlsx)
        assert all(not ln.speaker_en for ln in lines)
