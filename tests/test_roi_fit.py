"""ROI 是否切到文字的檢查。

這個判斷很重要：如果框比遊戲的文字區窄，長句子的尾巴會被切掉，
比對階段會系統性誤報成「超框」，而且畫面上看起來完全正常。

單看「文字貼齊 ROI 邊緣」分不出是真超框還是框太小，
所以要往框外再取一次字，看緊鄰的地方還有沒有文字。
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="需要 opencv")

from lqa.config import MaskConfig  # noqa: E402
from lqa.detect import textmask as tm  # noqa: E402
from lqa.tools_probe import _report_fit  # noqa: E402

CFG = MaskConfig(method="value", bright_threshold=170, min_text_pixels=40)


def frame_with_text(text: str, x: int = 40, y: int = 60,
                    size: int = 640, scale: float = 0.8) -> np.ndarray:
    """深色底、白字，模擬對白框。"""
    canvas = np.full((size, size, 3), 20, np.uint8)
    cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, scale,
                (254, 254, 254), 2, cv2.LINE_AA)
    return canvas


def run(frame, roi, capsys) -> str:
    mask = tm.build_mask(tm.crop(frame, roi), CFG)
    _report_fit("對白框", frame, roi, CFG, mask)
    return capsys.readouterr().out


class TestClippingDetected:
    def test_warns_when_text_continues_past_the_right_edge(self, capsys):
        frame = frame_with_text("This sentence is much longer than the box")
        roi = (30, 30, 200, 60)      # 明顯比整句窄
        out = run(frame, roi, capsys)
        assert "框正在切掉文字" in out
        assert "右" in out

    def test_warns_when_text_continues_below(self, capsys):
        frame = np.full((300, 600, 3), 20, np.uint8)
        for i, y in enumerate((60, 100, 140)):
            cv2.putText(frame, f"line number {i}", (40, y),
                        cv2.FONT_HERSHEY_DUPLEX, 0.8, (254, 254, 254), 2, cv2.LINE_AA)
        roi = (30, 30, 400, 55)      # 只框得到第一行
        out = run(frame, roi, capsys)
        assert "框正在切掉文字" in out
        assert "下" in out


class TestNoClipping:
    def test_quiet_when_the_box_has_room(self, capsys):
        frame = frame_with_text("Short line")
        roi = (20, 20, 400, 80)
        out = run(frame, roi, capsys)
        assert "框正在切掉文字" not in out
        assert "框內留白" in out

    def test_reports_margins_on_every_side(self, capsys):
        frame = frame_with_text("Short line")
        out = run(frame, (20, 20, 400, 80), capsys)
        for side in ("上", "下", "左", "右"):
            assert side in out

    def test_hints_when_margin_is_razor_thin(self, capsys):
        """框外沒有文字，但字幾乎貼著框線時仍要提醒留餘裕。"""
        frame = frame_with_text("Snug", x=10, y=40, scale=0.8)
        mask_full = tm.build_mask(frame, CFG)
        box = tm.content_bbox(mask_full)
        assert box is not None
        x0, y0, x1, y1 = box
        roi = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)   # 剛好貼齊文字
        out = run(frame, roi, capsys)
        assert "建議仍留 10px 以上餘裕" in out

    def test_empty_roi_does_not_crash(self, capsys):
        frame = np.full((200, 200, 3), 20, np.uint8)
        out = run(frame, (20, 20, 100, 50), capsys)
        assert "框正在切掉文字" not in out


class TestEdgeOfFrame:
    def test_roi_at_frame_border_is_handled(self, capsys):
        """ROI 貼著畫面邊緣時，往外擴會超出畫面，不能爆掉。"""
        frame = frame_with_text("Edge case", x=5, y=40, size=200)
        out = run(frame, (0, 0, 120, 60), capsys)
        assert "框內留白" in out
