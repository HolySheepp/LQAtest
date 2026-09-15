"""混合擷取後端的路徑選擇。

實測（輪詢 60ms，模擬器 dnplayer.exe 的 CPU 使用率）：
    不擷取（基準）        0.4 %
    PrintWindow 輪詢     5.4 %      約七倍
    mss 輪詢             0.8 %      和基準沒有差別

PrintWindow 的成本落在模擬器身上（它強迫整個視窗重繪），使用者會感覺卡。
但 mss 被蓋住就會抓到蓋在上面的東西而且不報錯。
所以先用 WindowFromPoint 檢查（十幾微秒）決定走哪條路。
"""

from __future__ import annotations

import numpy as np
import pytest

from lqa.capture import hybrid


@pytest.fixture
def fake_win32(monkeypatch):
    """把所有 Win32 呼叫換成假的，這段邏輯才測得動。"""
    state = {
        "covered": False,
        "minimized": False,
        "print_window_result": np.full((10, 20, 3), 7, np.uint8),
        "mss_result": np.full((10, 20, 4), 3, np.uint8),
        "print_window_calls": 0,
        "mss_calls": 0,
    }

    class FakeSct:
        def grab(self, box):
            state["mss_calls"] += 1
            return state["mss_result"]

        def close(self):
            pass

    class FakeMssModule:
        @staticmethod
        def MSS():
            return FakeSct()

    def fake_print_window(hwnd, w, h):
        state["print_window_calls"] += 1
        return state["print_window_result"]

    monkeypatch.setitem(__import__("sys").modules, "mss", FakeMssModule)
    monkeypatch.setattr(hybrid, "ensure_dpi_aware", lambda: None)
    monkeypatch.setattr(hybrid, "find_window", lambda _t: 1234)
    monkeypatch.setattr(hybrid, "client_rect_on_screen", lambda _h: (100, 50, 20, 10))
    monkeypatch.setattr(hybrid, "is_minimized", lambda _h: state["minimized"])
    monkeypatch.setattr(hybrid, "is_covered", lambda _h, _r: state["covered"])
    monkeypatch.setattr(hybrid, "print_window", fake_print_window)
    return state


class TestPathSelection:
    def test_uses_mss_when_not_covered(self, fake_win32):
        capture = hybrid.HybridCapture("測試")
        capture.grab()
        assert capture.last_path == "mss"
        assert fake_win32["print_window_calls"] == 0

    def test_uses_print_window_when_covered(self, fake_win32):
        fake_win32["covered"] = True
        capture = hybrid.HybridCapture("測試")
        frame = capture.grab()
        assert capture.last_path == "printwindow"
        assert fake_win32["mss_calls"] == 0
        assert frame[0, 0, 0] == 7

    def test_switches_back_when_uncovered(self, fake_win32):
        capture = hybrid.HybridCapture("測試")
        fake_win32["covered"] = True
        capture.grab()
        fake_win32["covered"] = False
        capture.grab()
        assert capture.last_path == "mss"
        assert fake_win32["print_window_calls"] == 1

    def test_counts_covered_frames(self, fake_win32):
        capture = hybrid.HybridCapture("測試")
        fake_win32["covered"] = True
        for _ in range(3):
            capture.grab()
        fake_win32["covered"] = False
        capture.grab()
        assert capture.covered_frames == 3

    def test_falls_back_to_mss_if_print_window_fails(self, fake_win32):
        fake_win32["covered"] = True
        fake_win32["print_window_result"] = None
        capture = hybrid.HybridCapture("測試")
        capture.grab()
        assert capture.last_path == "mss(fallback)"
        assert fake_win32["mss_calls"] == 1


class TestWatchedRegion:
    def test_watches_only_the_roi_when_given(self, fake_win32):
        """整個視窗被蓋住一角但對白框沒事時，沒必要付 PrintWindow 的代價。"""
        seen = []
        import lqa.capture.hybrid as h

        h.is_covered = lambda _hwnd, rect: seen.append(rect) or False
        capture = hybrid.HybridCapture("測試", roi=(5, 3, 8, 4))
        capture.grab()
        # 視窗在 (100, 50)，ROI 相對視窗 (5, 3) -> 螢幕 (105, 53)
        assert seen == [(105, 53, 8, 4)]

    def test_watches_the_whole_window_without_a_roi(self, fake_win32):
        seen = []
        import lqa.capture.hybrid as h

        h.is_covered = lambda _hwnd, rect: seen.append(rect) or False
        capture = hybrid.HybridCapture("測試")
        capture.grab()
        assert seen == [(100, 50, 20, 10)]


class TestMinimized:
    def test_minimized_at_open_raises(self, fake_win32):
        fake_win32["minimized"] = True
        with pytest.raises(hybrid.WindowMinimized):
            hybrid.HybridCapture("測試")

    def test_minimized_later_is_reported_not_raised(self, fake_win32):
        capture = hybrid.HybridCapture("測試")
        assert capture.unavailable() is None
        fake_win32["minimized"] = True
        capture.grab()
        assert "最小化" in (capture.unavailable() or "")

    def test_recovers_when_restored(self, fake_win32):
        capture = hybrid.HybridCapture("測試")
        fake_win32["minimized"] = True
        capture.grab()
        fake_win32["minimized"] = False
        capture.grab()
        assert capture.unavailable() is None

    def test_missing_window_raises(self, fake_win32, monkeypatch):
        monkeypatch.setattr(hybrid, "find_window", lambda _t: None)
        with pytest.raises(RuntimeError):
            hybrid.HybridCapture("測試")
