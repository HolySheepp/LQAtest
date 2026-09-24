"""對白判定：分辨「正在播對白」與「過場動畫」。

先前用整塊區域的亮度中位數，它分不出兩種黑：

    黑底對話框        中位數 0  -> 是對白
    整個畫面淡出成黑  中位數 0  -> 不是對白，卻被判成對白

整個畫面變紅那種轉場又反過來，中位數高得像對白框不在。兩種都會誤判。

改成看形狀：幾塊「必須全黑」（對白框的底）加幾塊「必須不是全黑」
（框外），兩邊同時成立才算對白。這幾個測試就是照著實際會踩到的
轉場樣態寫的。
"""

from __future__ import annotations

import numpy as np

from lqa.config import GateConfig
from lqa.detect.gate import (DialogueGate, dialogue_showing, explain,
                             region_darkness)

W, H = 400, 600
# 對白框在下半部：上面是立繪，下面是純黑底加白字
BOX_PAD_TOP = (40, 330, 320, 30)     # 文字上方的留白，永遠全黑
BOX_PAD_BOTTOM = (40, 560, 320, 30)  # 文字下方的留白，永遠全黑
ART = (40, 60, 320, 120)             # 立繪，對白時一定不是全黑

GATE = GateConfig(black_rois=[BOX_PAD_TOP, BOX_PAD_BOTTOM], lit_rois=[ART],
                  tolerance=10, hold_ms=250)


def frame(top: int, bottom: int, text: bool = True) -> np.ndarray:
    """上半部亮度 top、下半部亮度 bottom 的畫面。text 表示對白框裡有字。"""
    img = np.zeros((H, W, 3), np.uint8)
    img[:300] = top
    img[300:] = bottom
    if text:
        img[400:430, 60:340] = 254      # 對白文字，刻意畫在留白之外
    return img


def dialogue() -> np.ndarray:
    """正常的對白畫面：立繪亮著，對白框純黑。"""
    return frame(top=140, bottom=0)


class TestNormalDialogue:
    def test_dialogue_passes(self):
        assert dialogue_showing(dialogue(), GATE)

    def test_text_does_not_break_it(self):
        """留白區要挑在字的外面，字進來就會判不出對白。"""
        assert region_darkness(dialogue(), BOX_PAD_TOP) <= GATE.tolerance
        assert region_darkness(dialogue(), BOX_PAD_BOTTOM) <= GATE.tolerance

    def test_empty_box_before_typing_starts(self):
        """框已經出來但還沒打字，也要算對白。"""
        assert dialogue_showing(frame(top=140, bottom=0, text=False), GATE)

    def test_compression_noise_is_tolerated(self):
        """純黑區被壓縮弄出幾顆雜點，不該翻盤。"""
        img = dialogue()
        img[335, 50] = 200
        img[336, 60] = 180
        assert dialogue_showing(img, GATE)


class TestTransitionsThatFooledTheOldCheck:
    def test_whole_screen_goes_black(self):
        """整個畫面淡出成黑。舊判準看中位數是 0，會誤判成對白。"""
        img = frame(top=0, bottom=0, text=False)
        assert not dialogue_showing(img, GATE)

    def test_whole_screen_goes_red(self):
        """整個畫面變紅。舊判準看中位數很高，會誤判成「框不在」——

        方向雖然對，但它是碰巧對的；這裡是因為對白框的底不再是黑的。
        """
        img = np.zeros((H, W, 3), np.uint8)
        img[:, :, 2] = 180              # BGR 的紅
        assert not dialogue_showing(img, GATE)

    def test_artwork_showing_where_the_box_should_be(self):
        """最常見的過場：對白框消失，底下的畫面露出來。"""
        assert not dialogue_showing(frame(top=140, bottom=120, text=False), GATE)

    def test_dark_scene_is_still_a_cutscene_if_the_box_is_gone(self):
        """暗的過場畫面：只要沒到全黑，就還是分得出來。"""
        assert not dialogue_showing(frame(top=60, bottom=40, text=False), GATE)


class TestConfiguration:
    def test_unconfigured_gate_blocks_nothing(self):
        """沒框範圍就不擋，維持原本的行為。"""
        assert dialogue_showing(frame(top=0, bottom=0), GateConfig())

    def test_black_regions_alone_cannot_tell_black_screen_apart(self):
        """只框全黑區的話，整個畫面變黑還是會被當成對白 ——

        這正是需要「非全黑區」的理由。
        """
        only_black = GateConfig(black_rois=[BOX_PAD_TOP, BOX_PAD_BOTTOM])
        assert dialogue_showing(frame(top=0, bottom=0, text=False), only_black)

    def test_one_black_region_failing_is_enough_to_reject(self):
        """全黑區是「全部都要成立」—— 過場剛好在某一塊是黑的並不奇怪。"""
        img = dialogue()
        img[560:590, 40:360] = 90        # 只弄亮下面那塊留白
        assert not dialogue_showing(img, GATE)

    def test_roi_outside_the_frame_does_not_crash(self):
        assert region_darkness(dialogue(), (5000, 5000, 10, 10)) == 255

    def test_explain_lists_every_region(self):
        text = explain(dialogue(), GATE)
        assert "全黑1" in text and "全黑2" in text and "非全黑1" in text


class TestHoldTime:
    def test_needs_to_hold_before_opening(self):
        """轉場途中會有剛好兩邊都成立的瞬間，不能馬上放行。"""
        gate = DialogueGate(GATE)
        assert not gate.update(dialogue(), 0)
        assert not gate.update(dialogue(), 100)
        assert gate.update(dialogue(), 260)

    def test_closes_immediately(self):
        """對白消失是真的消失，沒必要拖。"""
        gate = DialogueGate(GATE)
        gate.update(dialogue(), 0)
        gate.update(dialogue(), 300)
        assert gate.open
        assert not gate.update(frame(top=140, bottom=120, text=False), 310)

    def test_a_brief_flicker_does_not_open_the_gate(self):
        """轉場中閃過一幀「像對白」的畫面，不該被放行。"""
        gate = DialogueGate(GATE)
        cut = frame(top=140, bottom=120, text=False)
        gate.update(cut, 0)
        gate.update(dialogue(), 50)      # 閃一下
        gate.update(cut, 100)
        assert not gate.open

    def test_the_clock_restarts_after_a_break(self):
        gate = DialogueGate(GATE)
        gate.update(dialogue(), 0)
        gate.update(frame(top=140, bottom=120, text=False), 100)
        gate.update(dialogue(), 200)
        assert not gate.update(dialogue(), 300)     # 從 200 重新算
        assert gate.update(dialogue(), 460)

    def test_zero_hold_opens_at_once(self):
        gate = DialogueGate(GateConfig(black_rois=[BOX_PAD_TOP], hold_ms=0))
        assert gate.update(dialogue(), 0)
