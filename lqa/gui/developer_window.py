"""開發者視窗：自動錄製的實驗場。

自動偵測「打字結束」先前試過三種判準，每一種都在某類句子上失敗，
所以主流程改成手動截圖。但當時漏句有一部分是遊戲卡頓造成的取樣不足，
而解析已經移到最後、擷取也改成只在按鍵當下進行，卡頓的成因大致消失了，
所以值得再測一次 —— 只是不該讓它出現在正常流程裡。

自動錄製沿用 LineTracker 的判準：打字只會增加筆畫，換句才會讓舊筆畫消失。
每偵測到一句結束就拍下那一句最完整的樣子，寫進目前游標所在的條目。
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..logging_setup import get
from .settings import GuiSettings
from .theme import build_qss, palette_for

log = get("gui.dev")


class DeveloperWindow(QtWidgets.QWidget):
    def __init__(self, main_window, settings: GuiSettings):
        super().__init__(main_window, QtCore.Qt.WindowType.Window)
        self.main = main_window
        self.settings = settings
        self.setWindowTitle("開發者選項")
        self.setMinimumWidth(460)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel("開發者選項")
        heading.setProperty("role", "title")
        layout.addWidget(heading)

        self.enabled = QtWidgets.QCheckBox("啟用開發者模式")
        self.enabled.setChecked(settings.developer_mode)
        self.enabled.toggled.connect(self._on_toggle)
        layout.addWidget(self.enabled)

        note = QtWidgets.QLabel(
            "開啟後主視窗會多出「自動錄製」。\n\n"
            "自動錄製會持續盯著對白框，偵測到一句顯示完成就自動拍下，\n"
            "拍到的內容寫進目前游標所在的條目，接著游標往下移。\n\n"
            "判準是「打字只會增加筆畫、換句才會讓舊筆畫消失」。\n"
            "先前實測會漏掉短句，當時有一部分是遊戲卡頓造成取樣不足；\n"
            "解析已經移到最後、擷取也改成只在需要時進行，值得再測一次。\n"
            "跑完請用條目標記核對句數，不要直接相信結果。")
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addWidget(self._divider())
        interval = QtWidgets.QHBoxLayout()
        interval.addWidget(QtWidgets.QLabel("取樣間隔"))
        self.interval = QtWidgets.QSpinBox()
        self.interval.setRange(20, 500)
        self.interval.setSingleStep(10)
        self.interval.setSuffix(" ms")
        self.interval.setValue(settings.auto_poll_ms)
        self.interval.valueChanged.connect(self._on_interval)
        interval.addWidget(self.interval)
        interval.addWidget(QtWidgets.QLabel("越短越不容易漏句，但模擬器負擔越重"))
        interval.addStretch(1)
        layout.addLayout(interval)

        layout.addStretch(1)
        close = QtWidgets.QPushButton("關閉")
        close.clicked.connect(self.close)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

        self.setStyleSheet(build_qss(palette_for(settings.dark, settings.accent)))

    def _divider(self) -> QtWidgets.QFrame:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        return line

    def _on_toggle(self, value: bool) -> None:
        self.settings.developer_mode = value
        self.settings.save()
        log.info("開發者模式 %s", "開啟" if value else "關閉")
        self.main.apply_developer_mode()

    def _on_interval(self, value: int) -> None:
        self.settings.auto_poll_ms = value
        self.settings.save()
