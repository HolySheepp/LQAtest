"""ROI 是否切到文字的檢查。

這個判斷很重要：框比遊戲的文字區窄時，長句子的尾巴會被切掉，
比對階段會系統性誤報成「超框」，而畫面上看起來完全正常。

判斷方式刻意用「擴框後重跑 OCR，看有沒有多讀到字」這種直接證據。
先前試過用框外的像素量、密度等啟發式規則，都不可靠：
姓名框上方的角色立繪會被誤報成被切掉的文字，
而框把一行字攔腰切斷時，框外那半反而比框內還多。
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="需要 opencv")

from lqa.config import MaskConfig  # noqa: E402
from lqa.detect import textmask as tm  # noqa: E402
from lqa.ocr.base import OcrEngine, OcrLine, OcrResult  # noqa: E402
from lqa.tools_probe import report_fit, report_margins, report_overlap  # noqa: E402

CFG = MaskConfig(method="value", bright_threshold=170, min_text_pixels=40, upscale=1)


def result_for(text: str, lines: int = 1) -> OcrResult:
    """做一個有指定行數的 OcrResult。行數是判斷關鍵：
    擴框後多出來的字如果自成新的一行，那是隔壁的文字塊，不是被切掉的尾巴。"""
    return OcrResult(
        text=text,
        confidence=0.99,
        lines=[OcrLine(text=text, confidence=0.99, box=(0, i * 20, 10, i * 20 + 10))
               for i in range(lines)],
    )


class ScriptedEngine(OcrEngine):
    """依照送進來的影像尺寸回傳預設結果，用來模擬擴框後讀到更多字。"""

    def __init__(self, by_size: dict[tuple[int, int], OcrResult],
                 default: OcrResult | None = None):
        self.by_size = by_size
        self.default = default or result_for("")
        self.calls: list[tuple[int, int]] = []

    def read(self, image: np.ndarray) -> OcrResult:
        h, w = image.shape[:2]
        self.calls.append((w, h))
        return self.by_size.get((w, h), self.default)


def frame_with_text(text: str, x: int = 40, y: int = 60,
                    size: int = 640, scale: float = 0.8) -> np.ndarray:
    canvas = np.full((size, size, 3), 20, np.uint8)
    cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, scale,
                (254, 254, 254), 2, cv2.LINE_AA)
    return canvas


def margins_for(frame, roi):
    return report_margins(frame, roi, tm.build_mask(tm.crop(frame, roi), CFG))


class TestMargins:
    def test_reports_every_side(self, capsys):
        frame = frame_with_text("Short line")
        margins = margins_for(frame, (20, 20, 400, 80))
        out = capsys.readouterr().out
        assert set(margins) == {"上", "下", "左", "右"}
        for side in ("上", "下", "左", "右"):
            assert side in out

    def test_empty_roi_reports_nothing(self, capsys):
        frame = np.full((200, 200, 3), 20, np.uint8)
        assert margins_for(frame, (20, 20, 100, 50)) == {}


class TestClippingByOcr:
    """擴框後重跑 OCR，用「有沒有多讀到字」當作被切掉的直接證據。"""

    # 文字向右超出框外的情境；只有「右」那一邊會被檢查
    CLIPPED_ROI = (30, 30, 200, 60)
    GROWN_RIGHT = (230, 60)          # CFG.upscale=1，所以尺寸就是裁切後的大小
    BASE = "This sentence"

    def clipped_frame(self):
        return frame_with_text("This sentence runs past the box edge")

    def run(self, frame, roi, engine, base, capsys):
        margins = margins_for(frame, roi)
        capsys.readouterr()
        report_fit("對白框", frame, roi, CFG, engine, "mask", base, margins)
        return capsys.readouterr().out

    def test_warns_when_widening_reveals_more_text(self, capsys):
        engine = ScriptedEngine(
            {self.GROWN_RIGHT: result_for("This sentence runs past")},
            default=result_for(self.BASE),
        )
        out = self.run(self.clipped_frame(), self.CLIPPED_ROI, engine,
                       result_for(self.BASE), capsys)
        assert "框正在切掉文字" in out
        assert "右" in out

    def test_one_extra_character_is_not_enough_to_warn(self, capsys):
        """OCR 本來就有雜訊，差一個字元不足以斷定被切掉。"""
        engine = ScriptedEngine(
            {self.GROWN_RIGHT: result_for("This sentences")},
            default=result_for(self.BASE),
        )
        out = self.run(self.clipped_frame(), self.CLIPPED_ROI, engine,
                       result_for(self.BASE), capsys)
        assert "框正在切掉文字" not in out

    def test_extra_text_on_a_new_line_is_a_neighbour_not_clipping(self, capsys):
        """回歸測試。

        姓名框正下方就是對白，往下擴一定會多讀到字。
        但那些字自成新的一行，代表是隔壁的文字塊而不是被切掉的尾巴。
        """
        engine = ScriptedEngine(
            {self.GROWN_RIGHT: result_for("This sentence AND A WHOLE NEW LINE", lines=2)},
            default=result_for(self.BASE),
        )
        out = self.run(self.clipped_frame(), self.CLIPPED_ROI, engine,
                       result_for(self.BASE), capsys)
        assert "框正在切掉文字" not in out

    def test_quiet_when_widening_reveals_nothing(self, capsys):
        """姓名框上方是角色立繪：擴框後 OCR 讀不到多的字，就不該報警。"""
        frame = frame_with_text("Cyan(11201)", x=40, y=60)
        frame[0:35, 20:400] = 250          # 緊貼上緣的大片亮色
        engine = ScriptedEngine({}, default=result_for("Cyan(11201)"))
        out = self.run(frame, (30, 38, 300, 30), engine,
                       result_for("Cyan(11201)"), capsys)
        assert "框正在切掉文字" not in out

    def test_only_checks_sides_the_text_touches(self, capsys):
        """文字離某邊還很遠時，連 OCR 都不必重跑。"""
        frame = frame_with_text("Short")
        engine = ScriptedEngine({}, default=result_for("Short"))
        self.run(frame, (20, 20, 400, 120), engine, result_for("Short"), capsys)
        assert engine.calls == [], "四邊都有餘裕時不該重跑 OCR"

    def test_hints_when_margin_is_thin_but_nothing_is_lost(self, capsys):
        frame = frame_with_text("Snug", x=10, y=40)
        box = tm.content_bbox(tm.build_mask(frame, CFG))
        assert box is not None
        x0, y0, x1, y1 = box
        roi = (x0, y0, x1 - x0 + 1, y1 - y0 + 1)
        engine = ScriptedEngine({}, default=result_for("Snug"))
        out = self.run(frame, roi, engine, result_for("Snug"), capsys)
        assert "框正在切掉文字" not in out
        assert "10px 以上餘裕" in out

    def test_no_engine_is_a_noop(self, capsys):
        frame = frame_with_text("Anything")
        out = self.run(frame, (20, 20, 400, 80), None, result_for("x"), capsys)
        assert out == ""


class TestOverlap:
    """對白框與姓名框重疊時兩邊的 OCR 會互相污染。

    實際校準時很容易發生：姓名框下緣 803、對白框上緣 799，
    重疊了 4px，看遮罩圖不一定看得出來。
    """

    def profile(self, body, speaker):
        from lqa.config import Profile

        return Profile(name="t", window_title="測試",
                       body_roi=body, speaker_roi=speaker)

    def test_detects_vertical_overlap(self, capsys):
        assert report_overlap(self.profile((91, 799, 375, 172), (93, 772, 331, 31)))
        assert "重疊" in capsys.readouterr().out

    def test_touching_edges_are_not_overlapping(self, capsys):
        # 姓名框 772..799，對白框 799..971，剛好相接不算重疊
        assert not report_overlap(self.profile((91, 799, 375, 172), (93, 772, 331, 27)))
        assert capsys.readouterr().out == ""

    def test_separated_boxes_are_fine(self):
        assert not report_overlap(self.profile((91, 810, 375, 172), (93, 772, 331, 27)))

    def test_no_speaker_roi_is_fine(self):
        assert not report_overlap(self.profile((91, 799, 375, 172), None))

    def test_side_by_side_boxes_do_not_overlap(self):
        assert not report_overlap(self.profile((0, 0, 100, 50), (120, 0, 100, 50)))
