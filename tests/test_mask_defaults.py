"""取字參數的預設值，以及「恢復預設」會動到哪些。

這幾個數字是實測掃出來的（門檻掃描見 MaskConfig 的說明），
不小心改掉不會有任何測試失敗、也不會報錯，只會讓辨識率悄悄變差，
所以直接把它們釘住。
"""

from __future__ import annotations

from dataclasses import replace

from lqa.config import MaskConfig
from lqa.gui.debug_window import KNOB_LABELS, describe_changes

# 調試視窗六個旋鈕該顯示的預設值
EXPECTED = {
    "bright_threshold": 140,
    "seed_threshold": 210,
    "ocr_grow": 3,
    "upscale": 1,
    "min_text_pixels": 40,
    "color_tolerance": 60,
}


class TestDefaults:
    def test_every_knob_has_the_agreed_default(self):
        cfg = MaskConfig()
        assert {k: getattr(cfg, k) for k in EXPECTED} == EXPECTED

    def test_knob_labels_cover_exactly_those_fields(self):
        """旋鈕與欄位要對得上，否則還原訊息會漏講其中一項。"""
        assert set(KNOB_LABELS) == set(EXPECTED)

    def test_default_method_is_hysteresis(self):
        """單一門檻對「純白核心 -> 灰白過渡 -> 灰黑描邊」的字本質上不管用。"""
        assert MaskConfig().method == "hysteresis"


class TestDescribeChanges:
    def test_no_changes_when_already_default(self):
        assert describe_changes(MaskConfig(), MaskConfig()) == []

    def test_lists_only_what_actually_moves(self):
        before = replace(MaskConfig(), bright_threshold=100, ocr_grow=2)
        assert describe_changes(before, MaskConfig()) == [
            "低門檻 100 → 140", "筆畫膨脹 2 → 3"]

    def test_keeps_the_on_screen_order(self):
        """訊息裡的順序要和畫面上旋鈕的順序一樣，才對得起來。"""
        before = replace(MaskConfig(), color_tolerance=10, bright_threshold=10)
        assert [line.split()[0] for line in describe_changes(before, MaskConfig())] \
            == ["低門檻", "容許色距"]

    def test_ignores_fields_without_a_knob(self):
        """文字顏色沒有旋鈕，不該出現在「哪幾個數字會變」裡。"""
        before = replace(MaskConfig(), text_colors=["#ff8a00"])
        assert describe_changes(before, MaskConfig()) == []
