"""框選「對白在不在」的判定範圍。

自動錄製要分得出「現在是對白」還是「現在是過場動畫」。做法是看形狀：

    全黑區    對白框的底。挑不會有字的地方（文字上下的留白、左右邊緣）
    非全黑區  對白框以外的地方。用來擋掉整個畫面淡黑的轉場 ——
              那種轉場整塊都是黑的，光看對白框的底分不出來

兩邊同時成立、而且連續成立一小段時間，才算對白。

框幾個都可以。全黑區多框幾塊比較保險：過場的畫面剛好在某一塊是黑的
並不奇怪，但同時在好幾塊都黑就很難了。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..config import GateConfig, Rect
from ..detect.gate import dialogue_showing, region_darkness
from .roi_picker import RoiPicker

BLACK = "black"
LIT = "lit"


class GateDialog(QtWidgets.QDialog):
    def __init__(self, profile, frame, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.frame = frame
        self.setWindowTitle("對白判定範圍")
        self.setMinimumWidth(560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QtWidgets.QLabel(
            "自動錄製靠這些範圍分辨「對白」和「過場動畫」。\n\n"
            "全黑區：對白框的底，挑不會有字的地方。框不在的時候這裡會露出"
            "畫面內容，就不再是純黑。\n"
            "非全黑區：對白框以外的地方。整個畫面淡黑的轉場會讓這裡也變黑，"
            "靠它把「黑畫面」和「黑底對話框」分開。\n\n"
            "多框幾塊比較保險。下面的數字是各範圍目前的亮度，"
            "全黑區要小於等於容許值，非全黑區要大於。")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self.listing = QtWidgets.QTreeWidget()
        self.listing.setHeaderLabels(["類型", "範圍", "目前亮度"])
        self.listing.setRootIsDecorated(False)
        self.listing.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.listing, 1)

        buttons = QtWidgets.QHBoxLayout()
        add_black = QtWidgets.QPushButton("新增全黑區")
        add_black.clicked.connect(lambda: self._add(BLACK))
        add_lit = QtWidgets.QPushButton("新增非全黑區")
        add_lit.clicked.connect(lambda: self._add(LIT))
        remove = QtWidgets.QPushButton("刪除選取")
        remove.clicked.connect(self._remove)
        buttons.addWidget(add_black)
        buttons.addWidget(add_lit)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        knobs = QtWidgets.QHBoxLayout()
        knobs.addWidget(QtWidgets.QLabel("容許亮度"))
        self.tolerance = QtWidgets.QSpinBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setValue(profile.gate.tolerance)
        self.tolerance.setToolTip("多暗才算全黑。純黑是 0，壓縮會讓它不是剛好 0")
        self.tolerance.valueChanged.connect(self._on_tolerance)
        knobs.addWidget(self.tolerance)
        knobs.addSpacing(16)
        knobs.addWidget(QtWidgets.QLabel("持續時間"))
        self.hold = QtWidgets.QSpinBox()
        self.hold.setRange(0, 3000)
        self.hold.setSingleStep(50)
        self.hold.setSuffix(" ms")
        self.hold.setValue(profile.gate.hold_ms)
        self.hold.setToolTip("條件要連續成立這麼久才算對白，用來濾掉轉場途中的瞬間")
        self.hold.valueChanged.connect(self._on_hold)
        knobs.addWidget(self.hold)
        knobs.addStretch(1)
        layout.addLayout(knobs)

        self.verdict = QtWidgets.QLabel()
        self.verdict.setWordWrap(True)
        layout.addWidget(self.verdict)

        box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("套用")
        box.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.refresh()

    # --- 內容 ---

    def _rois(self, kind: str) -> list[Rect]:
        gate = self.profile.gate
        return gate.black_rois if kind == BLACK else gate.lit_rois

    def refresh(self) -> None:
        gate = self.profile.gate
        self.listing.clear()
        for kind, label in ((BLACK, "全黑區"), (LIT, "非全黑區")):
            for roi in self._rois(kind):
                value = (region_darkness(self.frame, roi)
                         if self.frame is not None else None)
                ok = (value is None
                      or (value <= gate.tolerance if kind == BLACK
                          else value > gate.tolerance))
                item = QtWidgets.QTreeWidgetItem([
                    label,
                    f"x={roi[0]} y={roi[1]} 寬={roi[2]} 高={roi[3]}",
                    "（沒有畫面）" if value is None
                    else f"{value}{'' if ok else '　不符合'}",
                ])
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, (kind, roi))
                self.listing.addTopLevelItem(item)
        self._refresh_verdict()

    def _refresh_verdict(self) -> None:
        if self.frame is None:
            self.verdict.setText("沒有畫面可以驗算，請先在調試視窗按「重新抓取」")
            return
        if not self.profile.gate.configured:
            self.verdict.setText("尚未設定全黑區，自動錄製會退回用亮度中位數判斷")
            return
        showing = dialogue_showing(self.frame, self.profile.gate)
        self.verdict.setText(
            "目前這一幀判定為：" + ("對白" if showing else "過場（會暫停偵測）"))

    # --- 動作 ---

    def _add(self, kind: str) -> None:
        if self.frame is None:
            self.verdict.setText("沒有畫面可以框，請先在調試視窗按「重新抓取」")
            return
        from .debug_window import to_pixmap

        label = "全黑區（對白框的底，不要框到字）" if kind == BLACK \
            else "非全黑區（對白框以外）"
        picker = RoiPicker(to_pixmap(self.frame), label, None, self)
        if picker.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        roi = picker.result_rect()
        if roi:
            self._rois(kind).append(roi)
            self.refresh()

    def _remove(self) -> None:
        item = self.listing.currentItem()
        if item is None:
            return
        kind, roi = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        rois = self._rois(kind)
        if roi in rois:
            rois.remove(roi)
        self.refresh()

    def _on_tolerance(self, value: int) -> None:
        self.profile.gate.tolerance = value
        self.refresh()

    def _on_hold(self, value: int) -> None:
        self.profile.gate.hold_ms = value
