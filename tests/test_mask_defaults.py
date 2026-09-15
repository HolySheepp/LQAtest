"""取字參數的預設值，以及「恢復預設」會動到哪些。

這幾個數字是實測掃出來的（門檻掃描見 MaskConfig 的說明），
不小心改掉不會有任何測試失敗、也不會報錯，只會讓辨識率悄悄變差，
所以直接把它們釘住。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from lqa.config import MaskConfig
from lqa.detect import textmask as tm
from lqa.gui.debug_window import KNOB_LABELS, describe_changes

# 調試視窗六個旋鈕該顯示的預設值
EXPECTED = {
    "bright_threshold": 100,
    "seed_threshold": 210,
    "ocr_grow": 2,
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
        before = replace(MaskConfig(), bright_threshold=170, ocr_grow=4)
        assert describe_changes(before, MaskConfig()) == [
            "低門檻 170 → 100", "筆畫膨脹 4 → 2"]

    def test_keeps_the_on_screen_order(self):
        """訊息裡的順序要和畫面上旋鈕的順序一樣，才對得起來。"""
        before = replace(MaskConfig(), color_tolerance=10, bright_threshold=10)
        assert [line.split()[0] for line in describe_changes(before, MaskConfig())] \
            == ["低門檻", "容許色距"]

    def test_ignores_fields_without_a_knob(self):
        """文字顏色沒有旋鈕，不該出現在「哪幾個數字會變」裡。"""
        before = replace(MaskConfig(), text_colors=["#ff8a00"])
        assert describe_changes(before, MaskConfig()) == []


class TestDefaultsOnARealisticDialogueBox:
    """預設值成不成立，取決於對白框背景有多亮。

    hysteresis 的低門檻同時是「洪水擴散的邊界」：種子找到筆畫核心之後，
    會沿著所有高於低門檻而且相連的像素長出去。只要背景亮過低門檻、
    又碰得到筆畫，整片背景都會被收進遮罩。

    實際遊戲的對白框是漸變變暗的深色，所以 100 站得住。
    這裡把那個前提寫成測試 —— 哪天美術把對白框改亮，就會在這裡先炸掉，
    而不是等到辨識結果悄悄變差才發現。
    """

    @staticmethod
    def box(background: int, with_text: bool) -> np.ndarray:
        frame = np.full((60, 200, 3), background, np.uint8)
        if with_text:
            for x in range(20, 180, 20):
                frame[20:40, x:x + 3] = 254        # 筆畫核心，過得了種子門檻
                frame[20:40, x + 3:x + 5] = 150    # 灰白過渡，只過得了低門檻
        return frame

    def text_pixels(self, frame: np.ndarray) -> int:
        return tm.text_pixel_count(tm.build_mask(frame, MaskConfig()))

    def test_dark_box_with_no_text_reads_as_empty(self):
        assert self.text_pixels(self.box(40, with_text=False)) == 0

    def test_dark_box_keeps_strokes_and_their_soft_edges(self):
        """核心加上過渡都要收到，只留核心的話字會被挖空。"""
        strokes = self.text_pixels(self.box(40, with_text=True))
        assert 600 <= strokes <= 1200, strokes

    def test_background_brighter_than_the_low_threshold_floods(self):
        """這就是低門檻不能再往下調的原因。"""
        low = MaskConfig().bright_threshold
        flooded = self.text_pixels(self.box(low + 20, with_text=True))
        assert flooded > self.text_pixels(self.box(40, with_text=True)) * 5

    def test_the_low_threshold_clears_the_real_box_by_a_margin(self):
        """對白框最亮的地方也該離低門檻有一段距離，不能剛好卡在邊上。"""
        assert MaskConfig().bright_threshold >= 90

    def test_seed_threshold_stays_above_the_low_one(self):
        """種子門檻要是低於低門檻，雙門檻就退化成單門檻了。"""
        cfg = MaskConfig()
        assert cfg.seed_threshold > cfg.bright_threshold + 50
