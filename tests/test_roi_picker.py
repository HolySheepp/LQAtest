"""框選視窗的座標換算。

畫布會把整張畫面縮小塞進去，所以拖出來的矩形是「畫布座標」，
存進 profile 的必須是原圖座標。算錯不會報錯，只會讓 ROI 悄悄偏掉。
"""

from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore", reason="需要 PySide6")

from lqa.gui.roi_picker import (fit_scale, image_to_view,  # noqa: E402
                                view_to_image)

IMAGE = QtCore.QSize(540, 960)          # 直式手遊畫面
ORIGIN = QtCore.QPoint(0, 0)


class TestFitScale:
    def test_shrinks_to_fit_the_shorter_side(self):
        assert fit_scale(IMAGE, QtCore.QSize(1000, 480)) == pytest.approx(0.5)

    def test_never_enlarges(self):
        """放大只會讓框選更難對準，也不會多出任何資訊。"""
        assert fit_scale(QtCore.QSize(100, 100), QtCore.QSize(900, 900)) == 1.0

    def test_empty_sizes_do_not_divide_by_zero(self):
        assert fit_scale(QtCore.QSize(0, 0), QtCore.QSize(100, 100)) == 1.0
        assert fit_scale(IMAGE, QtCore.QSize(0, 0)) == 1.0


class TestViewToImage:
    def test_unscaled_rect_maps_one_to_one(self):
        rect = QtCore.QRect(10, 20, 100, 50)
        assert view_to_image(rect, ORIGIN, 1.0, IMAGE) == (10, 20, 100, 50)

    def test_half_scale_doubles_the_coordinates(self):
        rect = QtCore.QRect(10, 20, 100, 50)
        assert view_to_image(rect, ORIGIN, 0.5, IMAGE) == (20, 40, 200, 100)

    def test_canvas_offset_is_removed(self):
        """圖是置中畫的，左右留白不能算進 ROI。"""
        rect = QtCore.QRect(110, 20, 100, 50)
        assert view_to_image(rect, QtCore.QPoint(100, 0), 1.0, IMAGE) == \
            (10, 20, 100, 50)

    def test_dragging_off_the_left_edge_clamps_to_zero(self):
        """從圖外面開始拖是常事，換算出負座標存進 profile 會讓截圖直接炸掉。"""
        rect = QtCore.QRect(-50, -30, 100, 60)
        assert view_to_image(rect, ORIGIN, 1.0, IMAGE) == (0, 0, 50, 30)

    def test_dragging_past_the_right_edge_clamps_to_the_image(self):
        rect = QtCore.QRect(500, 900, 200, 200)
        x, y, w, h = view_to_image(rect, ORIGIN, 1.0, IMAGE)
        assert x + w <= IMAGE.width()
        assert y + h <= IMAGE.height()

    def test_zero_scale_does_not_crash(self):
        """畫布還沒配到大小的那一瞬間 scale 可能是 0。"""
        assert view_to_image(QtCore.QRect(0, 0, 10, 10), ORIGIN, 0.0, IMAGE)[2] == 10


class TestRoundTrip:
    @pytest.mark.parametrize("scale", [1.0, 0.5, 0.37])
    def test_image_to_view_and_back(self, scale):
        original = (40, 120, 300, 160)
        offset = QtCore.QPoint(17, 9)
        view = image_to_view(original, offset, scale)
        back = view_to_image(view, offset, scale, IMAGE)
        for got, want in zip(back, original):
            assert abs(got - want) <= 3, (back, original)
