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

from lqa.config import MaskConfig, StabilityConfig
from lqa.detect import textmask as tm
from lqa.detect.stability import StabilityTracker

W, H = 900, 160
BOX_ALPHA = 0.55

# 白字配深色半透明框，用 bright 門檻抽遮罩最乾淨
MASK_CFG = MaskConfig(method="bright", bright_threshold=170, clahe=False, blur=3,
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


class TestMaskSuppressesMovingBackground:
    """這是整個設計的關鍵假設，如果不成立就得換做法。"""

    def test_same_text_different_background_yields_near_identical_mask(self):
        line = "Take it easy. Do your best."
        masks = [
            tm.build_mask(make_frame(line, phase=p), MASK_CFG)
            for p in (0, 5, 11, 23)
        ]
        for later in masks[1:]:
            diff = tm.mask_change_ratio(masks[0], later, MASK_CFG.min_text_pixels)
            assert diff < StabilityConfig().diff_threshold, (
                f"背景移動造成的遮罩差異 {diff:.4f} 超過門檻，"
                "變動偵測會被背景誤觸發"
            )

    def test_raw_pixel_diff_would_have_false_triggered(self):
        """對照組：證明直接比對原始像素確實會被背景騙過去。"""
        line = "Take it easy. Do your best."
        a = tm.to_gray(make_frame(line, phase=0))
        b = tm.to_gray(make_frame(line, phase=23))
        raw_diff = float(np.count_nonzero(a != b)) / a.size
        assert raw_diff > 0.5, "測試用背景動得不夠，這個對照組沒有意義"

    def test_text_change_does_move_the_mask(self):
        before = tm.build_mask(make_frame("Take it easy."), MASK_CFG)
        after = tm.build_mask(make_frame("No matter how it turns out."), MASK_CFG)
        diff = tm.mask_change_ratio(before, after, MASK_CFG.min_text_pixels)
        assert diff > StabilityConfig().diff_threshold

    def test_one_more_character_outweighs_background_noise(self):
        """打一個字的變動量必須明顯大於背景雜訊，否則門檻無處可放。"""
        line = "No matter how it turns out."
        floor = MASK_CFG.min_text_pixels
        full = tm.build_mask(make_frame(line, phase=0), MASK_CFG)
        bg_noise = max(
            tm.mask_change_ratio(full, tm.build_mask(make_frame(line, phase=p), MASK_CFG), floor)
            for p in (5, 11, 23, 57)
        )
        # 用視窗（stable_frames 幀）的累積量比，這才是追蹤器實際採用的訊號
        window = StabilityConfig().stable_frames
        typing = tm.mask_change_ratio(
            tm.build_mask(make_frame(line[:10], phase=10), MASK_CFG),
            tm.build_mask(make_frame(line[: 10 + window], phase=10 + window), MASK_CFG),
            floor,
        )
        assert typing > bg_noise * 3, f"分離度不足：背景 {bg_noise:.4f} vs 打字 {typing:.4f}"
        assert bg_noise < StabilityConfig().diff_threshold < typing

    def test_empty_box_has_almost_no_text_pixels(self):
        mask = tm.build_mask(make_frame("", phase=9), MASK_CFG)
        assert tm.text_pixel_count(mask) < MASK_CFG.min_text_pixels


class TestTypewriterStability:
    def _run(self, frames, cfg: StabilityConfig) -> list[int]:
        """跑一遍追蹤器，回傳觸發擷取的幀索引。"""
        tracker = StabilityTracker(cfg, MASK_CFG.min_text_pixels)
        fired: list[int] = []
        for idx, frame in enumerate(frames):
            event = tracker.feed(
                tm.build_mask(frame, MASK_CFG),
                now_ms=idx * cfg.poll_interval_ms,
            )
            if event is not None:
                fired.append(idx)
        return fired

    @staticmethod
    def _typing_then_idle(line: str, idle: int = 10) -> list[np.ndarray]:
        """逐字打字，打完後背景繼續動但文字靜止。"""
        frames = [make_frame(line[:n], phase=n) for n in range(1, len(line) + 1)]
        frames += [make_frame(line, phase=100 + p) for p in range(idle)]
        return frames

    def test_fires_once_after_typing_completes(self):
        line = "No matter how it turns out."
        cfg = StabilityConfig(min_gap_ms=0)
        fired = self._run(self._typing_then_idle(line), cfg)
        assert len(fired) == 1, f"應該只觸發一次，實際觸發於 {fired}"
        assert fired[0] >= len(line), "不該在文字打完之前就觸發"

    def test_never_fires_while_still_typing(self):
        """回歸測試。

        舊版門檻用 ROI 面積正規化又只比對前一幀，打字中每一幀都被判定成
        穩定，結果會在打到第三個字時就擷取，錄到的全是半句話。
        """
        line = "No matter how it turns out."
        cfg = StabilityConfig(min_gap_ms=0)
        typing_only = [make_frame(line[:n], phase=n) for n in range(1, len(line) + 1)]
        assert self._run(typing_only, cfg) == [], "打字過程中不該觸發任何擷取"

    def test_captures_the_complete_sentence(self):
        line = "No matter how it turns out."
        cfg = StabilityConfig(min_gap_ms=0)
        frames = self._typing_then_idle(line)

        tracker = StabilityTracker(cfg, MASK_CFG.min_text_pixels)
        captured = None
        for idx, frame in enumerate(frames):
            event = tracker.feed(tm.build_mask(frame, MASK_CFG), idx * cfg.poll_interval_ms)
            if event is not None:
                captured = frames[idx]
        assert captured is not None
        # 擷取到的那一幀必須是完整句子，不能是打到一半
        full = tm.build_mask(make_frame(line, phase=0), MASK_CFG)
        diff = tm.mask_change_ratio(
            full, tm.build_mask(captured, MASK_CFG), MASK_CFG.min_text_pixels
        )
        assert diff < cfg.diff_threshold

    def test_two_sentences_fire_twice(self):
        cfg = StabilityConfig(min_gap_ms=0)
        frames = [make_frame("Take it easy.", phase=p) for p in range(10)]
        frames += [make_frame("", phase=p) for p in range(10, 16)]
        frames += [make_frame("Do your best.", phase=p) for p in range(16, 26)]
        assert len(self._run(frames, cfg)) == 2

    def test_repeated_identical_line_after_blank_is_captured_twice(self):
        """同一句連續出現兩次時，只要中間經過空白畫面就不會被當成重複。"""
        cfg = StabilityConfig(min_gap_ms=0)
        line = "Nev..."
        frames = [make_frame(line, phase=p) for p in range(10)]
        frames += [make_frame("", phase=p) for p in range(10, 16)]
        frames += [make_frame(line, phase=p) for p in range(16, 26)]
        assert len(self._run(frames, cfg)) == 2


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
