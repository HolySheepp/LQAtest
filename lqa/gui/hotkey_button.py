"""按一下就錄製快捷鍵的按鈕。

從下拉選單裡挑按鍵很難用：使用者得先知道我們怎麼稱呼那個鍵，
再從幾十個名字裡找出來。按一下、然後按你想用的那個鍵，才是直覺的做法。

偵測用的是 GetAsyncKeyState 輪詢，和正式的熱鍵監聽同一套 ——
所以滑鼠側鍵這些也錄得到，而且錄到的名稱一定和監聽端認得的一致。
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..hotkey import VK_CODES, KeyWatcher

UNBOUND = ""            # 不綁定
POLL_MS = 20
TIMEOUT_MS = 8000       # 按下去卻不按鍵時自己退出，不要卡在錄製狀態


def label_for(key: str) -> str:
    return key.upper() if key else "未設定"


class HotkeyButton(QtWidgets.QPushButton):
    """顯示目前的快捷鍵；按一下開始錄製，下一個按鍵就是新的設定。"""

    captured = QtCore.Signal(str)      # 新的按鍵名，空字串代表不綁定

    def __init__(self, key: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self._watcher: KeyWatcher | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._deadline = QtCore.QElapsedTimer()
        self.setCheckable(True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.clicked.connect(self._on_click)
        self.set_key(key)

    def set_key(self, key: str) -> None:
        self.key = key
        self.setText(label_for(key))
        self.setToolTip("按一下，再按下要用的鍵。Esc 代表不綁定")

    # --- 錄製 ---

    def _on_click(self) -> None:
        if self._timer.isActive():
            self._stop()
        else:
            self._start()

    def _start(self) -> None:
        self._watcher = KeyWatcher(VK_CODES)
        # 先讀一次把目前按著的鍵吃掉，否則使用者手還壓著某個鍵就會被錄進去
        self._watcher.pressed()
        self._deadline.start()
        self._timer.start()
        self.setChecked(True)
        self.setText("按下要用的鍵…")

    def _stop(self) -> None:
        self._timer.stop()
        self._watcher = None
        self.setChecked(False)
        self.set_key(self.key)

    def _poll(self) -> None:
        if self._watcher is None:
            return
        if self._deadline.hasExpired(TIMEOUT_MS):
            self._stop()
            return
        fired = self._watcher.pressed()
        if not fired:
            return
        key = UNBOUND if "esc" in fired else fired[0]
        self._stop()
        self.set_key(key)
        self.captured.emit(key)
