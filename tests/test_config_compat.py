"""舊的 profile.json 必須還載入得了。

演算法調整時設定欄位會增減，使用者手上的 profile 留著已移除的鍵是常態。
直接 **data 會丟 TypeError，讓他們的 profile 突然打不開，
所以載入時只取目前還存在的欄位。
"""

from __future__ import annotations

import json

import pytest

from lqa.config import MaskConfig, Profile, StabilityConfig

# 逐句偵測改版前的欄位，現在都已經不存在
REMOVED_STABILITY_KEYS = {
    "stable_frames": 4,
    "diff_threshold": 0.04,
    "min_gap_ms": 200,
    "rearm_threshold": 0.04,
}


def old_profile() -> dict:
    return {
        "name": "t",
        "window_title": "測試",
        "body_roi": [91, 799, 404, 161],
        "speaker_roi": [93, 768, 331, 31],
        "mask": {"method": "value", "bright_threshold": 85, "blur": 3},
        "stability": {"poll_interval_ms": 80, **REMOVED_STABILITY_KEYS},
        "ocr": {"engine": "rapidocr", "source": "mask"},
    }


class TestUnknownKeysAreIgnored:
    def test_removed_stability_keys_do_not_break_loading(self):
        profile = Profile.from_dict(old_profile())
        assert profile.stability.poll_interval_ms == 80

    def test_kept_keys_still_apply(self):
        profile = Profile.from_dict(old_profile())
        assert profile.mask.bright_threshold == 85
        assert profile.mask.blur == 3
        assert profile.ocr.source == "mask"

    def test_missing_keys_fall_back_to_defaults(self):
        profile = Profile.from_dict(old_profile())
        assert profile.stability.line_change_ratio == StabilityConfig().line_change_ratio
        assert profile.mask.seed_threshold == MaskConfig().seed_threshold

    def test_rois_survive(self):
        profile = Profile.from_dict(old_profile())
        assert profile.body_roi == (91, 799, 404, 161)
        assert profile.speaker_roi == (93, 768, 331, 31)

    def test_unknown_mask_key_is_ignored(self):
        data = old_profile()
        data["mask"]["some_future_option"] = 123
        assert Profile.from_dict(data).mask.method == "value"

    def test_empty_sections_use_defaults(self):
        data = {"name": "t", "window_title": "測試", "body_roi": [0, 0, 10, 10]}
        profile = Profile.from_dict(data)
        assert profile.mask.method == MaskConfig().method
        assert profile.ocr.engine == "rapidocr"

    def test_round_trip_through_a_file(self, tmp_path):
        path = tmp_path / "old.json"
        path.write_text(json.dumps(old_profile()), encoding="utf-8")
        profile = Profile.load(path)
        assert profile.stability.poll_interval_ms == 80
        # 存回去之後就是新格式，不再帶著已移除的欄位
        saved = json.loads(profile.save(tmp_path / "new.json").read_text(encoding="utf-8"))
        assert not (REMOVED_STABILITY_KEYS.keys() & saved["stability"].keys())


class TestShippedProfiles:
    def test_example_profile_loads(self):
        profile = Profile.load("config/profile.example.json")
        profile.validate()
        assert profile.body_roi is not None

    def test_example_profile_has_no_stale_keys(self):
        data = json.loads(
            __import__("pathlib").Path("config/profile.example.json")
            .read_text(encoding="utf-8")
        )
        assert not (REMOVED_STABILITY_KEYS.keys() & data["stability"].keys())


class TestValidation:
    def test_missing_body_roi_is_rejected(self):
        with pytest.raises(ValueError):
            Profile.from_dict({"name": "t", "window_title": "測試"}).validate()
