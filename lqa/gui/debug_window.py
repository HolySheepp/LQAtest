"""調試視窗：即時調整取字範圍與門檻。

門檻一動就重畫遮罩，和命令列版按 [ ] 的效果一樣，
但左右並排看得更清楚：左邊原圖、右邊遮罩。
目標是右邊只剩文字筆畫、背景乾乾淨淨，而且每個字都在。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from ..config import MaskConfig, Profile
from ..detect import textmask as tm
from .knob import Knob
from .settings import GuiSettings
from .theme import palette_for

METHODS = ["hysteresis", "value", "colorkey", "otsu", "adaptive", "bright"]
METHOD_LABELS = {
    "hysteresis": "雙門檻遲滯（建議）",
    "value": "亮度（RGB 最大值）",
    "colorkey": "指定顏色",
    "otsu": "Otsu 自適應",
    "adaptive": "區域自適應",
    "bright": "灰階亮度（不建議）",
}


def to_pixmap(image: np.ndarray) -> QtGui.QPixmap:
    """numpy 影像轉 QPixmap。

    緩衝區一定要先綁到區域變數再建 QImage。QImage 不會複製傳進去的
    位元組，如果直接寫 QImage(image.tobytes(), ...)，那個臨時 bytes
    在建構式回傳後就被回收，接著 copy() 會去讀已經釋放的記憶體 ——
    症狀是整個程式直接閃退，連例外都不會丟。
    """
    if image.ndim == 2:
        height, width = image.shape
        buffer = np.ascontiguousarray(image).tobytes()
        qimage = QtGui.QImage(buffer, width, height, width,
                              QtGui.QImage.Format.Format_Grayscale8)
    else:
        height, width = image.shape[:2]
        buffer = np.ascontiguousarray(image[:, :, 2::-1]).tobytes()
        qimage = QtGui.QImage(buffer, width, height, width * 3,
                              QtGui.QImage.Format.Format_RGB888)
    pixmap = QtGui.QPixmap.fromImage(qimage.copy())
    del buffer          # copy() 之後才可以放掉
    return pixmap


class DebugWindow(QtWidgets.QWidget):
    profileSaved = QtCore.Signal()

    def __init__(self, profile: Profile, settings: GuiSettings,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.profile = profile
        self.settings = settings
        self.frame: Optional[np.ndarray] = None
        self.region = "body"
        self._grabber = None

        self.setWindowTitle("調試")
        self.resize(940, 640)
        self._build()
        self._apply_theme()
        self.refresh_frame()

    def _build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        self.region_box = QtWidgets.QComboBox()
        self.region_box.addItems(["對白框", "姓名框"])
        self.region_box.currentIndexChanged.connect(self._on_region)
        top.addWidget(QtWidgets.QLabel("範圍"))
        top.addWidget(self.region_box)

        self.method_box = QtWidgets.QComboBox()
        for key in METHODS:
            self.method_box.addItem(METHOD_LABELS[key], key)
        self.method_box.currentIndexChanged.connect(self._on_method)
        top.addWidget(QtWidgets.QLabel("取字方式"))
        top.addWidget(self.method_box, 1)

        grab = QtWidgets.QPushButton("重新抓畫面")
        grab.clicked.connect(self.refresh_frame)
        top.addWidget(grab)

        reframe = QtWidgets.QPushButton("重新框選")
        reframe.clicked.connect(self._reframe)
        top.addWidget(reframe)
        root.addLayout(top)

        images = QtWidgets.QHBoxLayout()
        self.original = QtWidgets.QLabel("尚未抓到畫面")
        self.masked = QtWidgets.QLabel()
        for view in (self.original, self.masked):
            view.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            view.setMinimumHeight(190)
            view.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
            images.addWidget(view, 1)
        root.addLayout(images, 1)

        knobs = QtWidgets.QHBoxLayout()
        knobs.setSpacing(6)
        self.knobs: dict[str, Knob] = {}
        specs = [
            ("bright_threshold", "低門檻", 0, 255, [5, 1, 10], 0),
            ("seed_threshold", "種子門檻", 0, 255, [5, 1, 10], 0),
            ("ocr_grow", "筆畫膨脹", 0, 6, [1], 0),
            ("upscale", "放大倍率", 1, 4, [1], 0),
            ("min_text_pixels", "最少文字量", 0, 400, [10, 5, 50], 0),
            ("color_tolerance", "容許色距", 5, 200, [5, 1], 0),
        ]
        for key, text, low, high, steps, digits in specs:
            knob = Knob(text, getattr(self._cfg(), key), low, high, steps, digits)
            knob.valueChanged.connect(lambda v, k=key: self._on_knob(k, v))
            self.knobs[key] = knob
            knobs.addWidget(knob)
        knobs.addStretch(1)
        root.addLayout(knobs)

        self.info = QtWidgets.QLabel("")
        self.info.setProperty("role", "dim")
        root.addWidget(self.info)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        test = QtWidgets.QPushButton("測試辨識")
        test.clicked.connect(self._test_ocr)
        bottom.addWidget(test)
        save = QtWidgets.QPushButton("儲存設定")
        save.setProperty("role", "primary")
        save.clicked.connect(self._save)
        bottom.addWidget(save)
        root.addLayout(bottom)

    def _apply_theme(self) -> None:
        palette = palette_for(self.settings.dark, self.settings.accent)
        self.setStyleSheet(__import__("lqa.gui.theme", fromlist=["build_qss"])
                           .build_qss(palette))
        for knob in self.knobs.values():
            knob.set_colors(palette.accent, palette.border,
                            palette.text, palette.text_dim)

    # --- 狀態 ---

    def _cfg(self) -> MaskConfig:
        return (self.profile.mask if self.region == "body"
                else self.profile.effective_speaker_mask())

    def _set_cfg(self, cfg: MaskConfig) -> None:
        if self.region == "body":
            self.profile.mask = cfg
        else:
            self.profile.speaker_mask = cfg

    def _roi(self):
        return self.profile.body_roi if self.region == "body" else self.profile.speaker_roi

    def _on_region(self, index: int) -> None:
        self.region = "body" if index == 0 else "speaker"
        cfg = self._cfg()
        for key, knob in self.knobs.items():
            knob.blockSignals(True)
            knob.setValue(getattr(cfg, key))
            knob.blockSignals(False)
        self.method_box.blockSignals(True)
        self.method_box.setCurrentIndex(METHODS.index(cfg.method)
                                        if cfg.method in METHODS else 0)
        self.method_box.blockSignals(False)
        self.render()

    def _on_method(self, index: int) -> None:
        self._set_cfg(replace(self._cfg(), method=self.method_box.itemData(index)))
        self.render()

    def _on_knob(self, key: str, value: float) -> None:
        self._set_cfg(replace(self._cfg(), **{key: int(value)}))
        self.render()

    # --- 畫面 ---

    def _start_grabber(self) -> None:
        from .workers import FrameGrabber

        if self._grabber is None:
            self._grabber = FrameGrabber(self.profile, self)
            self._grabber.grabbed.connect(self._on_frame)
            self._grabber.failed.connect(self._on_grab_failed)
            self._grabber.start()

    def refresh_frame(self) -> None:
        """要求抓一張。實際擷取在背景執行緒進行，這裡不會阻塞。"""
        self._start_grabber()
        self.info.setText("抓取中...")
        self._grabber.request()

    def _on_frame(self, frame) -> None:
        self.frame = frame
        self.render()

    def _on_grab_failed(self, message: str) -> None:
        self.info.setText(f"抓不到畫面：{message}")

    def _release_capture(self) -> None:
        if self._grabber is not None:
            self._grabber.stop()
            self._grabber.wait(1000)
            self._grabber = None

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._release_capture()
        super().closeEvent(event)

    def render(self) -> None:
        roi = self._roi()
        if self.frame is None or roi is None:
            return
        crop = tm.crop(self.frame, roi)
        if crop.size == 0:
            self.info.setText("範圍超出畫面，請重新框選")
            return
        cfg = self._cfg()
        try:
            mask = tm.build_mask(crop, cfg)
        except ValueError as exc:
            self.info.setText(str(exc))
            return
        self.original.setPixmap(to_pixmap(crop).scaled(
            self.original.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation))
        self.masked.setPixmap(to_pixmap(mask).scaled(
            self.masked.size(), QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation))

        pixels = tm.text_pixel_count(mask)
        value = tm.value_channel(crop)
        self.info.setText(
            f"文字像素 {pixels}　覆蓋率 {pixels / mask.size:.2%}　"
            f"亮度中位數 {int(np.median(value))}　99% {int(np.percentile(value, 99))}　"
            f"最大 {int(value.max())}")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.render()

    # --- 動作 ---

    def _reframe(self) -> None:
        """用 OpenCV 的框選工具重畫範圍，沿用命令列版的互動。"""
        if self.frame is None:
            return
        try:
            import cv2
        except ImportError:
            self.info.setText("需要 opencv 才能框選")
            return
        title = "拖曳框選" + ("對白框" if self.region == "body" else "姓名框")
        box = cv2.selectROI(title, self.frame, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow(title)
        x, y, w, h = (int(v) for v in box)
        if w <= 0 or h <= 0:
            return
        if self.region == "body":
            self.profile.body_roi = (x, y, w, h)
        else:
            self.profile.speaker_roi = (x, y, w, h)
        self.render()

    def _test_ocr(self) -> None:
        roi = self._roi()
        if self.frame is None or roi is None:
            return
        from ..ocr.base import engine_for
        from ..record.recorder import _ocr_input

        cfg = self._cfg()
        crop = tm.crop(self.frame, roi)
        mask = tm.build_mask(crop, cfg)
        self.info.setText("辨識中...")
        QtWidgets.QApplication.processEvents()
        try:
            engine = engine_for(self.profile)
            result = engine.read(_ocr_input(crop, mask, cfg, self.profile.ocr.source))
        except Exception as exc:
            self.info.setText(f"辨識失敗：{exc}")
            return
        self.info.setText(f"讀到：{result.text!r}　信心 {result.confidence:.3f}")

    def _save(self) -> None:
        self.profile.save(self.settings.profile_path)
        self.info.setText(f"已存到 {self.settings.profile_path}")
        self.profileSaved.emit()
