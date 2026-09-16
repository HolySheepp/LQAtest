"""設定視窗：外觀與熱鍵。"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from . import sound
from .hotkey_button import HotkeyButton
from .settings import DEFAULT_HOTKEYS, HOTKEY_LABELS, GuiSettings
from .theme import ACCENT_LABELS, ACCENTS, CUSTOM, swatch


class ColourDot(QtWidgets.QAbstractButton):
    """副色用的圓點。

    比文字更直觀 —— 顏色本身就是標籤，不需要先讀字再想像那是什麼色。
    """

    SIZE = 26

    def __init__(self, key: str, tooltip: str,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self._colour = QtGui.QColor("#4f8cff")
        self.setCheckable(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setToolTip(tooltip)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def set_colour(self, value: str) -> None:
        self._colour = QtGui.QColor(value)
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        centre = QtCore.QPointF(self.width() / 2, self.height() / 2)

        if self.isChecked():
            # 選中的畫一圈外環，靠形狀而不是只靠顏色深淺來區分
            ring = QtGui.QPen(self._colour, 2)
            painter.setPen(ring)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, self.SIZE / 2 - 1.5, self.SIZE / 2 - 1.5)

        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self._colour)
        radius = self.SIZE / 2 - (5 if self.isChecked() else 2.5)
        painter.drawEllipse(centre, radius, radius)

        if self.key == "custom":
            # 自訂色多畫一個缺口，和固定色分得開
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 170), 1.6))
            painter.drawLine(QtCore.QPointF(centre.x() - 4, centre.y()),
                             QtCore.QPointF(centre.x() + 4, centre.y()))
            painter.drawLine(QtCore.QPointF(centre.x(), centre.y() - 4),
                             QtCore.QPointF(centre.x(), centre.y() + 4))


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings: GuiSettings, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self._last_reviewed = ""
        self.setWindowTitle("設定")
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._section("外觀"))
        theme_row = QtWidgets.QHBoxLayout()
        self.theme_box = QtWidgets.QComboBox()
        self.theme_box.addItems(["深色", "淺色"])
        self.theme_box.setCurrentIndex(0 if settings.dark else 1)
        self.theme_box.currentIndexChanged.connect(self._on_theme)
        theme_row.addWidget(QtWidgets.QLabel("主題"))
        theme_row.addWidget(self.theme_box, 1)
        layout.addLayout(theme_row)

        accent_row = QtWidgets.QHBoxLayout()
        accent_row.addWidget(QtWidgets.QLabel("副色"))
        self.accent_buttons: dict[str, ColourDot] = {}
        for key in list(ACCENTS) + [CUSTOM]:
            dot = ColourDot(key, ACCENT_LABELS.get(key, "自訂"))
            dot.setChecked(key == settings.accent)
            dot.clicked.connect(lambda _c, k=key: self._on_accent(k))
            self.accent_buttons[key] = dot
            accent_row.addWidget(dot)
        accent_row.addStretch(1)
        layout.addLayout(accent_row)
        self._refresh_dots()

        layout.addWidget(self._section("快捷鍵"))
        self.key_buttons: dict[str, HotkeyButton] = {}
        for action, text in HOTKEY_LABELS.items():
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(text))
            row.addStretch(1)
            button = HotkeyButton(settings.hotkeys.get(action, ""))
            button.setMinimumWidth(120)
            button.captured.connect(lambda key, a=action: self._on_key(a, key))
            self.key_buttons[action] = button
            row.addWidget(button)
            reset = QtWidgets.QPushButton("重置")
            reset.setToolTip(f"恢復成預設的 {DEFAULT_HOTKEYS[action].upper()}")
            reset.clicked.connect(lambda _c, a=action: self._reset_key(a))
            row.addWidget(reset)
            layout.addLayout(row)

        layout.addWidget(self._section("路徑"))
        self.script_edit = self._path_row(
            layout, "翻譯文本", settings.script_path,
            "翻譯文本 (*.xlsx *.xlsm *.csv *.tsv)")
        self.speakers_edit = self._path_row(
            layout, "發話者對照表", settings.speakers_path,
            "對照表 (*.xlsx *.xlsm *.csv *.tsv *.txt *.md)")
        speaker_hint = QtWidgets.QLabel(
            "xlsx 就第一欄中文、第二欄英文；txt / md / csv 一行一個，"
            "中文在前英文在後用逗號分開")
        speaker_hint.setProperty("role", "hint")
        speaker_hint.setWordWrap(True)
        layout.addWidget(speaker_hint)

        layout.addWidget(self._section("解析完成時"))
        self.notify_check = QtWidgets.QCheckBox("顯示系統通知")
        self.notify_check.setChecked(settings.notify_on_finish)
        layout.addWidget(self.notify_check)

        sound_row = QtWidgets.QHBoxLayout()
        sound_row.addWidget(QtWidgets.QLabel("音效"))
        self.sound_box = QtWidgets.QComboBox()
        self.sound_box.addItem("不播放", sound.NONE)
        for name in sound.available():
            self.sound_box.addItem(name, name)
        index = self.sound_box.findData(settings.sound_on_finish)
        self.sound_box.setCurrentIndex(max(0, index))
        self.sound_box.currentIndexChanged.connect(self._on_sound)
        sound_row.addWidget(self.sound_box, 1)
        self.preview_button = QtWidgets.QPushButton("試聽")
        self.preview_button.clicked.connect(
            lambda: sound.play(self.sound_box.currentData()))
        self.preview_button.setEnabled(bool(settings.sound_on_finish))
        sound_row.addWidget(self.preview_button)
        layout.addLayout(sound_row)

        layout.addWidget(self._section("進階"))
        debug_button = QtWidgets.QPushButton("開啟調試視窗（調整取字範圍與門檻）")
        debug_button.clicked.connect(self._open_debug)
        layout.addWidget(debug_button)

        self.hint = QtWidgets.QLabel("")
        self.hint.setProperty("role", "hint")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        layout.addStretch(1)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("確定")
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _section(self, text: str) -> QtWidgets.QLabel:
        widget = QtWidgets.QLabel(text)
        widget.setProperty("role", "section")
        return widget

    def _path_row(self, layout: QtWidgets.QVBoxLayout, text: str,
                  value: str, filter_: str = "") -> QtWidgets.QLineEdit:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel(text))
        edit = QtWidgets.QLineEdit(value)
        row.addWidget(edit, 1)
        browse = QtWidgets.QPushButton("瀏覽")
        browse.clicked.connect(lambda: self._browse(edit, filter_))
        if "對照表" in text:
            edit.editingFinished.connect(lambda: self._review_speakers(edit.text()))
            check = QtWidgets.QPushButton("檢查")
            check.setToolTip("看看這份表有沒有重複或中英不一致")
            check.clicked.connect(lambda: self._review_speakers(edit.text(), True))
            row.addWidget(check)
        row.addWidget(browse)
        layout.addLayout(row)
        return edit

    def _browse(self, edit: QtWidgets.QLineEdit, filter_: str = "") -> None:
        start = str(Path(edit.text()).parent) if edit.text() else "config"
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "選擇檔案", start, filter_)
        if path:
            edit.setText(path)

    # 外觀改動即時套用，讓使用者直接看到效果
    def _on_theme(self, index: int) -> None:
        self.settings.dark = index == 0
        self._refresh_dots()
        self._live()

    def _on_accent(self, key: str) -> None:
        if key == CUSTOM:
            initial = QtGui.QColor(self.settings.custom_accent)
            chosen = QtWidgets.QColorDialog.getColor(
                initial, self, "選擇副色",
                QtWidgets.QColorDialog.ColorDialogOption.DontUseNativeDialog)
            if not chosen.isValid():
                # 取消就回到原本選的那個，不要留下半選狀態
                self._refresh_dots()
                return
            self.settings.custom_accent = chosen.name()
        self.settings.accent = key
        self._refresh_dots()
        self._live()

    def _refresh_dots(self) -> None:
        for key, dot in self.accent_buttons.items():
            dot.set_colour(swatch(key, self.settings.dark,
                                  self.settings.custom_accent))
            dot.setChecked(key == self.settings.accent)

    def _review_speakers(self, path: str, always: bool = False) -> None:
        """指定對照表之後把問題攤開。

        沒問題時不打擾（除非是自己按「檢查」）—— 每次改個路徑就跳一個
        視窗出來很煩，但有問題時一定要講，那些錯會安靜地毀掉整輪檢查。
        """
        from ..compare.script_loader import read_speaker_map
        from .speaker_dialog import SpeakerTableDialog, summary

        if not path or not Path(path).exists() or path == self._last_reviewed:
            return
        self._last_reviewed = path
        try:
            table = read_speaker_map(path)
        except (OSError, ValueError) as exc:
            self.hint.setText(f"對照表讀不到：{exc}")
            return
        if not always and not table.conflicts and not table.duplicates:
            self.hint.setText(f"對照表 {summary(table)}")
            return
        SpeakerTableDialog(path, self.settings, self).exec()

    def _on_sound(self, _index: int) -> None:
        name = self.sound_box.currentData()
        self.preview_button.setEnabled(bool(name))
        # 選了就放來聽，不必再按一次試聽
        if name:
            sound.play(name)

    def _open_debug(self) -> None:
        """調試視窗不從主畫面進來，避免常用區塊被很少用的東西佔位。

        這個對話框是 modal，調試視窗開在上面會被擋住，所以先收掉自己 ——
        用 accept 而不是 reject，剛才改的設定要留住。
        """
        parent = self.parent()
        self._gather()
        self.accept()
        if parent is not None and hasattr(parent, "open_debug"):
            QtCore.QTimer.singleShot(0, parent.open_debug)

    def _on_key(self, action: str, value: str) -> None:
        # 同一個鍵綁兩個動作時，監聽端只會認得其中一個 ——
        # 與其讓另一個安靜地失效，不如當場把它清掉並講出來
        if value:
            for other, key in self.settings.hotkeys.items():
                if other != action and key == value:
                    self.settings.hotkeys[other] = ""
                    self.key_buttons[other].set_key("")
                    self.hint.setText(
                        f"{value.upper()} 原本是「{HOTKEY_LABELS[other]}」，已改綁到"
                        f"「{HOTKEY_LABELS[action]}」")
                    break
            else:
                self.hint.setText("")
        else:
            self.hint.setText(f"「{HOTKEY_LABELS[action]}」已取消綁定")
        self.settings.hotkeys[action] = value

    def _reset_key(self, action: str) -> None:
        default = DEFAULT_HOTKEYS[action]
        self.key_buttons[action].set_key(default)
        self._on_key(action, default)

    def _live(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "apply_theme"):
            parent.apply_theme()
            self.setStyleSheet(parent.styleSheet())

    def _gather(self) -> None:
        """把輸入框的內容收回設定。外觀那幾項是即時套用的，不在這裡。"""
        self.settings.script_path = self.script_edit.text().strip()
        self.settings.speakers_path = self.speakers_edit.text().strip()
        self.settings.notify_on_finish = self.notify_check.isChecked()
        self.settings.sound_on_finish = self.sound_box.currentData()

    def _accept(self) -> None:
        self._gather()
        self.accept()
