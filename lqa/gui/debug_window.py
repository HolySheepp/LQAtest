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

from ..config import (REGION_KEYS, REGION_LABELS, REGION_SPECS, MaskConfig,
                      Profile)
from ..logging_setup import get
from ..detect import textmask as tm
from .knob import Knob
from .roi_picker import RoiPicker
from .settings import GuiSettings
from .theme import palette_for

log = get("gui.debug")

# 旋鈕的欄位與中文名。順序就是畫面上由左到右的順序
KNOB_LABELS = {
    "bright_threshold": "低門檻",
    "seed_threshold": "種子門檻",
    "ocr_grow": "筆畫膨脹",
    "upscale": "放大倍率",
    "min_text_pixels": "最少文字量",
    "color_tolerance": "容許色距",
}

METHODS = ["hysteresis", "value", "colorkey", "otsu", "adaptive", "bright"]
METHOD_LABELS = {
    "hysteresis": "雙門檻遲滯（建議）",
    "value": "亮度（RGB 最大值）",
    "colorkey": "指定顏色",
    "otsu": "Otsu 自適應",
    "adaptive": "區域自適應",
    "bright": "灰階亮度（不建議）",
}


def describe_changes(before: MaskConfig, after: MaskConfig) -> list[str]:
    """列出哪幾個參數會變、從多少變成多少。

    「恢復預設」按下去只有旋鈕會轉，使用者未必看得出動了哪幾個 ——
    先講清楚，才不會以為它亂改一通。
    """
    return [f"{label} {getattr(before, key)} → {getattr(after, key)}"
            for key, label in KNOB_LABELS.items()
            if getattr(before, key) != getattr(after, key)]


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
        self.region = REGION_KEYS[0]
        self._grabber = None
        self._rendering = False

        self.setWindowTitle("調試")
        self.resize(940, 640)
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self.render)
        self._build()
        self._apply_theme()
        self._scan_windows()
        self.refresh_frame()

    def _build(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        # 擷取來源要能在介面裡換。profile 裡存的是設定者自己的模擬器
        # 實例名稱，換一台機器幾乎一定不一樣 —— 而這是拿到軟體之後
        # 第一個會卡住的地方
        top.addWidget(QtWidgets.QLabel("擷取來源"))
        self.window_box = QtWidgets.QComboBox()
        self.window_box.setMinimumWidth(190)
        self.window_box.currentIndexChanged.connect(self._on_window)
        top.addWidget(self.window_box)
        rescan = QtWidgets.QPushButton("重新掃描")
        rescan.setToolTip("剛開啟模擬器的話按這個")
        rescan.clicked.connect(self._scan_windows)
        top.addWidget(rescan)

        self.region_box = QtWidgets.QComboBox()
        for key, label, _roi, _mask in REGION_SPECS:
            self.region_box.addItem(label, key)
        self.region_box.setToolTip(
            "NPC 對白的框和一般對白位置不同，要分開框。"
            "解析時會自己挑出畫面上正在用的那一套")
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
            view.setMinimumSize(120, 190)
            view.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
            # setPixmap 會改變 QLabel 的尺寸建議，進而觸發版面重算與
            # resizeEvent；resizeEvent 又呼叫 render 再 setPixmap ——
            # 無限遞迴直到堆疊爆掉。Ignored 讓標籤的尺寸完全由版面決定，
            # 放什麼圖進去都不會回頭影響版面。
            view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                               QtWidgets.QSizePolicy.Policy.Ignored)
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
        reset = QtWidgets.QPushButton("恢復預設")
        reset.setToolTip("把目前範圍的取字參數還原成預設值，ROI 不動")
        reset.clicked.connect(self._reset_defaults)
        bottom.addWidget(reset)
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
        palette = palette_for(self.settings.dark, self.settings.accent,
                               self.settings.custom_accent)
        self.setStyleSheet(__import__("lqa.gui.theme", fromlist=["build_qss"])
                           .build_qss(palette))
        for knob in self.knobs.values():
            knob.set_colors(palette.accent, palette.border,
                            palette.text, palette.text_dim)

    # --- 狀態 ---

    def _cfg(self) -> MaskConfig:
        return self.profile.mask_for(self.region)

    def _set_cfg(self, cfg: MaskConfig) -> None:
        self.profile.set_mask(self.region, cfg)

    def _roi(self):
        return self.profile.roi_of(self.region)

    def _is_speaker(self) -> bool:
        return self.region.endswith("speaker")

    def _scan_windows(self) -> None:
        """列出現在開著的視窗。模擬器排在前面，其餘的也留著備用。"""
        from ..capture.window import list_windows

        try:
            windows = list_windows()
        except Exception as exc:
            log.warning("列視窗失敗：%s", exc)
            windows = []
        windows.sort(key=lambda w: (not w.is_emulator, w.is_emulator_manager,
                                    w.title.lower()))

        self.window_box.blockSignals(True)
        self.window_box.clear()
        current = self.profile.window_title or ""
        self.window_box.addItem(f"目前設定：{current or '（未設定）'}", current)
        for info in windows:
            mark = "（模擬器）" if info.is_emulator else ""
            self.window_box.addItem(
                f"{info.title}　{info.process} {info.width}x{info.height}{mark}",
                info.title)
        self.window_box.blockSignals(False)
        if not any(w.is_emulator for w in windows):
            self.info.setText("沒有偵測到模擬器視窗，請先開啟模擬器再按「重新掃描」")

    def _on_window(self, index: int) -> None:
        title = self.window_box.itemData(index)
        if index <= 0 or not title or title == self.profile.window_title:
            return
        self.profile.window_title = title
        # 換了視窗就得重新連線，舊的擷取還綁在原來那個
        self._release_capture()
        self._start_grabber()
        self.info.setText(f"擷取來源改成「{title}」，記得按「儲存設定」")

    def _on_region(self, index: int) -> None:
        self.region = self.region_box.itemData(index)
        self._sync_controls()
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
        log.debug("要求抓圖")
        self._start_grabber()
        self.info.setText("抓取中...")
        self._grabber.request()

    def _on_frame(self, frame) -> None:
        log.debug("收到畫面 %sx%s", frame.shape[1], frame.shape[0])
        self.frame = frame
        self.render()

    def _on_grab_failed(self, message: str) -> None:
        log.warning("抓圖失敗：%s", message)
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
        # 即使版面已經不會回頭觸發 render，仍保留這道防護：
        # 重入一次就會遞迴到堆疊溢位，代價太高
        if self._rendering:
            log.warning("render 重入，已擋下 —— 這代表版面又回頭觸發了重畫")
            return
        self._rendering = True
        try:
            self._render()
        except Exception:
            log.exception("重畫失敗")
        finally:
            self._rendering = False

    def _render(self) -> None:
        roi = self._roi()
        if roi is None:
            # NPC 的框一開始是空的。要講出來並清掉預覽，
            # 否則畫面上留著上一個範圍的圖，看起來像框選沒有生效
            self.original.setPixmap(QtGui.QPixmap())
            self.masked.setPixmap(QtGui.QPixmap())
            self.info.setText(
                f"「{REGION_LABELS[self.region]}」還沒框選，按「重新框選」畫一個。"
                "沒框的話解析時就只會用一般對白那一套")
            return
        if self.frame is None:
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
        # 不要在版面計算當下重畫，排到事件迴圈之後
        self._resize_timer.start(80)

    # --- 動作 ---

    def _reframe(self) -> None:
        """框選範圍。

        自己畫而不是用 OpenCV 的 selectROI —— 那個按叉叉取消不掉
        （只會把視窗再開一次），而且標題走系統 ANSI 編碼，中文會變亂碼。
        """
        if self.frame is None:
            self.info.setText("還沒抓到畫面，先按「重新抓取」")
            return
        label = REGION_LABELS[self.region]
        picker = RoiPicker(to_pixmap(self.frame), label, self._roi(), self)
        if picker.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self.info.setText(f"已取消，{label}維持原本的範圍")
            return
        box = picker.result_rect()
        if box is None:
            return
        x, y, w, h = box
        if w <= 0 or h <= 0:
            return
        self.profile.set_roi(self.region, (x, y, w, h))
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

    def _reset_defaults(self) -> None:
        """把取字參數還原成預設值。

        調壞了要有路可以回頭，否則使用者只能自己記得原本的數字。
        ROI 不動 —— 那是框選的成果，和參數是兩回事。
        """
        defaults = MaskConfig()
        if self._is_speaker():
            # 發話者可能是白色也可能是淺藍，colorkey 模式兩色都要收
            defaults = replace(defaults,
                               text_colors=["#fefefe", "#5dbcfe"],
                               min_text_pixels=max(8, defaults.min_text_pixels // 4))

        region_name = self.region_box.currentText()
        changes = describe_changes(self._cfg(), defaults)
        if not changes:
            self.info.setText(f"「{region_name}」目前就是預設值，沒有東西要還原")
            return

        if QtWidgets.QMessageBox.question(
            self, "恢復預設",
            f"要把「{region_name}」的取字參數還原成預設值嗎？（框選範圍不會變動）"
            + chr(10) + chr(10) + chr(10).join(changes),
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        self._set_cfg(defaults)
        self._sync_controls()
        self.render()
        log.info("已還原 %s 的取字參數：%s", self.region, "、".join(changes))
        # 一定要講「還沒存」—— 不按儲存設定就關掉，下次打開又是舊的數字，
        # 看起來會像還原沒有生效
        self.info.setText("已還原：" + "、".join(changes)
                          + "。還沒寫進 profile，要按「儲存設定」才會留住")

    def _sync_controls(self) -> None:
        """把目前設定同步回旋鈕與下拉選單。"""
        cfg = self._cfg()
        for key, knob in self.knobs.items():
            knob.blockSignals(True)
            knob.setValue(getattr(cfg, key))
            knob.blockSignals(False)
        self.method_box.blockSignals(True)
        self.method_box.setCurrentIndex(
            METHODS.index(cfg.method) if cfg.method in METHODS else 0)
        self.method_box.blockSignals(False)

    def _save(self) -> None:
        self.profile.save(self.settings.profile_path)
        self.info.setText(f"已存到 {self.settings.profile_path}")
        self.profileSaved.emit()
