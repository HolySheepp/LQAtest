"""介面進入點。"""

from __future__ import annotations

import sys

from PySide6 import QtWidgets

from ..logging_setup import get, install_qt_handler, setup
from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    log = setup()
    install_qt_handler()
    log.info("介面啟動")
    try:
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
