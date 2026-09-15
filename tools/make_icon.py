"""產生應用程式圖示。

不外連圖檔，直接用 QPainter 畫再組成 ICO ——
圖示要跟著主題色走，用程式產生比放一張死圖好維護。

圖案：對白框加一個打勾，正好是這個工具在做的事（對答案）。
16 像素下仍要認得出來，所以形狀盡量少、線條盡量粗。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = "#4f8cff"
DARK = "#1e2128"


def draw(size: int) -> QtGui.QImage:
    image = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

    s = size / 256.0          # 以 256 設計，再等比縮放
    # 底：圓角方塊
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor(DARK))
    painter.drawRoundedRect(QtCore.QRectF(0, 0, size, size), 56 * s, 56 * s)

    # 對白框
    painter.setBrush(QtGui.QColor(ACCENT))
    bubble = QtCore.QRectF(40 * s, 56 * s, 176 * s, 118 * s)
    painter.drawRoundedRect(bubble, 22 * s, 22 * s)
    tail = QtGui.QPolygonF([
        QtCore.QPointF(78 * s, 174 * s),
        QtCore.QPointF(78 * s, 216 * s),
        QtCore.QPointF(122 * s, 174 * s),
    ])
    painter.drawPolygon(tail)

    # 打勾。線條粗一點，16 像素下才看得出來
    pen = QtGui.QPen(QtGui.QColor("#ffffff"), 22 * s)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QtGui.QPolygonF([
        QtCore.QPointF(78 * s, 116 * s),
        QtCore.QPointF(112 * s, 148 * s),
        QtCore.QPointF(178 * s, 82 * s),
    ]))
    painter.end()
    return image


def png_bytes(image: QtGui.QImage) -> bytes:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def build_ico(path: Path) -> None:
    """ICO 容器：Vista 之後每個項目可以直接放 PNG，不必存成 BMP。"""
    payloads = [png_bytes(draw(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = len(header) + 16 * len(SIZES)
    entries, data = b"", b""
    for size, payload in zip(SIZES, payloads):
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,      # 256 要寫成 0
            size if size < 256 else 0,
            0, 0, 1, 32, len(payload), offset,
        )
        data += payload
        offset += len(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + data)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)          # QPainter 需要
    target = Path("lqa/gui/assets/icon.ico")
    build_ico(target)
    draw(256).save("lqa/gui/assets/icon.png", "PNG")
    print(f"已產生 {target}（{target.stat().st_size / 1024:.1f} KB，"
          f"{len(SIZES)} 種尺寸）")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
