"""介面進入點。"""

from __future__ import annotations

import sys

from PySide6 import QtWidgets

from .main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("LQA Checker")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
