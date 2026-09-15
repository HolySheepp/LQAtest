"""框選範圍的視窗。

原本借用 OpenCV 的 selectROI，有兩個問題：

  取消不掉   按視窗的叉叉不會回傳，selectROI 只會把視窗再開一次，
             使用者陷在裡面出不來，只能整個關掉軟體
  標題亂碼   OpenCV 的視窗標題走系統 ANSI 編碼，中文一律變亂碼

所以改成自己畫。順帶還能把目前的範圍先畫出來當起點，
不必每次都從零開始拖。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

Rect = tuple[int, int, int, int]

MIN_SIZE = 4        # 小於這個就當成誤點，不是框選


def fit_scale(image: QtCore.QSize, view: QtCore.QSize) -> float:
    """把整張圖塞進畫布要縮多少。只縮不放，放大只會讓框選更難對準。"""
    if image.isEmpty() or view.isEmpty():
        return 1.0
    return min(1.0, min(view.width() / image.width(),
                        view.height() / image.height()))


def view_to_image(rect: QtCore.QRect, offset: QtCore.QPoint, scale: float,
                  bounds: QtCore.QSize) -> Rect:
    """畫布上拖出來的矩形換算回原圖座標。

    要夾回圖片範圍：使用者常常從圖外面開始拖，或拖出邊界，
    直接換算會得到負數或超出畫面的 ROI，存進 profile 之後截圖就會炸。

    邊界用 x + width 而不是 QRect.right()。Qt 的 right() 是最後一個像素
    （left + width - 1），拿它當右界算出來的寬會少一。
    """
    scale = scale or 1.0
    left = round((rect.left() - offset.x()) / scale)
    top = round((rect.top() - offset.y()) / scale)
    right = round((rect.left() + rect.width() - offset.x()) / scale)
    bottom = round((rect.top() + rect.height() - offset.y()) / scale)
    left, right = sorted((left, right))
    top, bottom = sorted((top, bottom))
    left = max(0, min(left, bounds.width()))
    top = max(0, min(top, bounds.height()))
    right = max(0, min(right, bounds.width()))
    bottom = max(0, min(bottom, bounds.height()))
    return (left, top, right - left, bottom - top)


def image_to_view(rect: Rect, offset: QtCore.QPoint, scale: float) -> QtCore.QRect:
    x, y, w, h = rect
    return QtCore.QRect(round(x * scale) + offset.x(),
                        round(y * scale) + offset.y(),
                        round(w * scale), round(h * scale))


class Canvas(QtWidgets.QWidget):
    """畫面加上一個可以拖的方框。"""

    changed = QtCore.Signal()

    def __init__(self, pixmap: QtGui.QPixmap, initial: Rect | None):
        super().__init__()
        self.source = pixmap
        self.rect_in_image: Rect | None = initial
        self._drag_from: QtCore.QPoint | None = None
        self._drag_to: QtCore.QPoint | None = None
        self.setMinimumSize(320, 240)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    # --- 座標 ---

    def scale(self) -> float:
        return fit_scale(self.source.size(), self.size())

    def offset(self) -> QtCore.QPoint:
        scale = self.scale()
        return QtCore.QPoint(
            round((self.width() - self.source.width() * scale) / 2),
            round((self.height() - self.source.height() * scale) / 2))

    # --- 互動 ---

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_from = event.position().toPoint()
            self._drag_to = self._drag_from
            self.update()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_from is not None:
            self._drag_to = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, _event: QtGui.QMouseEvent) -> None:
        if self._drag_from is None or self._drag_to is None:
            return
        box = view_to_image(QtCore.QRect(self._drag_from, self._drag_to).normalized(),
                            self.offset(), self.scale(), self.source.size())
        self._drag_from = self._drag_to = None
        # 太小就當成誤點，保留原本的框，不要把它清掉
        if box[2] >= MIN_SIZE and box[3] >= MIN_SIZE:
            self.rect_in_image = box
            self.changed.emit()
        self.update()

    # --- 繪製 ---

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        scale = self.scale()
        offset = self.offset()
        target = QtCore.QRect(offset.x(), offset.y(),
                              round(self.source.width() * scale),
                              round(self.source.height() * scale))
        painter.drawPixmap(target, self.source)

        box = None
        if self._drag_from is not None and self._drag_to is not None:
            box = QtCore.QRect(self._drag_from, self._drag_to).normalized()
        elif self.rect_in_image:
            box = image_to_view(self.rect_in_image, offset, scale)
        if box is None:
            return
        # 框外壓暗，一眼看得出留下的是哪一塊
        shade = QtGui.QColor(0, 0, 0, 110)
        for part in (QtCore.QRect(target.left(), target.top(), target.width(),
                                  box.top() - target.top()),
                     QtCore.QRect(target.left(), box.bottom() + 1, target.width(),
                                  target.bottom() - box.bottom()),
                     QtCore.QRect(target.left(), box.top(),
                                  box.left() - target.left(), box.height()),
                     QtCore.QRect(box.right() + 1, box.top(),
                                  target.right() - box.right(), box.height())):
            if part.isValid():
                painter.fillRect(part.intersected(target), shade)
        pen = QtGui.QPen(QtGui.QColor("#42d65a"), 2)
        painter.setPen(pen)
        painter.drawRect(box)


class RoiPicker(QtWidgets.QDialog):
    """框選一個範圍。回傳 None 代表使用者取消。"""

    def __init__(self, pixmap: QtGui.QPixmap, label: str, initial: Rect | None,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"框選{label}")
        self.setModal(True)
        self.resize(min(1100, pixmap.width() + 80),
                    min(820, pixmap.height() + 150))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QtWidgets.QLabel(
            f"在畫面上拖出「{label}」的範圍。拖錯可以重拖，"
            "按取消或 Esc 就不會動到原本的設定。")
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        layout.addWidget(hint)

        self.canvas = Canvas(pixmap, initial)
        self.canvas.changed.connect(self._refresh)
        layout.addWidget(self.canvas, 1)

        self.readout = QtWidgets.QLabel()
        self.readout.setProperty("role", "dim")
        layout.addWidget(self.readout)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("使用這個範圍")
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._refresh()

    def _refresh(self) -> None:
        box = self.canvas.rect_in_image
        self.readout.setText(
            f"x={box[0]}  y={box[1]}  寬={box[2]}  高={box[3]}" if box
            else "還沒框選")
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok).setEnabled(bool(box))

    def result_rect(self) -> Rect | None:
        return self.canvas.rect_in_image
