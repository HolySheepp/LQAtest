"""按鍵名稱轉成 Qt 的快捷鍵。

拍攝用的快捷鍵是全域的（在模擬器裡按也收得到），靠 GetAsyncKeyState 輪詢。
但檢視結果時用的按鍵**不能**這樣做：預設是 Enter，全域監聽等於你在任何
程式裡按 Enter 都會把目前那條標成一致。所以這一類改用 Qt 的快捷鍵，
只有視窗在前景時才作用。

兩邊共用同一組按鍵名稱，設定介面才不必分成兩套。
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui

# 只列得出 Qt 對應鍵的才轉得過去。滑鼠鍵沒有 QKeySequence，回 None
_SPECIAL = {
    "enter": QtCore.Qt.Key.Key_Return,
    "space": QtCore.Qt.Key.Key_Space,
    "esc": QtCore.Qt.Key.Key_Escape,
    "backspace": QtCore.Qt.Key.Key_Backspace,
    "tab": QtCore.Qt.Key.Key_Tab,
    "insert": QtCore.Qt.Key.Key_Insert,
    "delete": QtCore.Qt.Key.Key_Delete,
    "home": QtCore.Qt.Key.Key_Home,
    "end": QtCore.Qt.Key.Key_End,
    "pageup": QtCore.Qt.Key.Key_PageUp,
    "pagedown": QtCore.Qt.Key.Key_PageDown,
    "up": QtCore.Qt.Key.Key_Up,
    "down": QtCore.Qt.Key.Key_Down,
    "left": QtCore.Qt.Key.Key_Left,
    "right": QtCore.Qt.Key.Key_Right,
}


def qt_key(name: str) -> QtGui.QKeySequence | None:
    """轉得過去就回 QKeySequence，轉不過去回 None。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in _SPECIAL:
        return QtGui.QKeySequence(_SPECIAL[key])
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return QtGui.QKeySequence(
            getattr(QtCore.Qt.Key, f"Key_F{int(key[1:])}"))
    if len(key) == 1 and key.isalnum():
        return QtGui.QKeySequence(key.upper())
    return None       # 滑鼠鍵之類的，Qt 快捷鍵做不到
