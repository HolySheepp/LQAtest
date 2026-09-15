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
WM_NCCALCSIZE = 0x0083
BORDER = 6          # 邊緣拉伸的感應寬度

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000

SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020

MONITOR_DEFAULTTONEAREST = 2

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


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
        for name, text, tip, slot in (
            ("min", "─", "最小化", self._minimise),
            ("max", "□", "最大化", self._toggle_max),
            ("close", "✕", "關閉", window.close),
        ):
            button = QtWidgets.QPushButton(text)
            button.setObjectName(f"win{name.capitalize()}")
            button.setToolTip(tip)
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
        else:
            self.window_ref.showMaximized()
        self.sync_max_button()

    def sync_max_button(self) -> None:
        """按鈕圖示跟著實際狀態走。

        最大化不只來自這顆按鈕 —— 雙擊標題列、拖到螢幕頂端、貼邊分割
        都由系統直接處理，不會經過這裡，所以要從視窗狀態反推。
        """
        maximised = self.window_ref.isMaximized()
        button = self.buttons["max"]
        button.setText("❐" if maximised else "□")
        button.setToolTip("還原" if maximised else "最大化")

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        # Windows 上這條路走不到（標題列回報成非工作區，系統自己處理），
        # 留著是為了其他平台
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._toggle_max()


def restore_native_frame(hwnd: int) -> bool:
    """把 Qt 拿掉的視窗樣式加回去。

    這是「自繪標題列後無法拉伸、最大化後拖不下來」的真正原因：
    FramelessWindowHint 會把 WS_THICKFRAME 一起拿掉，而
    DefWindowProc 的 SC_SIZE / SC_MOVE 是看樣式決定要不要動作的 ——
    命中測試回報 HTLEFT 它也不理你。樣式加回來之後，拉伸、貼邊分割、
    拖到頂端最大化、最大化後往下拖還原全部都回來了。

    有了樣式就會有非工作區（那圈看不見的邊框），所以另外靠
    WM_NCCALCSIZE 把它吃掉，畫面上才不會多一道框。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long

    handle = wintypes.HWND(hwnd)
    style = user32.GetWindowLongW(handle, GWL_STYLE)
    wanted = style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
    if wanted != style:
        user32.SetWindowLongW(handle, GWL_STYLE, wanted)
        user32.SetWindowPos(handle, None, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                            | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    return True


def round_corners(hwnd: int) -> bool:
    """Windows 11 的圓角。

    交給 DWM 而不是自己遮罩：系統畫的圓角有反鋸齒也有陰影，
    自己用 QRegion 切出來的邊緣是鋸齒狀的。舊版 Windows 不認這個屬性，
    呼叫會失敗但不會出事，就維持直角。
    """
    if not _IS_WINDOWS or not hwnd:
        return False
    try:
        preference = ctypes.c_int(DWMWCP_ROUND)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference), ctypes.sizeof(preference))
        return result == 0
    except OSError:
        return False


def clamp_to_work_area(window: tuple[int, int, int, int],
                       work: tuple[int, int, int, int]
                       ) -> tuple[int, int, int, int]:
    """最大化時把工作區夾回螢幕的可用範圍。

    最大化的視窗矩形有時候會比螢幕工作區大一圈 —— 系統假設那一圈會被
    非工作區吃掉。我們把非工作區吃掉了，所以得自己夾回來，
    否則視窗溢出螢幕，底部和兩側被切掉。

    但那一圈不是每次都有（Qt 會自己算最大化尺寸），所以用「取交集」而不是
    「固定往內縮一個邊框寬」—— 沒溢出時就什麼都不動，不會在邊上留一道縫。
    """
    return (max(window[0], work[0]), max(window[1], work[1]),
            min(window[2], work[2]), min(window[3], work[3]))


class FramelessMixin:
    """把視窗改成自繪外框，同時保住系統的拉伸與貼邊。

    兩件事缺一不可：
      - 樣式要留著 WS_THICKFRAME，系統才肯做拉伸與移動
      - 命中測試要回報邊緣與標題列，系統才知道該拉哪裡、該拖哪裡
    """

    def setup_frameless(self, title: str) -> TitleBar:
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        bar = TitleBar(self, title)
        self._title_bar = bar
        return bar

    def title_bar(self) -> TitleBar | None:
        return getattr(self, "_title_bar", None)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # 要等原生視窗真的存在才改得動樣式，show 之前 winId 可能還沒配
        if not getattr(self, "_frame_ready", False):
            self._frame_ready = True
            handle = int(self.winId())
            restore_native_frame(handle)
            round_corners(handle)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            bar = self.title_bar()
            if bar is not None:
                bar.sync_max_button()

    def nativeEvent(self, event_type, message):  # type: ignore[override]
        if not _IS_WINDOWS or event_type != "windows_generic_MSG":
            return False, 0
        msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents

        if msg.message == WM_NCCALCSIZE and msg.wParam:
            return True, self._eat_non_client_area(msg.hWnd, msg.lParam)
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

    def _eat_non_client_area(self, hwnd, lparam: int) -> int:
        """工作區 = 整個視窗，這樣加回來的邊框不會佔掉畫面。

        視窗代號一定要用訊息自己帶的那個，**不能呼叫 winId()** ——
        winId() 會讓 Qt 去確認原生視窗，那又會同步送出一則 WM_NCCALCSIZE，
        於是這個函式再被叫一次，無限遞迴到堆疊爆掉（存取違規閃退）。
        """
        user32 = ctypes.windll.user32
        # 問系統而不是問 Qt：這則訊息是在最大化的過程中送來的，
        # 此時 Qt 那邊的視窗狀態還沒更新
        if not user32.IsZoomed(hwnd):
            return 0
        work = _work_area(hwnd)
        if work is None:
            return 0
        params = ctypes.cast(ctypes.c_void_p(lparam),
                             ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
        rect = params.rgrc[0]
        left, top, right, bottom = clamp_to_work_area(
            (rect.left, rect.top, rect.right, rect.bottom), work)
        rect.left, rect.top, rect.right, rect.bottom = left, top, right, bottom
        return 0


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


def _work_area(hwnd) -> tuple[int, int, int, int] | None:
    """視窗所在螢幕的可用範圍（扣掉工作列）。"""
    user32 = ctypes.windll.user32
    # HMONITOR 是指標，64 位元下 ctypes 預設的 c_int 回傳值會把它截半，
    # 拿去查資訊就會失敗
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(ctypes.c_void_p(monitor), ctypes.byref(info)):
        return None
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


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
