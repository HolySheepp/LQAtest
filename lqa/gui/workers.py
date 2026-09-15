"""背景工作。

介面不能在 OCR 或比對時卡住，所以這些都丟到 QThread。
解析期間整個行程會降到低優先權 —— 解析通常和遊玩同時進行，
讓出排程給模擬器比早幾秒跑完重要。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6 import QtCore

from ..config import Profile
from ..hotkey import KeyWatcher
from ..model import CapturedLine


class HotkeyWatcher(QtCore.QThread):
    """全域熱鍵輪詢。

    在自己的執行緒輪詢 GetAsyncKeyState，這樣焦點在模擬器上也收得到，
    使用者不必為了按鍵切回介面。輪詢按鍵狀態只要幾微秒，
    和擷取畫面完全不是同一個量級。
    """

    pressed = QtCore.Signal(str)

    def __init__(self, keys: dict[str, str], parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._names = {name: key for name, key in keys.items()}
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            watcher = KeyWatcher(self._names.values())
        except ValueError:
            return
        reverse = {key: name for name, key in self._names.items()}
        while not self._stop:
            for key in watcher.pressed():
                action = reverse.get(key)
                if action:
                    self.pressed.emit(action)
            self.msleep(15)


class FrameGrabber(QtCore.QThread):
    """在自己的執行緒抓畫面。

    **絕對不能在 GUI 執行緒直接抓。** 視窗被蓋住時混合後端會走 PrintWindow，
    而 PrintWindow 是同步送訊息給目標視窗並等它回應 —— 目標是正在算繪遊戲的
    模擬器，這一等就把整個介面凍住（畫面顯示「沒有回應」）。
    更糟的是等待期間 Windows 會把訊息回送給我們自己的視窗，
    造成重入，最後直接閃退。

    擷取後端在這個執行緒內建立並使用：mss 的實例本來就必須待在
    建立它的執行緒。
    """

    grabbed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, profile: Profile, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self.profile = profile
        self._want = False
        self._stop = False

    def request(self) -> None:
        self._want = True

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from ..capture.mss_backend import open_capture

        capture = None
        try:
            while not self._stop:
                if not self._want:
                    self.msleep(20)
                    continue
                self._want = False
                try:
                    if capture is None:
                        capture = open_capture(
                            self.profile.window_title, self.profile.capture_region,
                            self.profile.capture_backend, roi=self.profile.body_roi)
                    frame = capture.grab()
                    reason = capture.unavailable()
                    if reason:
                        self.failed.emit(reason)
                    else:
                        self.grabbed.emit(frame)
                except Exception as exc:
                    if capture is not None:
                        try:
                            capture.close()
                        except Exception:
                            pass
                        capture = None
                    self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass


class AnalyseWorker(QtCore.QThread):
    """辨識 + 比對。"""

    progress = QtCore.Signal(int, int, str)
    finished_ok = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        session_dir: Path,
        profile: Profile,
        script_path: str,
        sheets: list[str],
        speakers_path: str,
        parent: QtCore.QObject | None = None,
    ):
        super().__init__(parent)
        self.session_dir = session_dir
        self.profile = profile
        self.script_path = script_path
        self.sheets = sheets
        self.speakers_path = speakers_path

    def run(self) -> None:
        try:
            from ..compare.classify import compare
            from ..compare.script_loader import load_script, load_speaker_map
            from ..priority import low_priority
            from ..record.reader import read_session

            def on_progress(done: int, total: int, line: CapturedLine) -> None:
                self.progress.emit(done, total, line.body_text)

            with low_priority(self.profile.ocr.low_priority):
                captured = read_session(
                    self.session_dir, profile=self.profile, on_progress=on_progress
                )
                speakers = (
                    load_speaker_map(self.speakers_path)
                    if self.speakers_path and Path(self.speakers_path).exists()
                    else {}
                )
                expected = load_script(self.script_path, speakers, sheets=self.sheets)
                result = compare(expected, captured)
            self.finished_ok.emit(result)
        except Exception as exc:  # 背景執行緒的例外要送回介面，不能讓它靜靜死掉
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ScriptLoadWorker(QtCore.QThread):
    """解析翻譯文本並列出頁簽。大檔案讀起來會卡，所以丟背景。"""

    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, path: str, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self.path = path

    def run(self) -> None:
        try:
            from ..compare.script_loader import list_dialogue_sheets

            self.loaded.emit(list_dialogue_sheets(self.path))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
