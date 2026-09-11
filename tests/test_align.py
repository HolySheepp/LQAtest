from lqa.compare.align import AlignConfig, align
from lqa.compare.normalize import similarity

LINES = [
    "On the day of the second mock exam, students filed into their seats.",
    "Nev stood by the doorway, his eyes still fixed on the study notes.",
    "Alright, stop looking at them now. If you keep cramming, you will scramble.",
    "There are still a few formulas I have not memorized properly.",
    "Take it easy. Do your best, that is all that matters.",
    "It does not matter what score you end up with.",
    "No matter how it turns out, we will figure something out.",
    "Nev fell silent. After a moment, he snapped his notebook shut.",
]


def pairs_as_tuples(pairs):
    return [(p.exp_idx, p.cap_idx) for p in pairs]


class TestAlign:
    def test_identical_sequences_align_one_to_one(self):
        pairs, reordered = align(LINES, LINES, similarity, AlignConfig())
        assert pairs_as_tuples(pairs) == [(i, i) for i in range(len(LINES))]
        assert reordered == set()

    def test_missing_line_becomes_expected_side_gap(self):
        captured = LINES[:3] + LINES[4:]      # 少了索引 3
        pairs, _ = align(LINES, captured, similarity, AlignConfig())
        gaps = [p.exp_idx for p in pairs if p.cap_idx is None]
        assert gaps == [3]

    def test_extra_line_becomes_captured_side_gap(self):
        captured = LINES[:4] + ["A line that never existed in the script at all."] + LINES[4:]
        pairs, _ = align(LINES, captured, similarity, AlignConfig())
        gaps = [p.cap_idx for p in pairs if p.exp_idx is None]
        assert gaps == [4]

    def test_swapped_lines_are_detected_as_reorder(self):
        captured = LINES[:4] + [LINES[5], LINES[4]] + LINES[6:]
        pairs, reordered = align(LINES, captured, similarity, AlignConfig())
        assert reordered, "互換的句子應該被交叉比對抓出來"
        # 互換的兩句最終都要有對應的期望索引，不能留成缺口
        matched_exp = {p.exp_idx for p in pairs if p.cap_idx is not None}
        assert {4, 5} <= matched_exp

    def test_mismatched_text_still_aligns_by_position(self):
        """同一個位置文字被改掉時要配成一對，這樣才報得出「不一致」。"""
        captured = list(LINES)
        captured[2] = "Completely different sentence occupying the same slot here."
        pairs, _ = align(LINES, captured, similarity, AlignConfig())
        assert (2, 2) in pairs_as_tuples(pairs)

    def test_recording_started_late_is_anchored(self):
        """從第 4 句才開始錄，前 3 句應該落成缺口而不是全盤錯位。"""
        pairs, _ = align(LINES, LINES[3:], similarity, AlignConfig())
        gaps = [p.exp_idx for p in pairs if p.cap_idx is None]
        assert gaps == [0, 1, 2]

    def test_empty_capture_marks_everything_missing(self):
        pairs, _ = align(LINES, [], similarity, AlignConfig())
        assert [p.exp_idx for p in pairs] == list(range(len(LINES)))
        assert all(p.cap_idx is None for p in pairs)
