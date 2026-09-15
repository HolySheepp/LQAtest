"""擷取後端的選擇邏輯。

mss 抓的是「螢幕上那塊區域」，任何視窗蓋在模擬器上面就會抓到那個視窗，
而且完全不報錯：校準會框到別的東西，錄製會錄到一堆垃圾。
PrintWindow 是請視窗自己畫給我們，被蓋住也沒關係，所以優先用它，
但硬體加速的視窗有可能回黑畫面，所以要實際抓一張確認才採用。
"""

from __future__ import annotations

import numpy as np
import pytest

from lqa.capture import mss_backend
from lqa.capture.gdi_backend import looks_blank


class TestLooksBlank:
    def test_all_black_is_blank(self):
        assert looks_blank(np.zeros((100, 100, 3), np.uint8))

    def test_single_colour_is_blank(self):
        assert looks_blank(np.full((100, 100, 3), 77, np.uint8))

    def test_none_is_blank(self):
        assert looks_blank(None)

    def test_empty_array_is_blank(self):
        assert looks_blank(np.zeros((0, 0, 3), np.uint8))

    def test_real_content_is_not_blank(self):
        rng = np.random.default_rng(0)
        frame = rng.integers(0, 255, (100, 100, 3), dtype=np.uint8)
        assert not looks_blank(frame)

    def test_dark_scene_with_text_is_not_blank(self):
        """對白場景可能整體很暗，不能因為暗就判成抓取失敗。"""
        frame = np.full((100, 200, 3), 12, np.uint8)
        frame[40:60, 20:180] = 254
        assert not looks_blank(frame)


class FakePrintWindow:
    instances: list["FakePrintWindow"] = []

    def __init__(self, title, *_args, **_kwargs):
        self.title = title
        FakePrintWindow.instances.append(self)

    def close(self):
        pass


class FakeHybrid:
    instances: list["FakeHybrid"] = []

    def __init__(self, title, roi=None, *_args, **_kwargs):
        self.title = title
        self.roi = roi
        FakeHybrid.instances.append(self)


class FakeMss:
    instances: list["FakeMss"] = []

    def __init__(self, title, region, *_args, **_kwargs):
        self.title = title
        self.region = region
        FakeMss.instances.append(self)


@pytest.fixture
def fake_backends(monkeypatch):
    for fake in (FakePrintWindow, FakeHybrid, FakeMss):
        fake.instances.clear()
    monkeypatch.setattr(mss_backend, "MssCapture", FakeMss)

    import lqa.capture.gdi_backend as gdi
    import lqa.capture.hybrid as hybrid

    monkeypatch.setattr(gdi, "PrintWindowCapture", FakePrintWindow)
    monkeypatch.setattr(hybrid, "HybridCapture", FakeHybrid)
    return None


class TestOpenCapture:
    """預設走混合模式：平常用便宜的 mss，偵測到被蓋住才用 PrintWindow。"""

    def test_auto_uses_the_hybrid_backend(self, fake_backends):
        capture = mss_backend.open_capture("測試", None, "auto")
        assert isinstance(capture, FakeHybrid)
        assert not FakeMss.instances
        assert not FakePrintWindow.instances

    def test_auto_passes_the_roi_so_only_it_is_watched(self, fake_backends):
        """整個視窗被蓋住一角但對白框沒事時，沒必要付 PrintWindow 的代價。"""
        roi = (91, 799, 404, 161)
        capture = mss_backend.open_capture("測試", None, "auto", roi=roi)
        assert capture.roi == roi

    def test_explicit_printwindow_is_honoured(self, fake_backends):
        capture = mss_backend.open_capture("測試", None, "printwindow")
        assert isinstance(capture, FakePrintWindow)

    def test_explicit_mss_never_tries_print_window(self, fake_backends):
        capture = mss_backend.open_capture("測試", (0, 0, 10, 10), "mss")
        assert isinstance(capture, FakeMss)
        assert not FakePrintWindow.instances

    def test_region_only_profile_uses_mss(self, fake_backends):
        """只給絕對座標、沒有視窗標題時，視窗相關的後端都無從施力。"""
        capture = mss_backend.open_capture(None, (0, 0, 10, 10), "auto")
        assert isinstance(capture, FakeMss)

    def test_unknown_backend_raises(self, fake_backends):
        with pytest.raises(ValueError):
            mss_backend.open_capture("測試", None, "magic")


class TestProfileRoundTrip:
    def test_backend_survives_save_and_load(self, tmp_path):
        from lqa.config import Profile

        path = Profile(name="t", window_title="測試", body_roi=(0, 0, 10, 10),
                       capture_backend="mss").save(tmp_path / "p.json")
        assert Profile.load(path).capture_backend == "mss"

    def test_defaults_to_auto_for_older_profiles(self, tmp_path):
        import json

        from lqa.config import Profile

        path = tmp_path / "old.json"
        path.write_text(json.dumps({"name": "t", "window_title": "測試",
                                    "body_roi": [0, 0, 10, 10]}), encoding="utf-8")
        assert Profile.load(path).capture_backend == "auto"

    def test_ocr_grow_defaults_for_older_profiles(self, tmp_path):
        import json

        from lqa.config import MaskConfig, Profile

        path = tmp_path / "old.json"
        path.write_text(json.dumps({"name": "t", "window_title": "測試",
                                    "body_roi": [0, 0, 10, 10],
                                    "mask": {"method": "value"}}), encoding="utf-8")
        assert Profile.load(path).mask.ocr_grow == MaskConfig().ocr_grow
