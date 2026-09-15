"""記錄與當機現場。

一般的例外會被 Python 攔下來，但堆疊溢位、存取違規這類硬當機不會 ——
行程直接消失，什麼都不留。所以這裡做三件事：

  1. 檔案記錄，每寫一行就 flush。當機時最後一行就是死在哪裡的線索
  2. faulthandler 把致命訊號當下的 Python 呼叫堆疊寫進檔案，
     這是硬當機唯一能拿到的現場
  3. 接管 Qt 自己的訊息，它的警告常常先於當機出現

記錄檔放在 logs/，每次啟動保留上一份。
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_PATH = LOG_DIR / "lqa.log"
CRASH_PATH = LOG_DIR / "crash.log"

_crash_file = None       # faulthandler 需要這個檔案在整個行程期間都開著


class _FlushingHandler(RotatingFileHandler):
    """每寫一行就落地。當機前的最後一行才是最重要的那一行。"""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.flush()
            os.fsync(self.stream.fileno())
        except (OSError, ValueError, AttributeError):
            pass


def setup(level: int = logging.DEBUG) -> logging.Logger:
    global _crash_file

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("lqa")
    if root.handlers:
        return root

    root.setLevel(level)
    handler = _FlushingHandler(LOG_PATH, maxBytes=2_000_000, backupCount=2,
                               encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"))
    root.addHandler(handler)

    _crash_file = CRASH_PATH.open("a", encoding="utf-8")
    _crash_file.write(f"\n===== 啟動 {os.getpid()} =====\n")
    _crash_file.flush()
    faulthandler.enable(file=_crash_file, all_threads=True)

    sys.excepthook = _log_uncaught
    root.info("記錄開始，pid=%s，crash dump -> %s", os.getpid(), CRASH_PATH)
    return root


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:
    logging.getLogger("lqa").critical(
        "未攔截的例外", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def install_qt_handler() -> None:
    """把 Qt 自己的訊息導進同一份記錄。它的警告常常先於當機出現。"""
    from PySide6 import QtCore

    logger = logging.getLogger("lqa.qt")
    levels = {
        QtCore.QtMsgType.QtDebugMsg: logging.DEBUG,
        QtCore.QtMsgType.QtInfoMsg: logging.INFO,
        QtCore.QtMsgType.QtWarningMsg: logging.WARNING,
        QtCore.QtMsgType.QtCriticalMsg: logging.ERROR,
        QtCore.QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, context, message) -> None:
        logger.log(levels.get(mode, logging.INFO), "%s", message)

    QtCore.qInstallMessageHandler(handler)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"lqa.{name}")
