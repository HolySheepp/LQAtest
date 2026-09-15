"""棘輪旋鈕。

沿用 AudioForge 的手感：垂直拖曳轉動，每個步進之間有「齒」——
指針在齒間會被張力拉住，越過門檻才跳到下一齒。
右鍵可以切換步進大小，雙擊可以直接輸入數值。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..logging_setup import get

log = get("gui.knob")

PX_PER_DETENT = 10          # 拖曳多少像素跨一齒
ANGLE_RANGE = 270           # 指針可轉的角度範圍（-135 ~ +135）
TENSION_RATIO = 0.34        # 齒間張力最多推進到一齒角度的幾成，必須小於 0.5


class Knob(QtWidgets.QWidget):
    valueChanged = QtCore.Signal(float)

    def __init__(
        self,
        label: str,
        value: float,
        minimum: float,
        maximum: float,
        step_options: list[float],
        digits: int = 0,
        unit: str = "",
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.label = label
        self._value = float(value)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.step_options = list(step_options)
        self.step = self.step_options[0]
        self.digits = digits
        self.unit = unit

        self._accent = QtGui.QColor("#4f8cff")
        self._track = QtGui.QColor("#333845")
        self._text = QtGui.QColor("#e8eaf0")
        self._dim = QtGui.QColor("#9aa0ad")

        self._drag_start: QtCore.QPoint | None = None
        self._drag_value = 0.0
        self._tension = 0.0          # 尚未跨齒的落後量，畫面上看得到指針被拉住

        self.setFixedSize(74, 92)
        self.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_step_menu)

    # --- 對外 ---

    def value(self) -> float:
        return self._value

    def setValue(self, value: float) -> None:
        clamped = max(self.minimum, min(self.maximum, float(value)))
        if abs(clamped - self._value) < 1e-9:
            return
        self._value = clamped
        self.update()
        self.valueChanged.emit(self._value)

    def set_colors(self, accent: str, track: str, text: str, dim: str) -> None:
        self._accent = QtGui.QColor(accent)
        self._track = QtGui.QColor(track)
        self._text = QtGui.QColor(text)
        self._dim = QtGui.QColor(dim)
        self.update()

    # --- 互動 ---

    def _show_step_menu(self, pos: QtCore.QPoint) -> None:
        if len(self.step_options) < 2:
            return
        menu = QtWidgets.QMenu(self)
        for option in self.step_options:
            action = menu.addAction(f"步進 {self._format(option)}")
            action.setCheckable(True)
            action.setChecked(abs(option - self.step) < 1e-9)
            action.triggered.connect(lambda _c, o=option: setattr(self, "step", o))
        menu.exec(self.mapToGlobal(pos))

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_value = self._value

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_start is None:
            return
        moved = self._drag_start.y() - event.position().toPoint().y()
        detents = moved / PX_PER_DETENT
        whole = int(detents)
        # 沒跨齒的部分變成張力，畫面上指針會被往前拉一點但還沒咬到下一齒
        self._tension = max(-1.0, min(1.0, detents - whole))
        self.setValue(self._drag_value + whole * self.step)
        self.update()

    def mouseReleaseEvent(self, _event: QtGui.QMouseEvent) -> None:
        self._drag_start = None
        self._tension = 0.0
        self.update()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        notches = event.angleDelta().y() / 120
        self.setValue(self._value + notches * self.step)

    def mouseDoubleClickEvent(self, _event: QtGui.QMouseEvent) -> None:
        text, ok = QtWidgets.QInputDialog.getText(
            self, self.label, f"輸入數值（{self._format(self.minimum)} ~ "
            f"{self._format(self.maximum)}）", text=self._format(self._value)
        )
        if ok:
            try:
                self.setValue(float(text))
            except ValueError:
                pass

    # --- 繪製 ---

    def _format(self, value: float) -> str:
        return f"{value:.{self.digits}f}" if self.digits else f"{value:.0f}"

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        """paintEvent 是虛擬函式，裡面丟例外不會往上傳，只會每次重繪都失敗一次。

        先前 QPen 用了不支援的 cap 關鍵字，結果六個旋鈕在每次重繪都各丟一次
        例外，介面看起來就是卡住。包起來確保單一繪製錯誤不會拖垮整個視窗。
        """
        try:
            self._paint()
        except Exception:
            log.exception("旋鈕繪製失敗")

    def _paint(self) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        radius = 22
        centre = QtCore.QPointF(self.width() / 2, 28)
        rect = QtCore.QRectF(centre.x() - radius, centre.y() - radius,
                             radius * 2, radius * 2)

        span = self.maximum - self.minimum
        ratio = (self._value - self.minimum) / span if span else 0.0
        tension_deg = self._tension * (ANGLE_RANGE * (self.step / span if span else 0)) * TENSION_RATIO

        # 底環。QPen 不吃 cap 關鍵字參數，要另外設 —— 寫成關鍵字的話
        # paintEvent 每次重繪都會丟例外，旋鈕根本畫不出來
        pen = QtGui.QPen(self._track, 5)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int((-135 - 90) * -16), int(-ANGLE_RANGE * 16))

        # 已填滿的部分
        pen.setColor(self._accent)
        painter.setPen(pen)
        painter.drawArc(rect, int((-135 - 90) * -16), int(-ANGLE_RANGE * ratio * 16))

        # 指針
        angle = -135 + ANGLE_RANGE * ratio + tension_deg
        transform = QtGui.QTransform().translate(centre.x(), centre.y()).rotate(angle)
        painter.save()
        painter.setTransform(transform, True)
        needle = QtGui.QPen(self._accent, 2.5)
        needle.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        painter.setPen(needle)
        painter.drawLine(QtCore.QPointF(0, -6), QtCore.QPointF(0, -radius + 5))
        painter.restore()

        # 數值與標籤。字型可能是以像素為單位，那時 pointSizeF() 會回 -1，
        # 直接加減就變成負數，Qt 會拒絕並每次重繪都警告一次。
        painter.setPen(self._text)
        font = painter.font()
        base = font.pointSizeF()
        if base <= 0:
            base = max(1.0, font.pixelSize() * 0.75)
        font.setPointSizeF(base + 0.5)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QtCore.QRectF(0, centre.y() + radius - 6, self.width(), 18),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self._format(self._value) + self.unit,
        )
        font.setBold(False)
        font.setPointSizeF(max(1.0, base - 1.0))
        painter.setFont(font)
        painter.setPen(self._dim)
        painter.drawText(
            QtCore.QRectF(0, centre.y() + radius + 11, self.width(), 16),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self.label,
        )
