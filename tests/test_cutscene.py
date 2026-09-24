"""過場動畫偵測。

過場時對白框會整個消失，露出底下的畫面。那些畫面內容會被當成筆畫，
自動錄製就以為一直在換句，狂吐假的句子出來。

判準是亮度中位數：對白框是純黑底加白字，白字只佔一小部分像素，
中位數幾乎不受影響；沒有黑底的話整塊都是畫面內容，中位數會跳到上百。
數值取自實際量測（純黑框 0、舊的漸層框 34~68、過場畫面 137）。
"""

from __future__ import annotations

import numpy as np

from lqa.detect import textmask as tm

BLACK_BOX = 0          # 實測：純黑對白框
GRADIENT_BOX = 50      # 實測：舊版漸變變暗的對白框
ARTWORK = 137          # 實測：過場畫面


def box(level: int, text_rows: int = 0) -> np.ndarray:
    """一塊底色加上幾列白字。"""
    frame = np.full((160, 400, 3), level, np.uint8)
    for row in range(text_rows):
        y = 30 + row * 40
        frame[y:y + 18, 20:380] = 254
    return frame


def artwork(phase: int) -> np.ndarray:
    """會動的過場畫面：亮的漸層加上隨幀平移的亮塊，中位數落在 ARTWORK 附近。"""
    columns = np.linspace(60, 220, 400, dtype=np.float32)
    frame = np.repeat(columns[None, :], 160, axis=0)
    for offset in (0, 130, 260):
        left = (offset + phase * 17) % 340
        frame[30:120, left:left + 50] = 250
    frame = np.clip(frame, 0, 255).astype(np.uint8)
    return np.repeat(frame[:, :, None], 3, axis=2)

class TestValueMedian:
    def test_text_barely_moves_the_median(self):
        """白字只佔一小部分像素，中位數幾乎不受影響 —— 這正是用它的理由。"""
        assert tm.value_median(box(BLACK_BOX)) == 0
        assert tm.value_median(box(BLACK_BOX, text_rows=3)) == 0

    def test_mean_would_have_been_fooled(self):
        """對照組：平均會被白字拉高，所以不能用平均。"""
        with_text = tm.value_channel(box(BLACK_BOX, text_rows=3)).mean()
        assert with_text > 10, with_text

    def test_artwork_is_far_brighter(self):
        assert tm.value_median(box(ARTWORK)) == ARTWORK


class TestCutsceneDecision:
    LIMIT = 60         # 純黑框（0）和過場（137）之間

    def test_black_box_with_text_is_not_a_cutscene(self):
        assert not tm.looks_like_cutscene(box(BLACK_BOX, text_rows=3), self.LIMIT)

    def test_empty_black_box_is_not_a_cutscene(self):
        """框在、只是還沒開始打字，不能當成過場。"""
        assert not tm.looks_like_cutscene(box(BLACK_BOX), self.LIMIT)

    def test_artwork_is_a_cutscene(self):
        assert tm.looks_like_cutscene(box(ARTWORK), self.LIMIT)

    def test_zero_limit_disables_the_check(self):
        """半透明漸層對白框的遊戲底色本來就不黑，硬套會把對白全擋掉。"""
        assert not tm.looks_like_cutscene(box(ARTWORK), 0)
        assert not tm.looks_like_cutscene(box(ARTWORK), -1)

    def test_a_gradient_box_needs_a_higher_limit(self):
        """舊版漸層框中位數 34~68，門檻設 60 會把對白誤判成過場。

        這就是預設關閉的理由 —— 由使用者看著實際數值決定。
        """
        assert tm.looks_like_cutscene(box(GRADIENT_BOX + 15), self.LIMIT)
        assert not tm.looks_like_cutscene(box(GRADIENT_BOX + 15), 100)


class TestDefaultIsOff:
    def test_setting_defaults_to_disabled(self):
        from lqa.gui.settings import GuiSettings

        assert GuiSettings().auto_cutscene_median == 0


class TestAutoRecordSkipsCutscenes:
    """真正要修的行為：過場時不能吐出假的句子。

    直接跑 AutoRecordWorker.run()，餵一串「對白 -> 過場 -> 對白」的畫面，
    看它送出幾句。沒有這道檢查的話，過場的每一幀都會被當成換句。
    """

    def _run(self, frames, cutscene_median):
        import lqa.capture.mss_backend as backend
        from lqa.config import MaskConfig, Profile, StabilityConfig
        from lqa.gui.workers import AutoRecordWorker

        class FakeCapture:
            def __init__(self):
                self.index = 0

            def grab(self):
                frame = frames[min(self.index, len(frames) - 1)]
                self.index += 1
                return frame

            def unavailable(self):
                return None

            def close(self):
                pass

        original = backend.open_capture
        backend.open_capture = lambda *a, **k: FakeCapture()
        try:
            profile = Profile(
                window_title="測試", body_roi=(0, 0, 400, 160),
                mask=MaskConfig(method="value", bright_threshold=140,
                                min_text_pixels=40),
                stability=StabilityConfig(min_changed_pixels=40))
            worker = AutoRecordWorker(profile, poll_ms=0,
                                      cutscene_median=cutscene_median)
            emitted = []
            worker.line_ready.connect(emitted.append)

            # 餵完就停，不要真的跑成無窮迴圈
            calls = {"n": 0}
            real_sleep = worker.msleep

            def counting_sleep(_ms):
                calls["n"] += 1
                if calls["n"] >= len(frames):
                    worker.stop()

            worker.msleep = counting_sleep
            worker.run()
            worker.msleep = real_sleep
            return emitted
        finally:
            backend.open_capture = original

    def _sequence(self):
        """對白 A（打字中）-> 過場 6 幀 -> 對白 B。

        過場那幾幀要像真的畫面：有亮處、而且每一幀都在動。平坦的灰色
        產生不出任何遮罩像素，測不出原本的問題。
        """
        return ([box(BLACK_BOX, text_rows=1), box(BLACK_BOX, text_rows=2),
                 box(BLACK_BOX, text_rows=3)]
                + [artwork(phase) for phase in range(6)]
                + [box(BLACK_BOX, text_rows=2)] * 3)

    def test_without_the_check_the_cutscene_fires_junk(self):
        """對照組：不檢查的話過場會被當成一直在換句。"""
        assert len(self._run(self._sequence(), 0)) > 2

    def test_with_the_check_only_real_lines_come_out(self):
        emitted = self._run(self._sequence(), 60)
        assert len(emitted) == 2, len(emitted)

    def test_the_line_showing_before_the_cutscene_is_kept(self):
        """框消失前那一句是完整的，不能因為偵測暫停就丟掉。"""
        from lqa.config import MaskConfig

        emitted = self._run(self._sequence(), 60)
        cfg = MaskConfig(method="value", bright_threshold=140)
        assert tm.text_pixel_count(tm.build_mask(emitted[0], cfg)) > 0
