"""自繪標題列的命中測試。

去掉系統外框之後，拉伸、貼邊分割、拖到頂端最大化都會一起消失 ——
那些是系統在「非工作區」上做的事。把邊緣與標題列重新宣告成非工作區，
系統就會照常提供。這段是座標判斷，容易寫錯而且不需要真視窗就測得動。
"""

from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore", reason="需要 PySide6")

from lqa.gui import titlebar as tb  # noqa: E402

W, H, BAR = 800, 600, 38


def at(x: int, y: int, maximised: bool = False, buttons=None):
    return tb.hit_test(QtCore.QPoint(x, y), W, H, maximised, BAR, buttons or [])


class TestResizeEdges:
    @pytest.mark.parametrize("point,expected", [
        ((1, 1), tb.HTTOPLEFT),
        ((W - 1, 1), tb.HTTOPRIGHT),
        ((1, H - 1), tb.HTBOTTOMLEFT),
        ((W - 1, H - 1), tb.HTBOTTOMRIGHT),
        ((1, 300), tb.HTLEFT),
        ((W - 1, 300), tb.HTRIGHT),
        ((400, 1), tb.HTTOP),
        ((400, H - 1), tb.HTBOTTOM),
    ], ids=["左上", "右上", "左下", "右下", "左", "右", "上", "下"])
    def test_every_edge_and_corner(self, point, expected):
        assert at(*point) == expected

    def test_corners_win_over_plain_edges(self):
        """角落要回報成角落，否則只能單向拉伸。"""
        assert at(1, 1) == tb.HTTOPLEFT
        assert at(1, 1) != tb.HTLEFT

    def test_just_inside_the_border_is_not_an_edge(self):
        assert at(tb.BORDER + 1, 300) != tb.HTLEFT

    def test_maximised_window_has_no_resize_edges(self):
        """最大化時從邊緣拉伸沒有意義，而且會擋到貼邊操作。"""
        assert at(1, 300, maximised=True) != tb.HTLEFT
        assert at(1, 1, maximised=True) == tb.HTCAPTION


class TestTitleBar:
    def test_title_area_is_draggable(self):
        assert at(400, 18) == tb.HTCAPTION

    def test_title_area_still_works_when_maximised(self):
        assert at(400, 18, maximised=True) == tb.HTCAPTION

    def test_buttons_are_not_draggable(self):
        """按鈕若回報成標題列就按不下去。"""
        close = QtCore.QRect(W - 46, 0, 46, BAR)
        assert at(W - 20, 18, buttons=[close]) == tb.HTCLIENT

    def test_gap_between_buttons_is_still_draggable(self):
        close = QtCore.QRect(W - 46, 0, 46, BAR)
        assert at(W - 60, 18, buttons=[close]) == tb.HTCAPTION


WORK = (0, 0, 1920, 1032)


class TestClampToWorkArea:
    """最大化的視窗矩形有時會比工作區大一圈，那圈本來要給非工作區用。

    我們把非工作區吃掉了，所以得自己夾回來，否則視窗溢出螢幕、
    底部與兩側被切掉。
    """

    def test_overflowing_window_is_pulled_back(self):
        assert tb.clamp_to_work_area((-8, -8, 1928, 1040), WORK) == WORK

    def test_exact_fit_is_left_alone(self):
        """實測 Qt 常常已經算好了。這時候再往內縮就會在邊上留一道縫。"""
        assert tb.clamp_to_work_area(WORK, WORK) == WORK

    def test_smaller_window_is_not_inflated(self):
        assert tb.clamp_to_work_area((100, 100, 900, 700), WORK) == (100, 100, 900, 700)

    def test_secondary_monitor_offsets_are_kept(self):
        """第二螢幕的工作區不是從 0,0 開始，不能當成原點處理。"""
        work = (1920, 0, 3840, 1032)
        assert tb.clamp_to_work_area((1912, -8, 3848, 1040), work) == work


class TestNativeFrameCalls:
    """非 Windows 上這些呼叫要安靜地不做事，而不是炸掉。"""

    def test_restore_is_a_no_op_without_a_handle(self):
        assert tb.restore_native_frame(0) is False

    def test_rounding_is_a_no_op_without_a_handle(self):
        assert tb.round_corners(0) is False


class TestClientArea:
    def test_body_is_left_to_qt(self):
        """內容區回 None，讓 Qt 照常處理點擊。"""
        assert at(400, 300) is None

    def test_body_is_left_to_qt_when_maximised(self):
        assert at(400, 300, maximised=True) is None

    def test_no_title_bar_means_no_caption(self):
        assert tb.hit_test(QtCore.QPoint(400, 10), W, H, False, 0, []) is None
