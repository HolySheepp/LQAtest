"""發話者對照表的檢查視窗。

指定對照表之後跳出來，把兩種問題攤開：

  完全重複      中英文都一樣的列。不影響結果，但大表裡會越積越多，
                之後要人工核對時很礙事
  中英不一致    同一個中文名填了兩種英文名。只會採用第一個，
                另一半會安靜地永遠對不上 —— 看起來像遊戲有問題，
                其實是表填錯了

處理方式讓使用者選，因為軟體沒有立場替他決定哪個譯名才對。
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtWidgets

from ..compare.script_loader import SpeakerMap, read_speaker_map, write_speaker_edits
from ..logging_setup import get

log = get("gui.speakers")


def summary(table: SpeakerMap) -> str:
    """一句話講完這份表的狀況。"""
    parts = [f"{len(table.names)} 個名字"]
    if table.duplicates:
        parts.append(f"{len(table.duplicates)} 組完全重複")
    if table.conflicts:
        parts.append(f"{len(table.conflicts)} 個中英不一致")
    if not table.duplicates and not table.conflicts:
        parts.append("沒有發現問題")
    return "　".join(parts)


class ConflictDialog(QtWidgets.QDialog):
    """一項一項處理中英不一致。"""

    def __init__(self, table: SpeakerMap, parent=None):
        super().__init__(parent)
        self.table = table
        self.setWindowTitle("修正中英不一致")
        self.setMinimumWidth(520)

        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(
            f"會修改：{table.path}\n\n"
            "每個名字選一個要留下的英文譯名，或直接輸入正確的。"
            "選「刪掉這個名字」會把它的所有列都移除。")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self.choices: dict[str, QtWidgets.QComboBox] = {}
        form = QtWidgets.QFormLayout()
        for zh, names in table.conflicts.items():
            box = QtWidgets.QComboBox()
            box.setEditable(True)           # 兩個都不對時可以自己打
            for name in names:
                box.addItem(name)
            box.addItem("（刪掉這個名字）")
            self.choices[zh] = box
            form.addRow(zh, box)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("套用並寫回檔案")
        buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def edits(self) -> tuple[set[int], dict[int, str]]:
        """換算成「刪哪幾列、哪幾列要改成什麼」。"""
        delete: set[int] = set()
        updates: dict[int, str] = {}
        for zh, box in self.choices.items():
            rows = [e.row for e in self.table.entries if e.zh == zh]
            if not rows:
                continue
            chosen = box.currentText().strip()
            if chosen == "（刪掉這個名字）" or not chosen:
                delete.update(rows)
                continue
            # 留第一列並改成選定的譯名，其餘刪掉
            updates[rows[0]] = chosen
            delete.update(rows[1:])
        return delete, updates


class SpeakerTableDialog(QtWidgets.QDialog):
    def __init__(self, path: str, settings, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.settings = settings
        self.setWindowTitle("發話者對照表檢查")
        self.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        self.heading = QtWidgets.QLabel()
        self.heading.setProperty("role", "title")
        layout.addWidget(self.heading)
        self.file_label = QtWidgets.QLabel(str(self.path))
        self.file_label.setProperty("role", "dim")
        self.file_label.setWordWrap(True)
        layout.addWidget(self.file_label)

        layout.addWidget(self._section("完全重複的項目"))
        self.dupe_list = QtWidgets.QPlainTextEdit()
        self.dupe_list.setReadOnly(True)
        self.dupe_list.setMaximumHeight(110)
        layout.addWidget(self.dupe_list)
        self.trim_button = QtWidgets.QPushButton("修剪重複項")
        self.trim_button.setToolTip("每組只保留一列，其餘刪掉。會先備份原檔")
        self.trim_button.clicked.connect(self._trim)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.trim_button)
        layout.addLayout(row)

        layout.addWidget(self._section("中文相同、英文不同"))
        self.conflict_list = QtWidgets.QPlainTextEdit()
        self.conflict_list.setReadOnly(True)
        self.conflict_list.setMaximumHeight(110)
        layout.addWidget(self.conflict_list)

        self.fix_button = QtWidgets.QPushButton("修正中英不一致")
        self.fix_button.setToolTip("一項一項指定要留下的譯名，或刪掉。會先備份原檔")
        self.fix_button.clicked.connect(self._fix)
        self.ask_button = QtWidgets.QPushButton("解析時詢問")
        self.ask_button.setCheckable(True)
        self.ask_button.setToolTip(
            "不改檔案。解析時把這些名字的句子標成「發話者需確認」，由你人工判斷")
        self.ask_button.clicked.connect(self._toggle_ask)
        row2 = QtWidgets.QHBoxLayout()
        row2.addStretch(1)
        row2.addWidget(self.fix_button)
        row2.addWidget(self.ask_button)
        layout.addLayout(row2)

        self.status = QtWidgets.QLabel("")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        close = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.button(
            QtWidgets.QDialogButtonBox.StandardButton.Close).setText("關閉")
        close.rejected.connect(self.reject)
        layout.addWidget(close)

        self.reload()

    def _section(self, text: str) -> QtWidgets.QLabel:
        widget = QtWidgets.QLabel(text)
        widget.setProperty("role", "section")
        return widget

    # --- 內容 ---

    def reload(self) -> None:
        self.table = read_speaker_map(self.path)
        self.heading.setText(summary(self.table))

        lines = []
        for key, rows in self.table.duplicates.items():
            zh, en = key.split("\t")
            lines.append(f"{zh} → {en}（多 {len(rows)} 列）")
        self.dupe_list.setPlainText(
            "\n".join(lines) if lines else "（沒有重複）")
        self.trim_button.setEnabled(bool(self.table.duplicates))

        conflicts = [f"{zh}：{' / '.join(names)}"
                     for zh, names in self.table.conflicts.items()]
        self.conflict_list.setPlainText(
            "\n".join(conflicts) if conflicts else "（沒有不一致）")
        self.fix_button.setEnabled(bool(self.table.conflicts))
        self.ask_button.setEnabled(bool(self.table.conflicts))
        asked = set(getattr(self.settings, "speaker_ask", []) or [])
        self.ask_button.setChecked(bool(conflicts) and
                                   asked >= set(self.table.conflicts))

    # --- 動作 ---

    def _trim(self) -> None:
        rows = {row for rows in self.table.duplicates.values() for row in rows}
        if not rows or not self._confirm(
                f"要刪掉 {len(rows)} 列重複的嗎？\n\n會修改：{self.path}"):
            return
        self._apply(rows, {}, f"已修剪 {len(rows)} 列重複")

    def _fix(self) -> None:
        dialog = ConflictDialog(self.table, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        delete, updates = dialog.edits()
        self._apply(delete, updates,
                    f"已處理 {len(self.table.conflicts)} 個不一致")

    def _apply(self, delete: set[int], updates: dict[int, str],
               message: str) -> None:
        try:
            backup = write_speaker_edits(self.path, delete, updates)
        except Exception as exc:       # 寫檔失敗要講清楚，不能安靜地當作成功
            log.exception("寫回對照表失敗")
            QtWidgets.QMessageBox.warning(
                self, "改不動對照表", f"{type(exc).__name__}: {exc}")
            return
        self.reload()
        self.status.setText(f"{message}。原檔已備份到 {backup.name}")

    def _toggle_ask(self) -> None:
        names = sorted(self.table.conflicts)
        if self.ask_button.isChecked():
            self.settings.speaker_ask = names
            self.status.setText(
                f"解析時會把這 {len(names)} 個名字的句子標成「發話者需確認」")
        else:
            self.settings.speaker_ask = []
            self.status.setText("解析時不再特別詢問")
        self.settings.save()

    def _confirm(self, message: str) -> bool:
        return QtWidgets.QMessageBox.question(
            self, "確認", message,
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No
        ) == QtWidgets.QMessageBox.StandardButton.Yes
