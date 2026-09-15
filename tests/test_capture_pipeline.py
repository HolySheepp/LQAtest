"""擷取管線整合測試：合成一張「半透明對白框 + 會動的背景」的假遊戲畫面，
驗證整套設計的兩個核心假設：

  1. 二值化遮罩能濾掉半透明層後面的背景 —— 背景在動、文字沒動時不該觸發擷取
  2. 打字機效果下只會在文字打完後擷取一次，不會抓到半句

第 2 個測試需要 OCR，沒裝就自動略過；第 1 個只需要 opencv。
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="擷取管線需要 opencv")

from lqa.config import MaskConfig, StabilityConfig  # noqa: E402
from lqa.detect import textmask as tm
from lqa.detect.linetracker import LineTracker

W, H = 900, 160
BOX_ALPHA = 0.55

MASK_CFG = MaskConfig(method="value", bright_threshold=170, clahe=False, blur=3,
                      upscale=2, min_text_pixels=40)


def make_background(phase: int) -> np.ndarray:
    """會動的背景：橫向漸層加上幾個隨 phase 平移的亮色塊。"""
    xs = np.linspace(0, 255, W, dtype=np.float32)
    base = np.tile(xs, (H, 1))
    bg = np.stack([base, np.roll(base, phase * 7, axis=1), 255 - base], axis=-1)
    bg = bg.astype(np.uint8)
    for i in range(4):
        cx = (i * 220 + phase * 13) % W
        cv2.circle(bg, (cx, 40 + i * 25), 34, (240, 200, 120), -1)
    return bg


def make_frame(text: str, phase: int = 0) -> np.ndarray:
    """背景 + 半透明對白框 + 白色描邊文字。"""
    frame = make_background(phase)
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (W, H), (20, 20, 30), -1)
    frame = cv2.addWeighted(overlay, BOX_ALPHA, frame, 1 - BOX_ALPHA, 0)

    if text:
        # 先黑色粗描邊再白色字，模擬遊戲常見的文字樣式
        for color, thickness in (((0, 0, 0), 5), ((255, 255, 255), 2)):
            cv2.putText(frame, text, (20, 90), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, color, thickness, cv2.LINE_AA)
    return frame


DIALOGUE_WHITE = "#fefefe"
ACCENT_ORANGE = "#ff8a00"
SPEAKER_BLUE = "#5dbcfe"


def _bgr(hex_color: str) -> tuple[int, int, int]:
    return tm.hex_to_bgr(hex_color)


def make_dialogue_frame(segments: list[tuple[str, str]], bright_scene: bool = True) -> np.ndarray:
    """模擬真實對話框：背景漸變變暗，文字直接疊在上面。

    segments 是 (文字, 顏色) 的序列，會橫向接續繪製，
    用來模擬 <color=#xxxxxx> 造成的同一行內變色。
    bright_scene=True 代表背景原本是亮的（白色場景），
    用來驗證「就算背景是白的，變暗之後也不會被誤認成文字」。
    """
    base = 250 if bright_scene else 90
    frame = np.full((H, W, 3), base, np.uint8)
    # 由上往下遞減的變暗係數
    factor = np.linspace(0.55, 0.30, H, dtype=np.float32)[:, None, None]
    frame = (frame * factor).astype(np.uint8)

    x = 20
    for text, color in segments:
        for draw_color, thickness in ((_bgr(color), 2),):
            cv2.putText(frame, text, (x, 95), cv2.FONT_HERSHEY_SIMPLEX,
                        0.9, draw_color, thickness, cv2.LINE_AA)
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        x += tw + 12
    return frame


class TestColoredTextExtraction:
    """對白預設是 #fefefe，但 <color=#xxxxxx> 會讓部分字變色。
    取字方式必須同時收得到白字與變色字，否則 OCR 會缺字。
    """

    SEGMENTS = [("This is the", DIALOGUE_WHITE), ("reward", ACCENT_ORANGE),
                ("for your work.", DIALOGUE_WHITE)]

    def test_value_channel_keeps_saturated_colors(self):
        """#ff8a00 的 RGB 最大值是 255，和白字同一個量級。"""
        for color in (DIALOGUE_WHITE, ACCENT_ORANGE, SPEAKER_BLUE):
            patch = np.full((4, 4, 3), _bgr(color), np.uint8)
            assert tm.value_channel(patch).max() >= 250, f"{color} 的 V 值過低"

    def test_grayscale_badly_underestimates_saturated_colours(self):
        """對照組：說明為什麼不能用灰階亮度當門檻。

        不綁定預設門檻，直接比較同一個顏色在灰階與 V 通道下的落差：
        白字兩者一致，橘字的灰階值卻掉了快 100，
        代表任何一個能收白字的門檻都可能把橘字砍掉。
        """
        def channels(hex_color: str) -> tuple[int, int]:
            patch = np.full((4, 4, 3), _bgr(hex_color), np.uint8)
            return int(tm.to_gray(patch).max()), int(tm.value_channel(patch).max())

        white_gray, white_value = channels(DIALOGUE_WHITE)
        orange_gray, orange_value = channels(ACCENT_ORANGE)

        assert white_value - white_gray <= 2, "白字在兩種通道下應該幾乎一樣"
        assert orange_value >= 250, "橘字的 V 值應該和白字同一個量級"
        assert orange_gray < 170, f"橘字的灰階值只有 {orange_gray}"
        assert white_gray - orange_gray > 80, "灰階把橘字壓得比白字低很多"

    def test_value_method_captures_both_white_and_colored_words(self):
        # 門檻寫死而不是用預設值：這裡驗的是 value 取字方式本身的性質。
        # 這個假造的對白框背景（V 中位數 106）比實際遊戲亮得多，
        # 預設低門檻是照真實畫面校準的，套到這裡會整片背景都收進來
        cfg = MaskConfig(method="value", bright_threshold=140)
        full = tm.build_mask(make_dialogue_frame(self.SEGMENTS), cfg)
        white_only = tm.build_mask(
            make_dialogue_frame([s for s in self.SEGMENTS if s[1] == DIALOGUE_WHITE]), cfg
        )
        # 多了一個橘色詞，遮罩的文字像素必須明顯變多
        assert tm.text_pixel_count(full) > tm.text_pixel_count(white_only) * 1.1

    def test_bright_method_loses_the_colored_word(self):
        """回歸測試：舊的 bright（灰階）取字方式會把變色字整段吃掉。

        門檻寫死 170 而不是用預設值：這裡要驗證的是灰階本身的缺陷，
        不該因為預設門檻調動就失去意義。
        """
        cfg = MaskConfig(method="bright", bright_threshold=170, clahe=False)
        full = tm.build_mask(make_dialogue_frame(self.SEGMENTS), cfg)
        white_only = tm.build_mask(
            make_dialogue_frame([s for s in self.SEGMENTS if s[1] == DIALOGUE_WHITE]), cfg
        )
        assert tm.text_pixel_count(full) == pytest.approx(
            tm.text_pixel_count(white_only), rel=0.05
        ), "本測試假設灰階門檻會濾掉橘字；若不成立代表 bright 已不再是問題"

    def test_colorkey_needs_the_colour_in_its_palette(self):
        without = MaskConfig(method="colorkey", text_colors=[DIALOGUE_WHITE])
        with_orange = MaskConfig(
            method="colorkey", text_colors=[DIALOGUE_WHITE, ACCENT_ORANGE]
        )
        frame = make_dialogue_frame(self.SEGMENTS)
        assert tm.text_pixel_count(tm.build_mask(frame, with_orange)) > (
            tm.text_pixel_count(tm.build_mask(frame, without)) * 1.1
        )

    def test_colorkey_rejects_empty_palette(self):
        with pytest.raises(ValueError):
            tm.build_mask(make_dialogue_frame(self.SEGMENTS),
                          MaskConfig(method="colorkey", text_colors=[]))

    def test_bright_scene_background_is_not_mistaken_for_text(self):
        """就算場景是白的，漸變變暗後也不該被當成文字。"""
        # 門檻寫死而不是用預設值：這裡驗的是 value 取字方式本身的性質。
        # 這個假造的對白框背景（V 中位數 106）比實際遊戲亮得多，
        # 預設低門檻是照真實畫面校準的，套到這裡會整片背景都收進來
        cfg = MaskConfig(method="value", bright_threshold=140)
        blank = tm.build_mask(make_dialogue_frame([], bright_scene=True), cfg)
        assert tm.text_pixel_count(blank) < cfg.min_text_pixels

    def test_speaker_blue_is_captured_by_value_method(self):
        cfg = MaskConfig(method="value", min_text_pixels=8)
        mask = tm.build_mask(make_dialogue_frame([("Cyan(11201)", SPEAKER_BLUE)]), cfg)
        assert tm.text_pixel_count(mask) > cfg.min_text_pixels


class TestOcrInputImages:
    """送進 OCR 的影像不能變形，而且要保留筆畫的灰階層次。"""

    SEGMENTS = [("This is the", DIALOGUE_WHITE), ("reward", ACCENT_ORANGE)]

    def _inputs(self, upscale: int = 2):
        from lqa.record.recorder import _ocr_input

        # 門檻同上，這個假造背景比實際遊戲亮，不能靠預設值
        cfg = MaskConfig(method="value", upscale=upscale, bright_threshold=140)
        frame = make_dialogue_frame(self.SEGMENTS)
        mask = tm.build_mask(frame, cfg)
        return frame, mask, cfg, _ocr_input

    def test_upscale_keeps_aspect_ratio(self):
        """回歸測試：fx 與 fy 不同會把字拉長變形。"""
        frame, mask, cfg, ocr_input = self._inputs(upscale=3)
        out = ocr_input(frame, mask, cfg, "masked_gray")
        assert out.shape[0] == frame.shape[0] * 3
        assert out.shape[1] == frame.shape[1] * 3

    def test_upscale_one_is_untouched(self):
        frame, mask, cfg, ocr_input = self._inputs(upscale=1)
        out = ocr_input(frame, mask, cfg, "masked_gray")
        assert out.shape[:2] == frame.shape[:2]

    def test_masked_gray_is_white_background_dark_text(self):
        frame, mask, cfg, ocr_input = self._inputs(upscale=1)
        out = ocr_input(frame, mask, cfg, "masked_gray")
        # 遮罩會先膨脹一圈把抗鋸齒邊緣納進來，所以「背景」要看膨脹範圍之外
        grow = MaskConfig().ocr_grow
        far_background = cv2.dilate(
            mask, np.ones((3, 3), np.uint8), iterations=grow + 1
        ) == 0
        assert out[far_background].min() == 255, "離文字夠遠的地方應該全白"
        assert out[mask > 0].mean() < 80, "文字應該是深色"

    def test_masked_gray_keeps_a_halo_around_the_strokes(self):
        """膨脹那一圈就是讓字看起來銳利的抗鋸齒邊緣，不能被挖掉。"""
        frame, mask, cfg, ocr_input = self._inputs(upscale=1)
        out = ocr_input(frame, mask, cfg, "masked_gray")
        halo = (cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0) & (mask == 0)
        assert halo.any()
        assert (out[halo] < 255).any(), "遮罩邊緣外圈應該保留部分亮度資訊"

    def test_masked_gray_keeps_intermediate_tones(self):
        """純二值只有 0 和 255；保留灰階才留得住抗鋸齒邊緣。"""
        frame, mask, cfg, ocr_input = self._inputs(upscale=1)
        soft = ocr_input(frame, mask, cfg, "masked_gray")
        hard = ocr_input(frame, mask, cfg, "mask")
        assert len(np.unique(hard)) == 2
        assert len(np.unique(soft)) > 2

    def test_mask_source_is_white_background(self):
        frame, mask, cfg, ocr_input = self._inputs(upscale=1)
        out = ocr_input(frame, mask, cfg, "mask")
        assert out[mask == 0].min() == 255
        assert out[mask > 0].max() == 0

    def test_colored_word_survives_masked_gray(self):
        """橘字不能在挖背景的過程中被一起挖掉。"""
        from lqa.record.recorder import _ocr_input

        cfg = MaskConfig(method="value", upscale=1, bright_threshold=140)
        with_orange = make_dialogue_frame(self.SEGMENTS)
        white_only = make_dialogue_frame([self.SEGMENTS[0]])
        dark_with = (_ocr_input(with_orange, tm.build_mask(with_orange, cfg),
                                cfg, "masked_gray") < 128).sum()
        dark_without = (_ocr_input(white_only, tm.build_mask(white_only, cfg),
                                   cfg, "masked_gray") < 128).sum()
        assert dark_with > dark_without * 1.1


class TestHexToBgr:
    def test_parses_six_digit(self):
        assert tm.hex_to_bgr("#ff8a00") == (0, 138, 255)

    def test_parses_without_hash_and_shorthand(self):
        assert tm.hex_to_bgr("fefefe") == (254, 254, 254)
        assert tm.hex_to_bgr("#f80") == (0, 136, 255)

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            tm.hex_to_bgr("#12345")


class TestMaskSuppressesMovingBackground:
    """整個設計的關鍵假設：遮罩要濾掉會動的背景，只留文字。

    現在的逐句判準看的是「舊筆畫消失了多少」，所以這裡驗的也是消失量：
    背景在動不該讓筆畫消失，換句才會。
    """

    @staticmethod
    def _removed(before: np.ndarray, after: np.ndarray) -> int:
        from lqa.detect.linetracker import _removed_pixels

        return _removed_pixels(before, after)

    def test_same_text_different_background_removes_almost_nothing(self):
        line = "Take it easy. Do your best."
        base = tm.build_mask(make_frame(line, phase=0), MASK_CFG)
        limit = StabilityConfig().min_changed_pixels
        for phase in (5, 11, 23, 57):
            later = tm.build_mask(make_frame(line, phase=phase), MASK_CFG)
            removed = self._removed(base, later)
            assert removed < limit, (
                f"背景移動造成 {removed} 個筆畫像素消失，會被誤判成換句"
            )

    def test_raw_pixel_diff_would_have_false_triggered(self):
        """對照組：證明直接比對原始像素確實會被背景騙過去。"""
        line = "Take it easy. Do your best."
        a = tm.to_gray(make_frame(line, phase=0))
        b = tm.to_gray(make_frame(line, phase=23))
        raw_diff = float(np.count_nonzero(a != b)) / a.size
        assert raw_diff > 0.5, "測試用背景動得不夠，這個對照組沒有意義"

    def test_changing_the_line_removes_a_lot(self):
        before = tm.build_mask(make_frame("Take it easy."), MASK_CFG)
        after = tm.build_mask(make_frame("No matter how it turns out."), MASK_CFG)
        removed = self._removed(before, after)
        assert removed > tm.text_pixel_count(before) * StabilityConfig().line_change_ratio

    def test_typing_one_more_character_removes_nothing(self):
        """打字只會增加筆畫。這是逐句偵測成立的前提。"""
        line = "No matter how it turns out."
        before = tm.build_mask(make_frame(line[:10], phase=10), MASK_CFG)
        after = tm.build_mask(make_frame(line[:11], phase=10), MASK_CFG)
        assert self._removed(before, after) < StabilityConfig().min_changed_pixels
        assert tm.text_pixel_count(after) > tm.text_pixel_count(before)

    def test_empty_box_has_almost_no_text_pixels(self):
        mask = tm.build_mask(make_frame("", phase=9), MASK_CFG)
        assert tm.text_pixel_count(mask) < MASK_CFG.min_text_pixels



class TestTypewriterLineDetection:
    """打字機效果下的逐句偵測。

    判準不是「等畫面靜止」，而是「打字只會增加筆畫、換句才會讓舊筆畫消失」。
    舊做法要求連續數幀靜止，實測漏掉將近一半的句子，漏的幾乎都是短句。
    """

    def _events(self, frames, cfg: StabilityConfig | None = None):
        cfg = cfg or StabilityConfig()
        tracker = LineTracker(cfg, MASK_CFG.min_text_pixels)
        out = []
        for frame in frames:
            event = tracker.feed(frame, tm.build_mask(frame, MASK_CFG))
            if event is not None:
                out.append(event)
        final = tracker.flush()
        if final is not None:
            out.append(final)
        return out

    @staticmethod
    def _typing(line: str, phase_base: int = 0) -> list[np.ndarray]:
        return [make_frame(line[:n], phase=phase_base + n)
                for n in range(1, len(line) + 1)]

    def test_captures_the_complete_sentence(self):
        line = "No matter how it turns out."
        events = self._events(self._typing(line))
        assert len(events) == 1
        full = tm.build_mask(make_frame(line, phase=0), MASK_CFG)
        captured = tm.build_mask(events[0].frame, MASK_CFG)
        # 保留下來的必須是打完的那一幀，不能是打到一半
        assert tm.text_pixel_count(captured) >= tm.text_pixel_count(full) * 0.95

    def test_no_idle_period_is_needed(self):
        """回歸測試：短句打完立刻換下一句，中間沒有任何靜止期。

        舊做法要求連續 4 幀靜止，這種情況整句都會漏掉。
        """
        frames = self._typing("Found it.") + self._typing("It was nothing.", 50)
        events = self._events(frames)
        assert len(events) == 2

    def test_a_pause_mid_typing_does_not_split_the_line(self):
        """刪節號會讓打字機停頓，舊做法會把停頓誤判成打完而錄到半句。"""
        line = "Though... thanks for helping me through all this."
        frames = self._typing(line[:8])            # "Though.."
        frames += [make_frame(line[:8], phase=200 + p) for p in range(8)]   # 停頓
        frames += [make_frame(line[:n], phase=n) for n in range(9, len(line) + 1)]
        events = self._events(frames)
        assert len(events) == 1
        captured = tm.text_pixel_count(tm.build_mask(events[0].frame, MASK_CFG))
        partial = tm.text_pixel_count(tm.build_mask(make_frame(line[:8]), MASK_CFG))
        assert captured > partial * 2, "應該保留完整句，不是停頓當下的半句"

    def test_moving_background_does_not_split_the_line(self):
        line = "Take it easy."
        frames = [make_frame(line, phase=p) for p in range(20)]
        assert len(self._events(frames)) == 1

    def test_two_sentences_separated_by_a_blank_box(self):
        frames = [make_frame("Take it easy.", phase=p) for p in range(6)]
        frames += [make_frame("", phase=p) for p in range(6, 10)]
        frames += [make_frame("Do your best.", phase=p) for p in range(10, 16)]
        assert len(self._events(frames)) == 2

    def test_repeated_identical_line_after_blank_is_reported_twice(self):
        line = "Nev..."
        frames = [make_frame(line, phase=p) for p in range(6)]
        frames += [make_frame("", phase=p) for p in range(6, 10)]
        frames += [make_frame(line, phase=p) for p in range(10, 16)]
        events = self._events(frames)
        assert len(events) == 2
        assert all(e.blanked_before for e in events)

    def test_flush_is_required_for_the_last_line(self):
        """最後一句沒有下一句可以觸發，不 flush 就會遺失。"""
        tracker = LineTracker(StabilityConfig(), MASK_CFG.min_text_pixels)
        for frame in self._typing("It was nothing."):
            assert tracker.feed(frame, tm.build_mask(frame, MASK_CFG)) is None
        assert tracker.flush() is not None

    def test_sample_count_is_reported(self):
        """取樣次數太少代表可能沒抓到顯示完整的那一刻，要能回報。"""
        events = self._events(self._typing("Found it."))
        assert events[0].samples == len("Found it.")

class TestOcrOnSyntheticFrame:
    """真的跑一次 OCR，確認二值化遮罩餵給 OCR 讀得出來。"""

    @pytest.fixture(scope="class")
    @classmethod
    def engine(cls):
        pytest.importorskip("rapidocr", reason="未安裝 OCR：pip install -r requirements-ocr.txt")
        from lqa.ocr.base import build_engine

        return build_engine("rapidocr", "en")

    def _read(self, engine, text: str) -> str:
        from lqa.record.recorder import _ocr_input

        frame = make_frame(text, phase=17)
        mask = tm.build_mask(frame, MASK_CFG)
        return engine.read(_ocr_input(frame, mask, MASK_CFG, "mask")).text

    def test_reads_text_through_translucent_box(self, engine):
        from lqa.compare.normalize import similarity

        expected = "No matter how it turns out."
        assert similarity(expected, self._read(engine, expected)) > 0.9

    def test_empty_box_reads_nothing(self, engine):
        assert self._read(engine, "").strip() == ""
