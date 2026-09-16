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
from .results import (ALL, FILTER_LABELS, is_visible, next_flagged,
                      rows_from_result, shot_name)
from .settings import HOTKEY_LABELS, GuiSettings
from .theme import build_qss, mix, palette_for
from .titlebar import FramelessMixin, title_bar_qss
from .workers import (AnalyseWorker, AutoRecordWorker, HotkeyWatcher,
                      ScriptLoadWorker)


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


class ShotView(QtWidgets.QLabel):
    """截圖預覽。

    縮放結果要記下來：setPixmap 會改變 sizeHint，進而觸發 resizeEvent，
    如果每次 resize 都無條件重算就會無限遞迴（調試視窗就這樣當過）。
    高度設上限，所以縮放只跟寬度有關，寬度沒變就什麼都不做。
    """

    MAX_HEIGHT = 240

    def __init__(self) -> None:
        super().__init__()
        self._source = QtGui.QPixmap()
        self._drawn_width = -1
        self.double_clicked = None
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(90)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                           QtWidgets.QSizePolicy.Policy.Preferred)

    def set_shot(self, pixmap: QtGui.QPixmap) -> None:
        self._source = pixmap
        self._drawn_width = -1
        self._rescale()

    def _rescale(self) -> None:
        if self._source.isNull():
            self.setPixmap(QtGui.QPixmap())
            self.setText("（沒有截圖）")
            return
        width = max(80, self.width() - 4)
        if width == self._drawn_width:
            return
        self._drawn_width = width
        self.setPixmap(self._source.scaled(
            QtCore.QSize(width, self.MAX_HEIGHT - 4),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale()

    def mouseDoubleClickEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self.double_clicked is not None and not self._source.isNull():
            self.double_clicked()


class RowTint(QtWidgets.QStyledItemDelegate):
    """條目的底色。

    套了樣式表之後，QTreeWidget::item 的規則會接管項目的背景繪製，
    setBackground 設的筆刷就再也畫不出來 —— 黃底、灰底、拍攝游標的
    highlight 全部靜靜地沒有效果。所以自己先填一層再交給預設繪製。

    選取中的那條不填，不然看不出游標停在哪。
    """

    def paint(self, painter, option, index):
        brush = index.data(QtCore.Qt.ItemDataRole.BackgroundRole)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        if brush is not None and not selected:
            colour = brush.color() if isinstance(brush, QtGui.QBrush) else brush
            if isinstance(colour, QtGui.QColor) and colour.alpha():
                painter.fillRect(option.rect, colour)
        super().paint(painter, option, index)


class MainWindow(FramelessMixin, QtWidgets.QMainWindow):
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
        self.auto_worker: Optional[QtCore.QThread] = None
        self.result = None
        self.rows: dict = {}              # 列號 -> RowResult，解析後才有
        self.filter_mode = ALL
        self._heading_clicks = 0
        self._heading_last = QtCore.QTime.currentTime()

        self.setWindowTitle("LQA Checker")
        self.resize(1340, 800)
        self._apply_icon()
        self._build()
        self._load_profile()
        self.apply_theme()
        if self.settings.script_path and Path(self.settings.script_path).exists():
            self._load_script(self.settings.script_path)
        self.setAcceptDrops(True)
        self._start_hotkeys()
        self._install_shortcuts()
        self.apply_developer_mode()

    def _install_shortcuts(self) -> None:
        """Ctrl + 上下：直接跳到下一條有疑慮的，不必一條條翻。

        用 QShortcut 而不是攔截條目表的鍵盤事件，這樣焦點在哪都有效。
        """
        for keys, step in (("Ctrl+Down", 1), ("Ctrl+Up", -1)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            shortcut.activated.connect(lambda s=step: self._jump_flagged(s))

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

    def _apply_icon(self) -> None:
        from pathlib import Path as _Path

        icon_path = _Path(__file__).parent / "assets" / "icon.ico"
        if not icon_path.exists():
            return
        icon = QtGui.QIcon(str(icon_path))
        self.setWindowIcon(icon)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        self._icon = icon

    def _build(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = self.setup_frameless("LQA Checker")
        if hasattr(self, "_icon"):
            bar.set_icon(self._icon)
        outer.addWidget(bar)

        body_widget = QtWidgets.QWidget()
        outer.addWidget(body_widget, 1)
        root = QtWidgets.QVBoxLayout(body_widget)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        # 條目表現在同時放譯文和遊戲內文，兩欄要對照著讀，左邊給多一點
        body.addWidget(self._build_left(), 5)
        body.addWidget(self._build_right(), 2)
        root.addLayout(body, 1)

        self.status = label("", "dim")
        root.addWidget(self.status)

    def _build_header(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        self.script_label = label("尚未載入翻譯文本", "dim")
        row.addWidget(self.script_label, 1)

        pick = QtWidgets.QPushButton("載入文本")
        pick.clicked.connect(self._pick_script)
        row.addWidget(pick)

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
        self.lines.setHeaderLabels(
            ["", "對話ID", "發話者", "翻譯文本", "遊戲內文", "疑慮分類"])
        self.lines.setRootIsDecorated(False)
        self.lines.setUniformRowHeights(True)
        self.lines.setItemDelegate(RowTint(self.lines))
        self.lines.setAllColumnsShowFocus(True)
        self.lines.setToolTip("上下鍵看條目，Ctrl + 上下鍵只跳疑慮條目")
        header = self.lines.header()
        # 譯文和遊戲內文是要對照著看的，兩欄等寬平分剩餘空間
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.lines.setColumnWidth(0, 30)
        self.lines.setColumnWidth(1, 88)
        self.lines.setColumnWidth(2, 92)
        self.lines.setColumnWidth(5, 112)
        self.lines.itemDoubleClicked.connect(self._on_line_double_clicked)
        self.lines.currentItemChanged.connect(self._on_line_current)
        self.lines.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.lines.customContextMenuRequested.connect(self._line_menu)

        self.lines_heading = label("條目", "section")
        # 連點標題多下開啟開發者視窗，不佔用正常介面的空間
        self.lines_heading.mousePressEvent = self._heading_clicked

        head = QtWidgets.QHBoxLayout()
        head.addWidget(self.lines_heading)
        head.addStretch(1)
        self.filters: dict[str, QtWidgets.QPushButton] = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for mode, text in FILTER_LABELS:
            chip = QtWidgets.QPushButton(text)
            chip.setProperty("role", "chip")
            chip.setCheckable(True)
            chip.setChecked(mode == ALL)
            chip.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            chip.setToolTip("Ctrl + 上下鍵可以直接在疑慮條目之間移動")
            chip.clicked.connect(lambda _c, m=mode: self._set_filter(m))
            group.addButton(chip)
            self.filters[mode] = chip
            head.addWidget(chip)
        self._filter_group = group
        self._refresh_filter_labels()

        return card(
            label("檔案", "section"),
            self.sheet_list,
            head,
            self.lines,
        )

    # ---------- 篩選 ----------

    def _set_filter(self, mode: str) -> None:
        self.filter_mode = mode
        self.filters[mode].setChecked(True)      # 也可能是程式改的，按鈕要跟上
        self._apply_filter()

    def _apply_filter(self) -> None:
        """套用篩選。被藏起來的列不能留著游標，否則上下鍵會跳進看不見的地方。"""
        current = self.lines.currentItem()
        first_visible = None
        for i in range(self.lines.topLevelItemCount()):
            item = self.lines.topLevelItem(i)
            shown = is_visible(self.rows.get(i), self.filter_mode)
            item.setHidden(not shown)
            if shown and first_visible is None:
                first_visible = item
        if current is not None and current.isHidden():
            self.lines.setCurrentItem(first_visible)
        shown_count = sum(not self.lines.topLevelItem(i).isHidden()
                          for i in range(self.lines.topLevelItemCount()))
        if self.filter_mode != ALL:
            self.status.setText(f"篩選中：顯示 {shown_count} 條")

    def _refresh_filter_labels(self) -> None:
        """把條數寫進按鈕，不必切過去才知道有幾條。"""
        flagged = sum(1 for row in self.rows.values() if row.flagged)
        skipped = sum(1 for row in self.rows.values() if row.not_captured)
        from .results import FLAGGED, NOT_CAPTURED

        self.filters[FLAGGED].setText(
            f"只顯示疑慮條目{f'（{flagged}）' if self.rows else ''}")
        self.filters[NOT_CAPTURED].setText(
            f"只顯示未截圖條目{f'（{skipped}）' if self.rows else ''}")
        for mode in (FLAGGED, NOT_CAPTURED):
            self.filters[mode].setEnabled(bool(self.rows))

    def _heading_clicked(self, _event) -> None:
        now = QtCore.QTime.currentTime()
        if self._heading_last.msecsTo(now) > 600:
            self._heading_clicks = 0
        self._heading_last = now
        self._heading_clicks += 1
        if self._heading_clicks >= 5:
            self._heading_clicks = 0
            self._open_developer()

    def apply_developer_mode(self) -> None:
        self.auto_button.setVisible(self.settings.developer_mode)

    def toggle_auto_record(self) -> None:
        if self.auto_worker is not None:
            self._stop_auto_record()
            return
        if self.bound is None:
            self._error("無法自動錄製", "請先按「開始錄製」，自動錄製會填進目前的游標位置")
            return
        worker = AutoRecordWorker(self.profile, self.settings.auto_poll_ms, self)
        worker.line_ready.connect(self._on_auto_line)
        worker.status.connect(self.status.setText)
        worker.failed.connect(lambda m: (self._stop_auto_record(),
                                         self._error("自動錄製失敗", m)))
        worker.start()
        self.auto_worker = worker
        self.auto_button.setText("停止自動")
        self.status.setText("自動錄製中，偵測到一句結束就會自動拍下")

    def _stop_auto_record(self) -> None:
        if self.auto_worker is not None:
            self.auto_worker.stop()
            self.auto_worker.wait(1500)
            self.auto_worker = None
        self.auto_button.setText("自動錄製")

    def _on_auto_line(self, frame) -> None:
        """自動偵測到一句結束，寫進目前游標所在的條目。"""
        if self.bound is None:
            return
        index = self.bound.state.cursor
        if self.bound.shoot(frame) is not None and index < len(self.expected):
            self.project.record_shot(
                self.sheet, index, self.expected[index].dialogue_id)
        self._update_progress()

    def _open_developer(self) -> None:
        from .developer_window import DeveloperWindow

        window = DeveloperWindow(self, self.settings)
        window.show()
        self._developer_window = window

    def _build_right(self) -> QtWidgets.QWidget:
        self.progress_label = label("尚未開始", "dim")
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)

        self.start_button = QtWidgets.QPushButton("開始錄製")
        self.start_button.setProperty("role", "primary")
        self.start_button.clicked.connect(self.toggle_capture)
        self.start_button.setEnabled(False)

        self.analyse_button = QtWidgets.QPushButton("開始解析")
        self.analyse_button.clicked.connect(self.start_analysis)
        self.analyse_button.setEnabled(False)

        self.auto_button = QtWidgets.QPushButton("自動錄製")
        self.auto_button.setToolTip("實驗中：偵測打字結束並自動截圖")
        self.auto_button.clicked.connect(self.toggle_auto_record)
        self.auto_button.setVisible(False)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.analyse_button)
        buttons.addWidget(self.auto_button)

        self.hotkey_hint = label("", "hint")
        self.hotkey_hint.setWordWrap(True)

        capture_card = card(
            label("錄製", "section"),
            self.progress_label,
            self.progress,
            buttons,
            self.hotkey_hint,
        )

        self.detail_title = label("選一條看細節", "dim")
        # 發話者各自放在自己那一欄的標題與內容之間。
        # 文本的發話者是從翻譯檔讀的，畫面的是從遊戲裡認出來的，
        # 兩者本來就可能不一樣 —— 並排才看得出哪裡對不上
        self.detail_expected_speaker = label("", "dim")
        self.detail_actual_speaker = label("", "dim")
        self.detail_expected = self._detail_box()
        self.detail_actual = self._detail_box()
        self.detail_verdict = label("", "")
        self.detail_verdict.setWordWrap(True)
        self.detail_shot = ShotView()
        self.detail_shot.setToolTip("點兩下開啟原圖")
        self.detail_shot.double_clicked = self._open_full_shot

        detail_card = card(
            self.detail_title,
            label("翻譯文本", "section"),
            self.detail_expected_speaker,
            self.detail_expected,
            label("遊戲內文", "section"),
            self.detail_actual_speaker,
            self.detail_actual,
            label("判定", "section"),
            self.detail_verdict,
            label("截圖", "section"),
            self.detail_shot,
        )

        holder = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)
        column.addWidget(capture_card)
        column.addWidget(detail_card, 1)
        return holder

    def _detail_box(self) -> QtWidgets.QPlainTextEdit:
        box = QtWidgets.QPlainTextEdit()
        box.setReadOnly(True)
        box.setMaximumHeight(78)
        box.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        return box

    # ---------- 主題 ----------

    def apply_theme(self) -> None:
        palette = palette_for(self.settings.dark, self.settings.accent,
                               self.settings.custom_accent)
        self.setStyleSheet(build_qss(palette) + title_bar_qss(palette))
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
            f"{Path(self.script_path).name}　共 {len(sheets)} 個頁簽")

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
        self._close_store()
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
        # 換頁簽等於換一份結果，舊的判定不能留在畫面上誤導人
        self.rows = {}
        self.result = None
        self.filter_mode = ALL
        self.filters[ALL].setChecked(True)
        self._refresh_filter_labels()
        for line in self.expected:
            item = QtWidgets.QTreeWidgetItem([
                "", line.dialogue_id,
                line.speaker_en or line.speaker_zh or "（旁白）",
                display_key(line.target_en), "", "",
            ])
            self.lines.addTopLevelItem(item)
        self._repaint_lines()
        self._show_detail(self.lines.currentItem())

    def _fill_results(self, result) -> None:
        """把解析結果寫回條目表。"""
        self.rows = rows_from_result(result)
        for index in range(self.lines.topLevelItemCount()):
            item = self.lines.topLevelItem(index)
            row = self.rows.get(index)
            if row is None:
                item.setText(4, "")
                item.setText(5, "")
                continue
            captured = row.captured
            item.setText(4, display_key(captured.body_text) if captured else "")
            item.setText(5, row.label)
        self._refresh_filter_labels()
        self._repaint_lines()
        self._apply_filter()
        # 直接跳到第一條有疑慮的，那才是使用者要看的
        first = next_flagged(-1, self.lines.topLevelItemCount(), self.rows, 1)
        if first >= 0 and self.rows.get(first) is not None:
            self._select_row(first)
        else:
            self._show_detail(self.lines.currentItem())

    # ---------- 細節面板 ----------

    def _select_row(self, index: int) -> None:
        item = self.lines.topLevelItem(index)
        if item is None:
            return
        self.lines.setCurrentItem(item)
        self.lines.scrollToItem(
            item, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _jump_flagged(self, step: int) -> None:
        """Ctrl + 上下：只在有疑慮的條目間移動。"""
        if not self.rows:
            return
        current = self.lines.indexOfTopLevelItem(self.lines.currentItem())
        target = next_flagged(current, self.lines.topLevelItemCount(),
                              self.rows, step)
        if target != current:
            self._select_row(target)

    def _on_line_current(self, item, _previous=None) -> None:
        self._show_detail(item)

    def _show_detail(self, item) -> None:
        index = self.lines.indexOfTopLevelItem(item) if item is not None else -1
        if index < 0 or index >= len(self.expected):
            self.detail_title.setText("選一條看細節")
            self._set_speaker_label(self.detail_expected_speaker, "", "")
            self._set_speaker_label(self.detail_actual_speaker, "", "")
            self.detail_expected.setPlainText("")
            self.detail_actual.setPlainText("")
            self.detail_verdict.setText("")
            self.detail_shot.set_shot(QtGui.QPixmap())
            return

        line = self.expected[index]
        self.detail_title.setText(f"第 {index + 1} 條　{line.dialogue_id}")
        self._set_speaker_label(self.detail_expected_speaker,
                                line.speaker_en or line.speaker_zh or "（旁白）",
                                self._palette.text_dim)
        self.detail_expected.setPlainText(display_key(line.target_en))

        row = self.rows.get(index)
        captured = row.captured if row is not None else None
        self._set_actual_speaker(row, captured)
        if captured is not None:
            self.detail_actual.setPlainText(display_key(captured.body_text))
        elif row is not None:
            self.detail_actual.setPlainText("（未截圖）")
        elif index in self.shots:
            self.detail_actual.setPlainText("（已截圖，按「開始解析」才會讀出文字）")
        else:
            self.detail_actual.setPlainText("（尚未截圖）")

        self.detail_verdict.setText(self._verdict_text(row))
        self.detail_shot.set_shot(self._shot_pixmap(captured, index))

    def _set_actual_speaker(self, row, captured) -> None:
        """畫面上認出來的發話者。

        和文本那邊分開顯示，不要塞進內文開頭 —— 這兩個是不同來源的東西，
        混在一起就看不出是誰跟誰對不上。對不上時標紅，那正是要看的重點。
        """
        if captured is None:
            self._set_speaker_label(self.detail_actual_speaker, "", "")
            return
        wrong = row is not None and any(
            issue.category is Category.SPEAKER for issue in row.issues)
        self._set_speaker_label(
            self.detail_actual_speaker,
            strip_speaker_id(captured.speaker_text) or "（畫面沒讀到發話者）",
            self._palette.danger if wrong else self._palette.text_dim)

    @staticmethod
    def _set_speaker_label(widget, text: str, colour: str) -> None:
        """沒有發話者就整行收掉，不要留一條空白帶。"""
        widget.setVisible(bool(text))
        widget.setText(text)
        if colour:
            widget.setStyleSheet(f"color: {colour};")

    def _unmapped_speakers(self) -> list[str]:
        """文本有發話者、但對照表給不出英文名的那些中文名。"""
        from ..compare.script_loader import unknown_speakers

        try:
            return sorted(unknown_speakers(self.expected))
        except Exception:      # 提醒而已，壞掉不該影響解析結果
            return []

    def _verdict_text(self, row) -> str:
        if row is None:
            return "尚未解析"
        # 說明裡通常已經帶了相似度，不另外再列一次
        return "\n".join([row.label] + ([row.detail] if row.detail else []))

    def _shot_pixmap(self, captured, index: int) -> QtGui.QPixmap:
        """這一條的截圖。解析前也要看得到。

        剛拍完就點條目確認「這張到底拍到什麼」是最自然的動作，
        沒道理要等解析完才給看。解析後用紀錄裡的路徑，
        解析前直接看磁碟上有沒有這一條的截圖。
        """
        self._full_shot = None
        path = self._shot_path(captured, index)
        if path is None or not path.exists():
            return QtGui.QPixmap()
        self._full_shot = path
        return self._crop_to_dialogue(QtGui.QPixmap(str(path)),
                                      self._layout_of(captured, path))

    def _shot_path(self, captured, index: int) -> Optional[Path]:
        name = shot_name(captured, self.shots, index)
        if not name:
            return None
        if self.store is not None:
            return self.store.dir / name
        if self.project is not None and self.sheet:
            return self.project.sheet_dir(self.sheet) / name
        return None

    def _layout_of(self, captured, path: Path) -> str:
        """這張截圖用的是哪一套版面。

        解析過就直接看紀錄。還沒解析、而且真的有兩套版面時才自己判一次 ——
        判錯會裁到空白處。只有一套版面就不必算，省下每次點選的開銷。
        """
        if captured is not None and captured.layout:
            return captured.layout
        if self.profile is None or len(self.profile.layouts()) < 2:
            return ""
        from ..imageio import imread
        from ..record.reader import pick_layout

        frame = imread(path)
        if frame is None:
            return ""
        return pick_layout(frame, self.profile.layouts())[0].key

    def _crop_to_dialogue(self, pixmap: QtGui.QPixmap,
                          layout_key: str = "") -> QtGui.QPixmap:
        """裁到對白框附近再顯示。

        存下來的是整個模擬器視窗，而手遊是直式的 —— 整張塞進側邊欄
        只剩一百多像素寬，字根本認不出來，那這個預覽就白放了。
        要看原圖可以點兩下，或右鍵「開啟截圖」。
        """
        if self.profile is None or pixmap.isNull():
            return pixmap
        # 裁哪一塊要看這張截圖用的是哪套版面 —— NPC 對白框和一般對白
        # 不在同一個位置，裁錯就整片空白。舊資料沒記版面，退回一般
        layouts = self.profile.layouts()
        layout = next((l for l in layouts if l.key == layout_key), layouts[0])
        rois = [r for r in (layout.speaker_roi, layout.body_roi) if r]
        if not rois:
            return pixmap
        left = min(r[0] for r in rois)
        top = min(r[1] for r in rois)
        rect = QtCore.QRect(left, top,
                            max(r[0] + r[2] for r in rois) - left,
                            max(r[1] + r[3] for r in rois) - top)
        rect = rect.adjusted(-10, -10, 10, 10).intersected(pixmap.rect())
        return pixmap.copy(rect) if rect.isValid() else pixmap

    def _open_full_shot(self) -> None:
        path = getattr(self, "_full_shot", None)
        if path is not None and path.exists():
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(path.resolve())))

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
            self._error("無法開始錄製", "請先點選一個頁簽")
            return
        from ..capture.mss_backend import open_capture

        sheet = self.sheets[row].name
        if sheet != self.sheet:
            self._show_sheet(sheet)
        try:
            self.capture = open_capture(
                self.profile.window_title, self.profile.capture_region,
                self.profile.capture_backend, roi=self.profile.watch_roi())
        except Exception as exc:
            self._error("無法開始錄製", str(exc))
            return

        self.project.write_sheet_meta(sheet, self.profile, [sheet])
        self.store = self.project.store(sheet)
        self.bound = BoundCapture(self.store, self.capture, len(self.expected))
        self.bound.state.shots = dict(self.shots)      # 接續既有進度
        self.bound.move_to(self._first_gap())

        self.start_button.setText("結束錄製")
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
        self._stop_auto_record()
        # 不要在這裡關掉熱鍵監聽 —— 關了就再也按不了「開始」
        if self.capture:
            self.capture.close()
            self.capture = None
        if self.store is not None:
            self.store.close()      # 解析還要用 store.dir，物件本身留著
        self.bound = None
        self._reload_shots()
        self._refresh_sheet_counts()
        self.start_button.setText("開始錄製")
        self.sheet_list.setEnabled(True)
        self.analyse_button.setEnabled(bool(self.shots))
        self.progress_label.setText(f"錄製結束，共 {len(self.shots)} 張")
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
        # 解析結果用底色表示：黃底有疑慮、灰底沒截到。
        # 混進 surface 而不是用純黃，深淺主題都還讀得到字
        flagged_bg = QtGui.QColor(mix(self._palette.warning, self._palette.surface, 0.30))
        skipped_bg = QtGui.QColor(mix(self._palette.text_dim, self._palette.surface, 0.16))
        blank = QtGui.QColor(0, 0, 0, 0)

        for i in range(self.lines.topLevelItemCount()):
            item = self.lines.topLevelItem(i)
            row = self.rows.get(i)
            if row is None:
                result_bg = blank
            elif row.flagged:
                result_bg = flagged_bg
            elif row.not_captured:
                result_bg = skipped_bg
            else:
                result_bg = blank

            # 游標優先於「已拍」：退回到拍過的條目時要看得出游標在哪，
            # 否則使用者不知道下一張會覆蓋掉誰
            if i == cursor:
                item.setText(0, ">")
                mark = done if i in shots else accent
                fg, bg = accent, soft
            elif i in shots:
                item.setText(0, "•")
                mark, fg, bg = done, plain, result_bg
            elif i < cursor:
                item.setText(0, "-")
                mark, fg, bg = dim, dim, result_bg
            else:
                item.setText(0, "")
                mark, fg, bg = plain, plain, result_bg
            item.setForeground(0, mark)
            for column in range(1, self.lines.columnCount()):
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
                self.clear_sheet(name)

    def _line_menu(self, pos: QtCore.QPoint) -> None:
        item = self.lines.itemAt(pos)
        if item is None or self.project is None:
            return
        index = self.lines.indexOfTopLevelItem(item)
        line = self.expected[index] if index < len(self.expected) else None
        row = self.rows.get(index)
        captured = row.captured if row is not None else None
        expected_text = display_key(line.target_en) if line else ""
        actual_text = display_key(captured.body_text) if captured else ""

        menu = QtWidgets.QMenu(self)
        copies = {}
        for text, payload in (
            ("複製對話ID", line.dialogue_id if line else ""),
            ("複製譯文（正確答案）", expected_text),
            ("複製遊戲內文", actual_text),
            ("複製整列", "\t".join([
                line.dialogue_id if line else "", expected_text, actual_text,
                row.label if row else "", row.detail if row else ""])),
        ):
            action = menu.addAction(text)
            action.setEnabled(bool(payload.strip()))
            copies[action] = payload
        menu.addSeparator()

        open_shot = menu.addAction("開啟截圖")
        shot = None
        if captured is not None and captured.screenshot and self.store is not None:
            shot = self.store.dir / captured.screenshot
        open_shot.setEnabled(bool(shot and shot.exists()))

        clear = menu.addAction("清除這條的截圖")
        clear.setEnabled(index in self.shots)
        jump = menu.addAction("從這條開始")
        jump.setEnabled(self.bound is not None)

        chosen = menu.exec(self.lines.mapToGlobal(pos))
        if chosen in copies:
            QtWidgets.QApplication.clipboard().setText(copies[chosen])
            self.status.setText(f"已複製：{copies[chosen][:60]}")
        elif chosen is open_shot and shot:
            QtGui.QDesktopServices.openUrl(
                QtCore.QUrl.fromLocalFile(str(shot.resolve())))
        elif chosen is clear and index in self.shots:
            self.clear_entry(index)
        elif chosen is jump and self.bound is not None:
            self.bound.move_to(index)
            self._update_progress()

    # ---------- 清除截圖 ----------

    def clear_sheet(self, name: str) -> int:
        """刪掉整個頁簽的截圖。"""
        if self.project is None:
            return 0
        if name == self.sheet:
            self._close_store()
        removed = self.project.clear_sheet(name)
        if name == self.sheet:
            # 解析結果是從這些截圖來的，截圖沒了，判定就不該還留在表上
            self.rows = {}
            self.result = None
            self._clear_result_columns()
            self._refresh_filter_labels()
            self._set_filter(ALL)
        self._after_shots_changed(current=name == self.sheet)
        self.status.setText(f"已清除「{name}」的 {removed} 張截圖")
        return removed

    def clear_entry(self, index: int) -> None:
        """刪掉一條的截圖，退回「未截圖」。"""
        if self.project is None:
            return
        self.project.clear_entry(self.sheet, index)
        if self.bound is not None:
            self.bound.discard(index)
            self.bound.state.shots.pop(index, None)
        # 這一條的判定也跟著沒了，不然表上還留著一個沒有依據的結論
        self.rows.pop(index, None)
        self._clear_result_columns(index)
        self._refresh_filter_labels()
        self._after_shots_changed()
        self.status.setText(f"已清除第 {index + 1} 條的截圖")

    def _clear_result_columns(self, index: Optional[int] = None) -> None:
        rows = range(self.lines.topLevelItemCount()) if index is None else [index]
        for i in rows:
            item = self.lines.topLevelItem(i)
            if item is not None:
                item.setText(4, "")
                item.setText(5, "")

    def _after_shots_changed(self, current: bool = True) -> None:
        """截圖被刪掉之後，畫面上跟截圖有關的地方全部更新。

        集中在一個地方做。先前標記、頁簽數字、細節面板各自寫在不同的
        分支裡，於是刪整個頁簽時漏了細節面板、刪單條時漏了預覽 ——
        刪掉的截圖還留在右邊看得到。
        """
        if current:
            self._reload_shots()
            self._repaint_lines()
            self._show_detail(self.lines.currentItem())
            if self.bound is None:
                self.analyse_button.setEnabled(bool(self.shots))
        self._refresh_sheet_counts()

    def _close_store(self) -> None:
        """放掉 lines.jsonl 的檔案控制代碼。

        SessionStore 一建立就用附加模式開著這個檔，而 Windows 不讓人刪除
        開啟中的檔案 —— 清除整個頁簽時會在刪 lines.jsonl 那一步丟例外，
        整個處理從中間斷掉：截圖已經刪了，進度沒更新，畫面也沒重畫，
        所以綠點還留在原地。

        而且每切一次頁簽就新建一個 store，不關掉就是一路漏控制代碼。
        """
        if self.store is not None:
            try:
                self.store.close()
            except OSError:
                pass
            self.store = None

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
        self._fill_results(result)
        counts = result.summary()
        # 疑慮和未截圖分開講。未截圖是使用者自己跳過的，混在同一個數字裡
        # 會讓人以為遊戲有那麼多問題
        flagged = sum(1 for row in self.rows.values() if row.flagged)
        skipped = sum(1 for row in self.rows.values() if row.not_captured)
        self.status.setText(
            f"共 {len(result.expected)} 條，疑慮 {flagged} 筆，未截圖 {skipped} 條　"
            + "　".join(f"{CATEGORY_LABEL_ZH[c]} {counts[c.value]}"
                        for c in Category if counts.get(c.value)))
        from . import sound

        sound.play(self.settings.sound_on_finish)
        missing = self._unmapped_speakers()
        if missing:
            # 對照表沒有英文名就沒有正確答案，發話者那一項等於沒查 ——
            # 不講的話使用者會以為已經檢查過了
            self.status.setText(
                self.status.text()
                + f"　發話者未檢查（對照表缺 {len(missing)} 個英文名："
                + "、".join(missing[:4]) + ("…" if len(missing) > 4 else "") + "）")
        if self.settings.notify_on_finish:
            self._notify("解析完成",
                         f"{len(result.expected)} 條中有 {flagged} 筆疑慮"
                         + (f"，另有 {skipped} 條未截圖" if skipped else ""))

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

    def open_debug(self) -> None:
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
        self._stop_auto_record()
        self._stop_hotkeys()
        self._close_store()
        self.settings.save()
        super().closeEvent(event)
