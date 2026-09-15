"""手動截圖：由使用者決定什麼時候這句話顯示完了。

為什麼不自動判斷：
    遊戲沒有給出「這句顯示完了」的任何訊號，只能從像素反推。
    試過三種判準（等畫面靜止、看變動比例、看筆畫消失量），
    每一種都在某類句子上失敗 —— 短句沒有靜止期、刪節號的停頓
    偽裝成結束、背景變動混進訊號。反推必然有模糊地帶。
    使用者的眼睛有這個訊號，程式沒有。

附帶好處是不用持續輪詢擷取畫面。輪詢按鍵狀態只要幾微秒，
所以模擬器完全不受影響 —— 先前 PrintWindow 輪詢會讓它的 CPU
從 0.4% 跳到 5.4%，那個成本在這裡直接歸零。

截圖當下不做 OCR，只存原圖。辨識與比對留到離線階段，
這樣按鍵到存檔只要幾十毫秒，不會打斷遊玩節奏，
而且參數調整後可以重跑辨識，不必重玩一次。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from ..capture.base import CaptureBackend
from ..capture.mss_backend import open_capture
from ..config import Profile
from ..hotkey import KeyWatcher
from .store import SessionStore

ShotCallback = Callable[[int, str], None]


class Shooter:
    def __init__(
        self,
        profile: Profile,
        store: SessionStore,
        capture: Optional[CaptureBackend] = None,
        shoot_key: str = "f9",
        undo_key: str = "f10",
        poll_interval_ms: int = 15,
        on_shot: Optional[ShotCallback] = None,
    ):
        profile.validate()
        self.profile = profile
        self.store = store
        self.shoot_key = shoot_key
        self.undo_key = undo_key
        self.poll_interval = poll_interval_ms / 1000.0
        self.on_shot = on_shot
        self._capture = capture or open_capture(
            profile.window_title, profile.capture_region, profile.capture_backend,
            roi=profile.watch_roi(),
        )
        self._shots: list[str] = []
        self._last_warning: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self._shots)

    def shoot(self) -> Optional[str]:
        frame = self._capture.grab()
        reason = self._capture.unavailable()
        if reason:
            print(f"[略過] {reason}")
            return None
        path = self.store.save_shot(frame, len(self._shots))
        self._shots.append(path)
        if self.on_shot:
            self.on_shot(len(self._shots), path)
        return path

    def undo(self) -> Optional[str]:
        """取消最後一張。按錯或手滑時不必整輪重來。"""
        if not self._shots:
            return None
        path = self._shots.pop()
        self.store.remove_shot(path)
        return path

    def run(self, stop_event: Optional[threading.Event] = None) -> int:
        watcher = KeyWatcher([self.shoot_key, self.undo_key])
        self.store.write_meta(
            {
                "mode": "manual",
                "profile": self.profile.to_dict(),
                "capture_region": list(self._capture.region()),
                "shoot_key": self.shoot_key,
            }
        )
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                for key in watcher.pressed():
                    if key == self.shoot_key:
                        self.shoot()
                    elif key == self.undo_key:
                        removed = self.undo()
                        if removed:
                            print(f"  已取消第 {len(self._shots) + 1} 張")
                        else:
                            print("  沒有可取消的截圖")
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self._capture.close()
        return len(self._shots)
