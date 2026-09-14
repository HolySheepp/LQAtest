"""雙門檻遲滯取字。

遊戲的對白字不是均勻的純色。實測一張真實畫面的亮度分布：

    240-256   1300 px   筆畫核心（接近純白）
    200-240    865 px   ┐
    150-200    778 px   ├ 抗鋸齒過渡與灰黑描邊
    100-150   2066 px   ┘
     60-100  11816 px   背景

單一門檻對這種結構本質上就不管用：門檻放高只留核心，字被挖空；
門檻放低才收得到完整筆畫，但背景也一起進來。
遲滯門檻用高門檻找種子、低門檻取範圍，只保留連通到種子的部分。
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="需要 opencv")

from lqa.config import MaskConfig  # noqa: E402
from lqa.detect import textmask as tm  # noqa: E402

LOW, HIGH = 100, 210


def cfg(method: str = "hysteresis", **kwargs) -> MaskConfig:
    base = {"method": method, "bright_threshold": LOW, "seed_threshold": HIGH,
            "blur": 0, "clahe": False}
    return MaskConfig(**{**base, **kwargs})


def glyph_frame(background: int = 40) -> np.ndarray:
    """仿造真實字形：純白核心，往外遞減成灰白過渡與灰黑描邊。"""
    frame = np.full((80, 300, 3), background, np.uint8)
    core = np.zeros((80, 300), np.uint8)
    # 筆畫刻意畫細：真實畫面裡核心只佔整個字約四分之一，
    # 筆畫太粗的話核心佔比失真，測不出「高門檻會把字挖空」。
    cv2.putText(core, "Text", (20, 55), cv2.FONT_HERSHEY_DUPLEX, 1.2, 255, 1, cv2.LINE_AA)

    # 由核心往外擴出三層，亮度逐層遞減，模擬抗鋸齒
    layers = [(core, 255)]
    previous = core
    for value in (215, 160, 115):
        grown = cv2.dilate(previous, np.ones((3, 3), np.uint8))
        layers.append((cv2.subtract(grown, previous), value))
        previous = grown
    for region, value in reversed(layers):
        frame[region > 0] = value
    return frame


class TestGlyphStructure:
    def test_fixture_really_has_a_gradient(self):
        """先確認測試素材本身符合使用者描述的結構，否則測試沒有意義。"""
        value = tm.value_channel(glyph_frame())
        for low, high in ((240, 256), (200, 240), (150, 200), (100, 150)):
            band = int(((value >= low) & (value < high)).sum())
            assert band > 0, f"亮度 {low}-{high} 這一層應該要有像素"


class TestHysteresisVsSingleThreshold:
    def test_high_single_threshold_hollows_out_the_glyph(self):
        """對照組：高門檻只留核心。"""
        frame = glyph_frame()
        core_only = tm.text_pixel_count(tm.build_mask(frame, cfg("value", bright_threshold=HIGH)))
        full = tm.text_pixel_count(tm.build_mask(frame, cfg()))
        assert full > core_only * 1.5

    def test_captures_the_full_stroke(self):
        frame = glyph_frame()
        low_only = tm.text_pixel_count(tm.build_mask(frame, cfg("value", bright_threshold=LOW)))
        full = tm.text_pixel_count(tm.build_mask(frame, cfg()))
        # 背景夠暗時，遲滯抓到的筆畫量應該和低門檻差不多
        assert full == pytest.approx(low_only, rel=0.05)


class TestBackgroundRejection:
    def test_bright_background_passing_the_low_threshold_is_dropped(self):
        """關鍵性質：背景亮到過得了低門檻，但因為連不到種子而被丟掉。

        單一低門檻在這種畫面會把整片背景收進來。
        """
        frame = glyph_frame()
        frame[0:20, 200:290] = 150          # 一塊比低門檻亮、但沒有純白核心的背景

        single = tm.build_mask(frame, cfg("value", bright_threshold=LOW))
        hyst = tm.build_mask(frame, cfg())
        patch = (slice(0, 20), slice(200, 290))

        assert tm.text_pixel_count(single[patch]) > 1000, "單一門檻應該會收進這塊背景"
        assert tm.text_pixel_count(hyst[patch]) == 0, "遲滯門檻應該要丟掉它"

    def test_bright_background_touching_the_glyph_is_still_dropped_if_dim(self):
        """只有連通『而且』有種子的元件才會被留下。"""
        frame = glyph_frame()
        frame[0:10, 0:300] = 120            # 橫跨整張、不含核心的亮帶
        mask = tm.build_mask(frame, cfg())
        assert tm.text_pixel_count(mask[0:8, :]) == 0

    def test_a_bright_blob_with_a_core_is_kept(self):
        """對照組：有純白核心的東西本來就該留下。"""
        frame = glyph_frame()
        frame[5:15, 240:280] = 250
        mask = tm.build_mask(frame, cfg())
        assert tm.text_pixel_count(mask[5:15, 240:280]) > 300


class TestEdgeCases:
    def test_no_seed_means_empty_mask(self):
        """整張都沒有夠亮的核心時回空遮罩，不能把低門檻的東西全收。"""
        frame = np.full((40, 80, 3), 150, np.uint8)
        assert tm.text_pixel_count(tm.build_mask(frame, cfg())) == 0

    def test_blank_frame(self):
        frame = np.full((40, 80, 3), 10, np.uint8)
        assert tm.text_pixel_count(tm.build_mask(frame, cfg())) == 0

    def test_grayscale_input_is_accepted(self):
        gray = tm.to_gray(glyph_frame())
        assert tm.text_pixel_count(tm.build_mask(gray, cfg())) > 0

    def test_colored_text_is_kept(self):
        """變色字的 V 值和白字同一個量級，一樣有種子。"""
        frame = np.full((80, 300, 3), 40, np.uint8)
        cv2.putText(frame, "Text", (20, 55), cv2.FONT_HERSHEY_DUPLEX, 1.4,
                    tm.hex_to_bgr("#ff8a00"), 2, cv2.LINE_AA)
        assert tm.text_pixel_count(tm.build_mask(frame, cfg())) > 200


class TestProfileRoundTrip:
    def test_seed_threshold_survives_save_and_load(self, tmp_path):
        from lqa.config import Profile

        path = Profile(name="t", window_title="測試", body_roi=(0, 0, 10, 10),
                       mask=cfg(seed_threshold=222)).save(tmp_path / "p.json")
        assert Profile.load(path).mask.seed_threshold == 222

    def test_older_profiles_get_a_default(self, tmp_path):
        import json

        from lqa.config import Profile

        path = tmp_path / "old.json"
        path.write_text(json.dumps({"name": "t", "window_title": "測試",
                                    "body_roi": [0, 0, 10, 10],
                                    "mask": {"method": "value"}}), encoding="utf-8")
        assert Profile.load(path).mask.seed_threshold == MaskConfig().seed_threshold
