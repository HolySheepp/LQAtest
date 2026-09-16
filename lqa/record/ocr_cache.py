"""辨識結果快取。

同一張截圖、同一組取字參數，辨識結果一定一樣。但每按一次「開始解析」
都會把整批重跑一次 —— 實測 76 張要 30 秒，而使用者調完一條判定、
改過對照表、或只是想再看一次，往往就會再按一次。

所以把每張圖的辨識結果記下來，下次直接拿。只要有一點對不上就重跑：

  取字參數變了   ROI、遮罩門檻、OCR 設定任何一項不同，結果就可能不同
  截圖被換掉     以修改時間加大小判斷。重拍同一條會蓋掉原檔

快取壞掉或讀不到一律當成沒有快取，重跑一次就好 —— 它是加速用的，
不是正確性的一部分，絕不能因為它讓結果變錯。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from ..config import Profile
from ..logging_setup import get
from ..model import CapturedLine

log = get("ocr.cache")

CACHE_NAME = "ocr_cache.json"
VERSION = 1


def cache_key(profile: Profile) -> str:
    """辨識結果取決於哪些設定。這些一變，舊的結果就不能用了。"""
    relevant: dict[str, Any] = {
        "version": VERSION,
        "rois": [profile.body_roi, profile.speaker_roi,
                 profile.npc_body_roi, profile.npc_speaker_roi],
        "masks": [asdict(profile.mask_for(region))
                  for region in ("body", "speaker", "npc_body", "npc_speaker")],
        "ocr": asdict(profile.ocr),
    }
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _stamp(path: Path) -> str:
    stat = path.stat()
    return f"{int(stat.st_mtime)}:{stat.st_size}"


class OcrCache:
    """一個頁簽的辨識結果快取。"""

    def __init__(self, session_dir: Path, key: str):
        self.path = session_dir / CACHE_NAME
        self.key = key
        self.entries: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.info("快取讀不到，這次重新辨識")
            return
        if data.get("key") != self.key:
            log.info("取字參數變了，快取作廢")
            return
        self.entries = data.get("shots", {})

    def get(self, shot: Path) -> Optional[CapturedLine]:
        entry = self.entries.get(shot.name)
        if not entry:
            return None
        try:
            if entry.get("stamp") != _stamp(shot):
                return None            # 這張重拍過
            line = CapturedLine.from_dict(entry["line"])
        except (OSError, KeyError, TypeError):
            return None
        self.hits += 1
        return line

    def put(self, shot: Path, line: CapturedLine) -> None:
        try:
            self.entries[shot.name] = {"stamp": _stamp(shot),
                                       "line": line.to_dict()}
        except OSError:
            pass

    def save(self, keep: set[str]) -> None:
        """寫回，順便丟掉已經不存在的截圖，免得檔案越長越大。"""
        self.entries = {name: entry for name, entry in self.entries.items()
                        if name in keep}
        try:
            self.path.write_text(
                json.dumps({"key": self.key, "shots": self.entries},
                           ensure_ascii=False),
                encoding="utf-8")
        except OSError as exc:
            log.warning("快取寫不進去：%s", exc)
