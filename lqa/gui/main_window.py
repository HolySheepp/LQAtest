"""主視窗。

流程由左而右：載入文本 -> 選頁簽 -> 拍攝 -> 解析 -> 看結果。
拍攝時左側清單會高亮「接下來要拍哪一條」，熱鍵全域生效，
所以使用者可以全程待在模擬器裡不必切窗。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ..compare.normalize import display_key, strip_speaker_id
from ..config import Profile
from ..model import CATEGORY_LABEL_ZH, Category
from ..record.bound import BoundCapture
from ..record.project import Project
from ..record.store import SessionStore
from .settings import HOTKEY_LABELS, GuiSettings
from .theme import ACCENT_LABELS, build_qss, palette_for
from .workers import AnalyseWorker, HotkeyWatcher, ScriptLoadWorker


def card(*children: QtWidgets.QWidget, spacing: int = 10) -> QtWidgets.QFrame:
    frame = QtWidgets.QFrame()
    frame.setProperty("role", "card")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(spacing)
    for child in children:
        if isinstance(child, QtWidgets.QLayout):
            layout.addLayout(child)
        else:
            layout.addWidget(child)
    return frame


def label(text: str, role: str = "") -> QtWidgets.QLabel:
    widget = QtWidgets.QLabel(text)
    if role:
        widget.setProperty("role", role)
    return widget


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = GuiSettings.load()
        self.profile: Optional[Profile] = None
        self.sheets: list = []
        self.expected: list = []
        self.project: Optional[Project] = None
        self.sheet: str = ""              # 目前顯示／拍攝的頁簽
        self.shots: dict[int, str] = {}   # 目前頁簽已有的截圖，切換頁簽時重讀
        self.store: Optional[SessionStore] = None
        self.bound: Optional[BoundCapture] = None
        self.capture = None
        self.hotkeys: Optional[HotkeyWatcher] = None
        self.worker: Optional[QtCore.QThread] = None
        self.result = None

        self.setWindowTitle("LQA Checker")
        self.resize(1180, 760)
        self._build()
        self._load_profile()
        self.apply_theme()
        if self.settings.script_path and Path(self.settings.script_path).exists():
            self._load_script(self.settings.script_path)
        self.setAcceptDrops(True)
        self._start_hotkeys()

    def _start_hotkeys(self) -> None:
        """熱鍵監聽全程執行，不是只在拍攝時。

        先前只在 start_capture 裡才啟動，於是「開始」那一下根本沒有人在聽 ——
        按了沒反應，但一旦開始就收得到結束。和用哪個鍵無關。
        """
        self._stop_hotkeys()
        self.hotkeys = HotkeyWatcher(self.settings.hotkeys, self)
        self.hotkeys.pressed.connect(self._on_hotkey)
        self.hotkeys.start()

    def _stop_hotkeys(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys.wait(300)
            self.hotkeys = None

    # ---------- 版面 ----------

    def _build(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_left(), 3)
        body.addWidget(self._build_right(), 2)
        root.addLayout(body, 1)

        self.status = label("", "dim")
        root.addWidget(self.status)

    def _build_header(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(label("LQA Checker", "title"))
        row.addSpacing(14)

        self.script_label = label("尚未載入翻譯文本", "dim")
        row.addWidget(self.script_label, 1)

        pick = QtWidgets.QPushButton("載入文本")
        pick.clicked.connect(self._pick_script)
        row.addWidget(pick)

        debug = QtWidgets.QPushButton("調試")
        debug.clicked.connect(self._open_debug)
        row.addWidget(debug)

        gear = QtWidgets.QPushButton("設定")
        gear.clicked.connect(self._open_settings)
        row.addWidget(gear)
        return row

    def _build_left(self) -> QtWidgets.QWidget:
        self.sheet_list = QtWidgets.QListWidget()
        self.sheet_list.setMaximumHeight(118)
        self.sheet_list.itemChanged.connect(self._on_sheet_checked)
        self.sheet_list.currentRowChanged.connect(self._on_sheet_focused)
        self.sheet_list.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.sheet_list.customContextMenuRequested.connect(self._sheet_menu)

        self.lines = QtWidgets.QTreeWidget()
        self.lines.setHeaderLabels(["", "對話ID", "發話者", "英文翻譯"])
        self.lines.setRootIsDecorated(False)
        self.lines.header().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.lines.setColumnWidth(0, 34)
        self.lines.setColumnWidth(1, 92)
        self.lines.setColumnWidth(2, 96)
        self.lines.itemDoubleClicked.connect(self._on_line_double_clicked)
        self.lines.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.lines.customContextMenuRequested.connect(self._line_menu)

        return card(
            label("頁簽（勾選要檢查的，點一下切換顯示）", "section"),
            self.sheet_list,
            label("條目", "section"),
            self.lines,
        )

    def _build_right(self) -> QtWidgets.QWidget:
        self.progress_label = label("尚未開始", "dim")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)

        self.start_button = QtWidgets.QPushButton("開始拍攝")
        self.start_button.setProperty("role", "primary")
        self.start_button.clicked.connect(self.toggle_capture)
        self.start_button.setEnabled(False)

        self.analyse_button = QtWidgets.QPushButton("開始解析")
        self.analyse_button.clicked.connect(self.start_analysis)
        self.analyse_button.setEnabled(False)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.analyse_button)

        self.hotkey_hint = label("", "hint")
        self.hotkey_hint.setWordWrap(True)

        self.issues = QtWidgets.QTreeWidget()
        self.issues.setHeaderLabels(["分類", "對話ID", "說明"])
        self.issues.setRootIsDecorated(False)
        self.issues.header().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.issues.setColumnWidth(0, 108)
        self.issues.setColumnWidth(1, 92)
        self.issues.itemSelectionChanged.connect(self._on_issue_selected)

        self.detail = QtWidgets.QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(132)

        return card(
            label("拍攝", "section"),
            self.progress_label,
            self.progress,
            buttons,
            self.hotkey_hint,
            label("疑慮條目", "section"),
            self.issues,
            self.detail,
        )

    # ---------- 主題 ----------

    def apply_theme(self) -> None:
        palette = palette_for(self.settings.dark, self.settings.accent)
        self.setStyleSheet(build_qss(palette))
        self._palette = palette
        self._refresh_hotkey_hint()
        self._repaint_lines()

    # ---------- 文本 ----------

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in (".xlsx", ".xlsm", ".csv", ".tsv"):
                self._load_script(path)
                break

    def _pick_script(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "選擇翻譯文本", "scripts",
            "翻譯文本 (*.xlsx *.xlsm *.csv *.tsv)")
        if path:
            self._load_script(path)

    def _load_script(self, path: str) -> None:
        self.script_path = path
        self.script_label.setText(f"讀取中：{Path(path).name}")
        worker = ScriptLoadWorker(path, self)
        worker.loaded.connect(self._on_sheets_loaded)
        worker.failed.connect(lambda msg: self._error("讀不到文本", msg))
        worker.start()
        self._script_worker = worker

    def _on_sheets_loaded(self, sheets: list) -> None:
        self.sheets = sheets
        self.project = Project(self.script_path)
        self.settings.script_path = self.script_path
        self.settings.save()
        self.script_label.setText(
            f"{Path(self.script_path).name}　共 {len(sheets)} 個對白頁簽")

        self.sheet_list.blockSignals(True)
        self.sheet_list.clear()
        for info in sheets:
            taken = self.project.progress(info.name).taken if self.project else 0
            done = f"　已拍 {taken}" if taken else ""
            item = QtWidgets.QListWidgetItem(
                f"{info.name}　{info.line_count} 句{done}")
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, info.name)
            self.sheet_list.addItem(item)
        self.sheet_list.blockSignals(False)
        if sheets:
            self.sheet_list.setCurrentRow(0)
            self.sheet_list.item(0).setCheckState(QtCore.Qt.CheckState.Checked)

    def checked_sheets(self) -> list[str]:
        return [
            self.sheet_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole)
            for i in range(self.sheet_list.count())
            if self.sheet_list.item(i).checkState() == QtCore.Qt.CheckState.Checked
        ]

    def _on_sheet_checked(self, _item: QtWidgets.QListWidgetItem) -> None:
        self.start_button.setEnabled(bool(self.checked_sheets()) and self.profile is not None)

    def _on_sheet_focused(self, row: int) -> None:
        if row < 0 or self.bound is not None:
            return
        self._show_sheet(self.sheets[row].name)
        self.analyse_button.setEnabled(bool(self.shots))

    def _show_sheet(self, name: str) -> None:
        from ..compare.script_loader import load_script, load_speaker_map

        speakers = {}
        if Path(self.settings.speakers_path).exists():
            speakers = load_speaker_map(self.settings.speakers_path)
        try:
            self.expected = load_script(self.script_path, speakers, sheets=[name])
        except (OSError, ValueError) as exc:
            # 例如文本被換掉、頁簽已不存在。是槽函式，丟出去只會印堆疊
            self.status.setText(f"讀不到頁簽「{name}」：{exc}")
            return
        self.sheet = name
        self._reload_shots()
        self._fill_lines()
        # 重開軟體後不必先拍攝也能直接解析既有截圖
        if self.project is not None and self.shots:
            self.store = self.project.store(name)

    def _reload_shots(self) -> None:
        """從磁碟重讀目前頁簽的截圖。

        切換頁簽或重開軟體後標記要還在 —— 先前截圖只存在記憶體裡，
        離開那個頁簽進度就看不見了。
        """
        if self.project is None or not self.sheet:
            self.shots = {}
            return
        self.shots = self.project.progress(self.sheet).shots
        stale = self.project.stale_entries(self.sheet, self.expected)
        if stale:
            self.status.setText(
                f"注意：{len(stale)} 張舊截圖的對話ID 與目前文本對不上"
                "（文本可能改過），建議清除後重拍")

    def _fill_lines(self) -> None:
        self.lines.clear()
        for line in self.expected:
            item = QtWidgets.QTreeWidgetItem([
                "", line.dialogue_id,
                line.speaker_en or line.speaker_zh or "（旁白）",
                display_key(line.target_en),
            ])
            self.lines.addTopLevelItem(item)
        self._repaint_lines()

    # ---------- 拍攝 ----------

    def _load_profile(self) -> None:
        try:
            self.profile = Profile.load(self.settings.profile_path)
            self.profile.validate()
        except (OSError, ValueError) as exc:
            self.profile = None
            self.status.setText(f"profile 有問題：{exc}")

    def toggle_capture(self) -> None:
        if self.bound is None:
            self.start_capture()
        else:
            self.stop_capture()

    def start_capture(self) -> None:
        if self.profile is None or self.project is None:
            return
        row = self.sheet_list.currentRow()
        if row < 0:
            self._error("無法開始拍攝", "請先點選一個頁簽")
            return
        from ..capture.mss_backend import open_capture

        sheet = self.sheets[row].name
        if sheet != self.sheet:
            self._show_sheet(sheet)
        try:
            self.capture = open_capture(
                self.profile.window_title, self.profile.capture_region,
                self.profile.capture_backend, roi=self.profile.body_roi)
        except Exception as exc:
            self._error("無法開始拍攝", str(exc))
            return

        self.project.write_sheet_meta(sheet, self.profile, [sheet])
        self.store = self.project.store(sheet)
        self.bound = BoundCapture(self.store, self.capture, len(self.expected))
        self.bound.state.shots = dict(self.shots)      # 接續既有進度
        self.bound.move_to(self._first_gap())

        self.start_button.setText("結束拍攝")
        self.analyse_button.setEnabled(False)
        self.sheet_list.setEnabled(False)
        self._update_progress()

    def _first_gap(self) -> int:
        """從第一個還沒拍的條目接續，而不是每次都從頭開始。"""
        for index in range(len(self.expected)):
            if index not in self.shots:
                return index
        return len(self.expected)

    def stop_capture(self) -> None:
        # 不要在這裡關掉熱鍵監聽 —— 關了就再也按不了「開始」
        if self.capture:
            self.capture.close()
            self.capture = None
        if self.store:
            self.store.close()
        self.bound = None
        self._reload_shots()
        self._refresh_sheet_counts()
        self.start_button.setText("開始拍攝")
        self.sheet_list.setEnabled(True)
        self.analyse_button.setEnabled(bool(self.shots))
        self.progress_label.setText(f"拍攝結束，共 {len(self.shots)} 張")
        self._repaint_lines()

    def _refresh_sheet_counts(self) -> None:
        if self.project is None:
            return
        for i in range(self.sheet_list.count()):
            item = self.sheet_list.item(i)
            info = self.sheets[i]
            taken = self.project.progress(info.name).taken
            done = f"　已拍 {taken}" if taken else ""
            item.setText(f"{info.name}　{info.line_count} 句{done}")

    def _on_hotkey(self, action: str) -> None:
        if action == "toggle":
            self.toggle_capture()
            return
        if self.bound is None:
            return
        if action == "shoot":
            index = self.bound.state.cursor
            if self.bound.shoot() is None:
                self.status.setText(
                    "擷取失敗：" + (self.capture.unavailable() or "已拍完所有條目"))
            elif index < len(self.expected):
                self.project.record_shot(
                    self.sheet, index, self.expected[index].dialogue_id)
        elif action == "skip":
            self.bound.skip()
        elif action == "back":
            self.bound.back()
        elif action == "clear":
            # 刪掉目前這條的截圖，退回「未截圖」
            index = self.bound.state.cursor
            self.bound.discard(index)
            self.project.clear_entry(self.sheet, index)
            self.status.setText(f"已清除第 {index + 1} 條的截圖")
        self._update_progress()

    def _update_progress(self) -> None:
        if self.bound is None:
            return
        state = self.bound.state
        self.shots = dict(state.shots)
        self.progress.setMaximum(max(1, state.total))
        self.progress.setValue(state.taken)
        self.progress_label.setText(
            f"第 {min(state.cursor + 1, state.total)} / {state.total} 條　已拍 {state.taken} 張")
        self._repaint_lines()
        self._scroll_to_cursor()

    def _scroll_to_cursor(self) -> None:
        if self.bound is None:
            return
        index = min(self.bound.state.cursor, self.lines.topLevelItemCount() - 1)
        if index >= 0:
            self.lines.scrollToItem(self.lines.topLevelItem(index),
                                    QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _repaint_lines(self) -> None:
        if not hasattr(self, "_palette"):
            return
        accent = QtGui.QColor(self._palette.accent)
        soft = QtGui.QColor(accent)
        soft.setAlpha(46)
        done = QtGui.QColor(self._palette.success)
        dim = QtGui.QColor(self._palette.text_dim)
        plain = QtGui.QColor(self._palette.text)
        cursor = self.bound.state.cursor if self.bound else -1
        shots = self.shots

        for i in range(self.lines.topLevelItemCount()):
            item = self.lines.topLevelItem(i)
            # 游標優先於「已拍」：退回到拍過的條目時要看得出游標在哪，
            # 否則使用者不知道下一張會覆蓋掉誰
            if i == cursor:
                item.setText(0, ">")
                mark = done if i in shots else accent
                fg, bg = accent, soft
            elif i in shots:
                item.setText(0, "•")
                mark, fg, bg = done, plain, QtGui.QColor(0, 0, 0, 0)
            elif i < cursor:
                item.setText(0, "-")
                mark, fg, bg = dim, dim, QtGui.QColor(0, 0, 0, 0)
            else:
                item.setText(0, "")
                mark, fg, bg = plain, plain, QtGui.QColor(0, 0, 0, 0)
            item.setForeground(0, mark)
            for column in range(1, 4):
                item.setForeground(column, fg)
                item.setBackground(column, bg)
            item.setBackground(0, bg)

    def _sheet_menu(self, pos: QtCore.QPoint) -> None:
        item = self.sheet_list.itemAt(pos)
        if item is None or self.project is None or self.bound is not None:
            return
        name = item.data(QtCore.Qt.ItemDataRole.UserRole)
        taken = self.project.progress(name).taken
        menu = QtWidgets.QMenu(self)
        action = menu.addAction(f"清除「{name}」的 {taken} 張截圖")
        action.setEnabled(taken > 0)
        if menu.exec(self.sheet_list.mapToGlobal(pos)) is action and taken:
            if self._confirm(f"要刪掉「{name}」的 {taken} 張截圖嗎？"):
                removed = self.project.clear_sheet(name)
                if name == self.sheet:
                    self._reload_shots()
                    self._repaint_lines()
                self._refresh_sheet_counts()
                self.status.setText(f"已清除「{name}」的 {removed} 張截圖")

    def _line_menu(self, pos: QtCore.QPoint) -> None:
        item = self.lines.itemAt(pos)
        if item is None or self.project is None:
            return
        index = self.lines.indexOfTopLevelItem(item)
        menu = QtWidgets.QMenu(self)
        clear = menu.addAction("清除這條的截圖")
        clear.setEnabled(index in self.shots)
        jump = menu.addAction("把游標移到這條")
        jump.setEnabled(self.bound is not None)
        chosen = menu.exec(self.lines.mapToGlobal(pos))
        if chosen is clear and index in self.shots:
            self.project.clear_entry(self.sheet, index)
            if self.bound is not None:
                self.bound.discard(index)
                self.bound.state.shots.pop(index, None)
            self._reload_shots()
            self._refresh_sheet_counts()
            self._repaint_lines()
            self.status.setText(f"已清除第 {index + 1} 條的截圖")
        elif chosen is jump and self.bound is not None:
            self.bound.move_to(index)
            self._update_progress()

    def _confirm(self, message: str) -> bool:
        return QtWidgets.QMessageBox.question(
            self, "確認", message,
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        ) == QtWidgets.QMessageBox.StandardButton.Yes

    def _on_line_double_clicked(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """雙擊條目把游標移過去，方便補拍。"""
        if self.bound is None:
            return
        self.bound.move_to(self.lines.indexOfTopLevelItem(item))
        self._update_progress()

    # ---------- 解析 ----------

    def start_analysis(self) -> None:
        if self.store is None or self.profile is None:
            return
        self.analyse_button.setEnabled(False)
        self.start_button.setEnabled(False)
        worker = AnalyseWorker(
            self.store.dir, self.profile, self.script_path,
            [self.sheet], self.settings.speakers_path, self)
        worker.progress.connect(self._on_analysis_progress)
        worker.finished_ok.connect(self._on_analysis_done)
        worker.failed.connect(lambda msg: self._error("解析失敗", msg))
        worker.finished.connect(lambda: self.start_button.setEnabled(True))
        worker.start()
        self.worker = worker

    def _on_analysis_progress(self, done: int, total: int, text: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        self.progress_label.setText(f"解析中 {done}/{total}")
        self.status.setText(display_key(text)[:90])

    def _on_analysis_done(self, result) -> None:
        self.result = result
        if self.project is not None and self.sheet:
            self.project.mark_analysed(self.sheet)
        self.progress_label.setText("解析完成")
        self.analyse_button.setEnabled(True)
        self._fill_issues(result)
        counts = result.summary()
        problems = len(result.problems)
        self.status.setText(
            f"共 {len(result.expected)} 條，疑慮 {problems} 筆　"
            + "　".join(f"{CATEGORY_LABEL_ZH[c]} {counts[c.value]}"
                        for c in Category if counts.get(c.value)))
        if self.settings.notify_on_finish:
            self._notify("解析完成", f"{len(result.expected)} 條中有 {problems} 筆疑慮")

    def _fill_issues(self, result) -> None:
        self.issues.clear()
        palette = self._palette
        colour = {
            Category.UNTRANSLATED: palette.danger,
            Category.TRUNCATED: palette.warning,
            Category.MISMATCH: palette.warning,
            Category.SPEAKER: palette.accent,
            Category.MISSING: palette.text_dim,
            Category.NOT_CAPTURED: palette.text_dim,
            Category.ORDER: palette.accent,
            Category.EXTRA: palette.text_dim,
        }
        # 「未截圖」是使用者自己跳過的，不是遊戲的問題，排到最下面
        ordered = sorted(result.problems,
                         key=lambda i: (i.category is Category.NOT_CAPTURED,
                                        i.expected_order or i.actual_order or 0))
        for issue in ordered:
            item = QtWidgets.QTreeWidgetItem([
                CATEGORY_LABEL_ZH[issue.category],
                issue.dialogue_id or "",
                issue.detail,
            ])
            item.setForeground(0, QtGui.QColor(colour.get(issue.category, palette.text)))
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, issue)
            self.issues.addTopLevelItem(item)

    def _on_issue_selected(self) -> None:
        items = self.issues.selectedItems()
        if not items:
            return
        issue = items[0].data(0, QtCore.Qt.ItemDataRole.UserRole)
        parts = []
        if issue.expected:
            parts.append(f"文本　{display_key(issue.expected.target_en)}")
            speaker = issue.expected.speaker_en or issue.expected.speaker_zh
            if speaker:
                parts.append(f"發話者　{speaker}")
        if issue.captured:
            parts.append(f"畫面　{display_key(issue.captured.body_text)}")
            if issue.captured.speaker_text:
                parts.append(f"畫面發話者　{strip_speaker_id(issue.captured.speaker_text)}")
            if issue.captured.screenshot and self.store:
                parts.append(f"截圖　{self.store.dir / issue.captured.screenshot}")
        if issue.similarity:
            parts.append(f"相似度　{issue.similarity:.0%}")
        self.detail.setPlainText("\n".join(parts))

    # ---------- 雜項 ----------

    def _refresh_hotkey_hint(self) -> None:
        keys = self.settings.hotkeys
        self.hotkey_hint.setText("　".join(
            f"{keys[name].upper()} {HOTKEY_LABELS[name]}" for name in HOTKEY_LABELS
            if name in keys))

    def _notify(self, title: str, body: str) -> None:
        if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            if not hasattr(self, "_tray"):
                self._tray = QtWidgets.QSystemTrayIcon(self.windowIcon(), self)
                self._tray.show()
            self._tray.showMessage(title, body)

    def _error(self, title: str, message: str) -> None:
        self.status.setText(f"{title}：{message}")
        QtWidgets.QMessageBox.warning(self, title, message)

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings.save()
            self.apply_theme()
            self._start_hotkeys()      # 熱鍵可能被改過，重新掛上

    def _open_debug(self) -> None:
        from .debug_window import DebugWindow

        if self.profile is None:
            self._error("無法調試", "profile 讀不到，請先用 lqa calibrate 校準")
            return
        window = DebugWindow(self.profile, self.settings, self)
        window.profileSaved.connect(self._load_profile)
        window.show()
        self._debug_window = window

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.bound is not None:
            self.stop_capture()
        self._stop_hotkeys()
        self.settings.save()
        super().closeEvent(event)
