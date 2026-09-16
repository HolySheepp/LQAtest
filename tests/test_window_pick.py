"""視窗挑選邏輯的測試。

不碰真的 Win32 API，只測「有多個標題相符時要挑哪一個」這段判斷。
實務上會踩到的情況：模擬器實例名稱很短（例如「測試」），
而多開管理器 LDMultiPlayer 也在清單裡但抓不到遊戲畫面。
"""

from __future__ import annotations

import pytest

from lqa.capture import window as win


def info(hwnd: int, title: str, process: str, w: int = 596, h: int = 1026):
    return win.WindowInfo(hwnd=hwnd, title=title, process=process, width=w, height=h)


@pytest.fixture
def fake_windows(monkeypatch):
    def install(items: list[win.WindowInfo]):
        monkeypatch.setattr(win, "_IS_WINDOWS", True)
        monkeypatch.setattr(win, "list_windows", lambda: items)
    return install


class TestWindowInfoFlags:
    def test_emulator_process_is_recognised(self):
        assert info(1, "測試", "dnplayer.exe").is_emulator

    def test_manager_process_is_not_the_emulator(self):
        manager = info(1, "LDMultiPlayer", "dnmultiplayerex.exe")
        assert manager.is_emulator_manager
        assert not manager.is_emulator

    def test_unrelated_process_is_neither(self):
        other = info(1, "測試報告.docx - Word", "WINWORD.EXE")
        assert not other.is_emulator
        assert not other.is_emulator_manager

    def test_process_match_is_case_insensitive(self):
        assert info(1, "測試", "DNPlayer.EXE").is_emulator


class TestFindWindow:
    def test_picks_the_emulator_over_other_matches(self, fake_windows):
        """「測試」同時命中 Word 文件和模擬器時，要挑模擬器。"""
        fake_windows([
            info(10, "測試報告.docx - Word", "WINWORD.EXE"),
            info(20, "測試", "dnplayer.exe"),
        ])
        assert win.find_window("測試") == 20

    def test_prefers_emulator_over_manager(self, fake_windows):
        fake_windows([
            info(10, "LDMultiPlayer 測試", "dnmultiplayerex.exe"),
            info(20, "測試", "dnplayer.exe"),
        ])
        assert win.find_window("測試") == 20

    def test_avoids_manager_when_no_emulator_present(self, fake_windows):
        fake_windows([
            info(10, "LDMultiPlayer", "dnmultiplayerex.exe"),
            info(20, "LDMultiPlayer 備註.txt", "notepad.exe"),
        ])
        assert win.find_window("ldmultiplayer") == 20

    def test_falls_back_to_first_match(self, fake_windows):
        fake_windows([info(10, "LDMultiPlayer", "dnmultiplayerex.exe")])
        assert win.find_window("LDMulti") == 10

    def test_match_is_case_insensitive(self, fake_windows):
        fake_windows([info(20, "LDPlayer Instance", "dnplayer.exe")])
        assert win.find_window("ldplayer") == 20

    def test_falls_back_to_the_only_emulator_when_the_title_misses(self, fake_windows):
        """profile 存的是設定者自己的實例名稱，換一台機器幾乎一定不一樣。

        程序名稱才是穩定的識別。沒有這層退路的話，同事拿到軟體第一件事
        就是「抓不到畫面」，而且看不出要去改哪裡。
        """
        fake_windows([info(20, "朋友的模擬器", "dnplayer.exe")])
        assert win.find_window("測試") == 20

    def test_does_not_guess_between_two_emulators(self, fake_windows):
        """開了兩個實例就不能替使用者猜，猜錯會錄到另一個視窗的內容。"""
        fake_windows([
            info(20, "實例一", "dnplayer.exe"),
            info(21, "實例二", "dnplayer.exe"),
        ])
        assert win.find_window("測試") is None

    def test_no_fallback_to_the_multi_instance_manager(self, fake_windows):
        """多開管理器抓得到視窗但裡面沒有遊戲畫面。"""
        fake_windows([info(10, "LDMultiPlayer", "dnmultiplayerex.exe")])
        assert win.find_window("測試") is None

    def test_no_match_and_no_emulator_returns_none(self, fake_windows):
        fake_windows([info(20, "記事本", "notepad.exe")])
        assert win.find_window("不存在的視窗") is None

    def test_empty_window_list(self, fake_windows):
        fake_windows([])
        assert win.find_window("測試") is None
