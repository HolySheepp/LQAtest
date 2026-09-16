"""快捷鍵的綁定與解除。

介面改成「按一下再按鍵」之後，「不綁定」變成正常狀態（按 Esc）。
沒處理好的症狀是所有快捷鍵一起失效 —— 不會報錯，只是按了沒反應。
"""

from __future__ import annotations

import pytest

from lqa.hotkey import VK_CODES, KeyWatcher, resolve


class TestKeyNames:
    def test_recorded_names_are_the_ones_the_watcher_understands(self):
        """錄製和監聽共用同一張表，錄到的名字一定認得。"""
        for name in VK_CODES:
            assert resolve(name) == VK_CODES[name]

    def test_unbound_is_rejected_by_resolve(self):
        """空字串進不了 resolve，所以呼叫端必須先濾掉。"""
        with pytest.raises(ValueError):
            resolve("")

    def test_mouse_side_buttons_are_available(self):
        """側鍵在模擬器上不會和遊戲操作打架，是好用的截圖鍵。"""
        assert "mouse_x1" in VK_CODES and "mouse_x2" in VK_CODES


class TestWatcherSkipsUnbound:
    def test_unbound_actions_do_not_break_the_others(self):
        from lqa.gui.workers import HotkeyWatcher

        watcher = HotkeyWatcher({"shoot": "f9", "skip": "", "toggle": "f12"})
        assert set(watcher._names) == {"shoot", "toggle"}

    def test_all_unbound_leaves_nothing_to_watch(self):
        from lqa.gui.workers import HotkeyWatcher

        assert HotkeyWatcher({"shoot": "", "skip": ""})._names == {}


class TestFreshPressOnly:
    def test_a_key_already_held_is_not_reported_after_priming(self, monkeypatch):
        """錄製時手還壓著某個鍵，不該把那個鍵錄進去。

        先讀一次把現況吃掉，之後才算新按下的。
        """
        held = {"f9"}
        monkeypatch.setattr(KeyWatcher, "_is_down",
                            staticmethod(lambda code: code == VK_CODES["f9"]))
        watcher = KeyWatcher(["f9", "f8"])
        assert watcher.pressed() == ["f9"]      # 這一次是拿來吃掉現況的
        assert watcher.pressed() == []

    def test_a_new_press_is_reported(self, monkeypatch):
        state = {"down": False}
        monkeypatch.setattr(KeyWatcher, "_is_down",
                            staticmethod(lambda code: state["down"]))
        watcher = KeyWatcher(["f9"])
        watcher.pressed()
        state["down"] = True
        assert watcher.pressed() == ["f9"]
