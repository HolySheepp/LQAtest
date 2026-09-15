"""介面進入點。"""

from __future__ import annotations

import sys

from PySide6 import QtWidgets

from ..logging_setup import get, install_qt_handler, setup
from .main_window import MainWindow


def _claim_taskbar_identity() -> None:
    """告訴 Windows 這是獨立的應用程式。

    不設的話工作列會把我們歸在 python.exe 底下，顯示 Python 的圖示，
    而且和其他 Python 程式擠在同一個群組。必須在建立任何視窗之前呼叫。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Cela.LQAChecker")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    log = setup()
    install_qt_handler()
    log.info("介面啟動")
    try:
        _claim_taskbar_identity()
        app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
        app.setApplicationName("LQA Checker")
        window = MainWindow()
        window.show()
        code = app.exec()
        log.info("介面結束，代碼 %s", code)
        return code
    except Exception:
        log.exception("介面啟動失敗")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
