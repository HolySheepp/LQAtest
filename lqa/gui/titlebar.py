"""自繪標題列。

去掉系統外框自己畫，才能和整體配色一致。但去掉外框的同時也會失去
Windows 的貼邊分割、拖到頂端最大化、以及從邊緣拉伸 —— 那些都是
系統在「非工作區」上做的事。所以這裡接管 WM_NCHITTEST，
把邊緣與標題列重新宣告成非工作區，系統就會照常提供那些行為。

只在 Windows 生效；其他平台退回系統外框。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

_IS_WINDOWS = sys.platform == "win32"

# WM_NCHITTEST 的回傳值：哪個部位被滑鼠碰到
HTCLIENT = 1
HTCAPTION = 2
HTLEFT, HTRIGHT = 10, 11
HTTOP, HTTOPLEFT, HTTOPRIGHT = 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

WM_NCHITTEST = 0x0084
BORDER = 6          # 邊緣拉伸的感應寬度


class TitleBar(QtWidgets.QWidget):
    def __init__(self, window: QtWidgets.QWidget, title: str):
        super().__init__(window)
        self.window_ref = window
        self.setFixedHeight(38)
        self.setObjectName("titleBar")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        self.icon = QtWidgets.QLabel()
        self.icon.setFixedSize(18, 18)
        self.icon.setScaledContents(True)
        layout.addWidget(self.icon)

        self.title = QtWidgets.QLabel(title)
        self.title.setObjectName("titleText")
        layout.addWidget(self.title)
        layout.addStretch(1)

        self.buttons: dict[str, QtWidgets.QPushButton] = {}
        for name, text, slot in (
            ("min", "─", self._minimise),
            ("max", "□", self._toggle_max),
            ("close", "✕", window.close),
        ):
            button = QtWidgets.QPushButton(text)
            button.setObjectName(f"win{name.capitalize()}")
            button.setFixedSize(46, 38)
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            layout.addWidget(button)
            self.buttons[name] = button

    def set_icon(self, icon: QtGui.QIcon) -> None:
        self.icon.setPixmap(icon.pixmap(18, 18))

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def _minimise(self) -> None:
        self.window_ref.showMinimized()

    def _toggle_max(self) -> None:
        if self.window_ref.isMaximized():
            self.window_ref.showNormal()
            self.buttons["max"].setText("□")
        else:
            self.window_ref.showMaximized()
            self.buttons["max"].setText("❐")

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._toggle_max()


class FramelessMixin:
    """把視窗改成自繪外框，同時保住系統的拉伸與貼邊。

    關鍵在 nativeEvent：Qt 只給我們工作區，系統以為整個視窗都是工作區，
    於是不再提供拉伸與拖曳。把邊緣回報成 HTLEFT 之類、標題列回報成
    HTCAPTION，系統就會照常處理 —— 包括貼邊分割和拖到頂端最大化。
    """

    def setup_frameless(self, title: str) -> TitleBar:
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        bar = TitleBar(self, title)
        self._title_bar = bar
        return bar

    def title_bar(self) -> TitleBar | None:
        return getattr(self, "_title_bar", None)

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if not _IS_WINDOWS or event_type != "windows_generic_MSG":
            return False, 0
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if msg.message != WM_NCHITTEST:
            return False, 0

        # lParam 的低高 16 位元是螢幕座標，可能為負，要當成有號數
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        local = self.mapFromGlobal(QtCore.QPoint(x, y))

        bar = self.title_bar()
        bar_height = bar.height() if bar is not None and bar.isVisible() else 0
        buttons = ([b.geometry().translated(bar.pos()) for b in bar.buttons.values()]
                   if bar_height else [])
        result = hit_test(local, self.width(), self.height(),
                          self.isMaximized(), bar_height, buttons)
        if result is None:
            return False, 0
        return True, result


def hit_test(point: QtCore.QPoint, width: int, height: int, maximised: bool,
             bar_height: int, buttons: list[QtCore.QRect]) -> int | None:
    """回報滑鼠碰到的是哪個部位。None 代表交給 Qt 自己處理。

    抽成純函式而不是塞在 nativeEvent 裡：這是座標判斷，容易寫錯，
    而且不需要真的視窗就測得動。
    """
    if not maximised:
        left = point.x() < BORDER
        right = point.x() > width - BORDER
        top = point.y() < BORDER
        bottom = point.y() > height - BORDER
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM

    if bar_height and point.y() < bar_height:
        # 按鈕上不能回報成標題列，否則點不到
        if any(rect.contains(point) for rect in buttons):
            return HTCLIENT
        return HTCAPTION
    return None


def title_bar_qss(palette) -> str:
    from .theme import mix

    hover = mix(palette.text, palette.surface, 0.12)
    return f"""
#titleBar {{ background: {palette.surface}; border-bottom: 1px solid {palette.border}; }}
#titleText {{ color: {palette.text_dim}; font-size: 12px; }}
#titleBar QPushButton {{
    background: transparent;
    border: none;
    border-radius: 0;
    color: {palette.text_dim};
    font-size: 13px;
    padding: 0;
}}
#titleBar QPushButton:hover {{ background: {hover}; color: {palette.text}; }}
#winClose:hover {{ background: {palette.danger}; color: #ffffff; }}
"""
