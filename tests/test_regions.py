"""四種框選範圍，以及解析時怎麼挑出畫面上正在用的那一套。

遊戲有兩種對白版面：一般劇情對白，以及 NPC 對白，框在畫面上位置不同。
拍攝時使用者不會告訴軟體現在是哪一種，所以解析時得自己看出來 ——
挑錯版面會裁到空白處，整句變成「遊戲中沒這句」。
"""

from __future__ import annotations

import numpy as np
import pytest

from lqa.config import (MASK_FALLBACK, REGION_KEYS, MaskConfig, Profile,
                        RegionSet)
from lqa.record.reader import pick_layout

WHITE, GREY = 254, 150


def box(shape=(200, 400)) -> np.ndarray:
    """深色的對白框底，亮度遠低於低門檻。"""
    return np.full((*shape, 3), 40, np.uint8)


def write(frame: np.ndarray, roi: tuple[int, int, int, int]) -> None:
    """在 roi 裡畫幾筆假的白字。"""
    x, y, w, h = roi
    for column in range(x + 6, x + w - 6, 14):
        frame[y + 5:y + h - 5, column:column + 3] = WHITE
        frame[y + 5:y + h - 5, column + 3:column + 5] = GREY


NORMAL_BODY = (10, 140, 380, 50)
NPC_BODY = (10, 20, 380, 50)


def profile_with_npc() -> Profile:
    return Profile(window_title="測試",
                   body_roi=NORMAL_BODY, speaker_roi=(10, 120, 120, 18),
                   npc_body_roi=NPC_BODY, npc_speaker_roi=(10, 4, 120, 14))


class TestRegionPlumbing:
    def test_four_regions_exist(self):
        assert REGION_KEYS == ["body", "speaker", "npc_body", "npc_speaker"]

    def test_roi_round_trips_through_the_region_name(self):
        profile = Profile(window_title="測試")
        for index, key in enumerate(REGION_KEYS):
            profile.set_roi(key, (index, index, 10, 10))
        assert [profile.roi_of(k)[0] for k in REGION_KEYS] == [0, 1, 2, 3]

    def test_unknown_region_is_rejected(self):
        with pytest.raises(KeyError):
            Profile(window_title="測試").roi_of("boss_body")

    def test_every_region_has_a_fallback_entry(self):
        """漏一個就會在取用時 KeyError，而且只有那個範圍會炸。"""
        assert set(MASK_FALLBACK) == set(REGION_KEYS)


class TestMaskFallback:
    def test_npc_borrows_the_normal_settings_until_tuned(self):
        """NPC 的框通常和一般對白長得一樣，沒調過就沿用，不必重調一次。"""
        profile = Profile(window_title="測試", mask=MaskConfig(bright_threshold=77))
        assert profile.mask_for("npc_body").bright_threshold == 77

    def test_npc_speaker_prefers_the_normal_speaker_settings(self):
        """姓名框有自己的色盤（白或淺藍），要退給姓名框而不是對白框。"""
        profile = Profile(window_title="測試",
                          speaker_mask=MaskConfig(text_colors=["#5dbcfe"]))
        assert profile.mask_for("npc_speaker").text_colors == ["#5dbcfe"]

    def test_tuned_npc_settings_win(self):
        profile = Profile(window_title="測試", mask=MaskConfig(bright_threshold=77),
                          npc_mask=MaskConfig(bright_threshold=123))
        assert profile.mask_for("npc_body").bright_threshold == 123


class TestLayouts:
    def test_only_normal_when_npc_is_not_framed(self):
        profile = Profile(window_title="測試", body_roi=NORMAL_BODY)
        assert [layout.key for layout in profile.layouts()] == ["normal"]

    def test_both_when_npc_is_framed(self):
        assert [layout.key for layout in profile_with_npc().layouts()] == \
            ["normal", "npc"]

    def test_watch_roi_covers_both_dialogue_boxes(self):
        """只盯一般對白框的話，NPC 對白被蓋住時不會退回 PrintWindow。"""
        watch = profile_with_npc().watch_roi()
        assert watch == (10, 20, 380, 170)

    def test_watch_roi_is_none_before_calibration(self):
        assert Profile(window_title="測試").watch_roi() is None


class TestPickLayout:
    def setup_method(self):
        self.layouts = profile_with_npc().layouts()

    def test_picks_the_box_that_has_text(self):
        frame = box()
        write(frame, NPC_BODY)
        layout, _mask = pick_layout(frame, self.layouts)
        assert layout.key == "npc"

    def test_picks_normal_when_normal_has_text(self):
        frame = box()
        write(frame, NORMAL_BODY)
        layout, _mask = pick_layout(frame, self.layouts)
        assert layout.key == "normal"

    def test_empty_frame_falls_back_to_normal(self):
        """拍到沒有對白的畫面是常事，要當成「這句沒抓到」而不是解析失敗。"""
        layout, mask = pick_layout(box(), self.layouts)
        assert layout.key == "normal"
        assert mask is not None

    def test_single_layout_profile_is_unaffected(self):
        profile = Profile(window_title="測試", body_roi=NORMAL_BODY)
        frame = box()
        write(frame, NORMAL_BODY)
        layout, _mask = pick_layout(frame, profile.layouts())
        assert layout.key == "normal"

    def test_returned_mask_belongs_to_the_chosen_layout(self):
        """回傳的遮罩會直接送去 OCR，配錯版面就是辨識空白。"""
        frame = box()
        write(frame, NPC_BODY)
        layout, mask = pick_layout(frame, self.layouts)
        assert mask.shape[:2] == (layout.body_roi[3], layout.body_roi[2])
        assert mask.max() == 255


class TestBackwardCompatibility:
    def test_old_profiles_load_without_npc_fields(self, tmp_path):
        import json

        path = tmp_path / "old.json"
        path.write_text(json.dumps({"name": "t", "window_title": "測試",
                                    "body_roi": [1, 2, 3, 4]}), encoding="utf-8")
        profile = Profile.load(path)
        assert profile.npc_body_roi is None
        assert [layout.key for layout in profile.layouts()] == ["normal"]

    def test_npc_fields_survive_a_save_and_load(self, tmp_path):
        path = tmp_path / "p.json"
        profile_with_npc().save(path)
        assert Profile.load(path).npc_body_roi == NPC_BODY
