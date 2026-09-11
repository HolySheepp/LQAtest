"""錄製結果的存放。

一次錄製 = 一個 session 目錄：

    sessions/20260911_143000_ch1/
        meta.json     錄製資訊（profile、時間、備註）
        lines.jsonl   每行一句 CapturedLine
        shots/        每句一張截圖

刻意用 jsonl 而不是一次寫出：錄到一半當掉也不會整批遺失。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

from ..model import CapturedLine


class SessionStore:
    def __init__(self, root: str | Path, name: Optional[str] = None):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = f"{stamp}_{name}" if name else stamp
        self.dir = Path(root) / folder
        self.shots_dir = self.dir / "shots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.lines_path = self.dir / "lines.jsonl"
        self._fh = self.lines_path.open("a", encoding="utf-8")
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def write_meta(self, meta: dict[str, Any]) -> None:
        meta = {**meta, "created_at": time.time()}
        (self.dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_screenshot(self, frame: np.ndarray, seq: int, quality: int = 85) -> str:
        """存截圖，回傳相對於 session 目錄的路徑。

        走 imageio 而不是 cv2.imwrite：session 目錄名稱可能含中文
        （--name 是使用者自己取的），cv2 在 Windows 上會寫出亂碼檔名。
        """
        from ..imageio import imwrite  # noqa: PLC0415

        rel = f"shots/{seq:05d}.jpg"
        try:
            imwrite(self.dir / rel, frame, quality=quality)
        except (ImportError, OSError):
            return ""
        return rel

    def append(self, line: CapturedLine) -> None:
        self._fh.write(json.dumps(line.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        self._count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def iter_session(session_dir: str | Path) -> Iterator[CapturedLine]:
    """逐行讀回一次錄製的結果。"""
    path = Path(session_dir)
    if path.is_dir():
        path = path / "lines.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"找不到錄製結果：{path}")
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if raw:
                yield CapturedLine.from_dict(json.loads(raw))


def load_session(session_dir: str | Path) -> list[CapturedLine]:
    lines = list(iter_session(session_dir))
    lines.sort(key=lambda c: c.seq)
    return lines


def load_sessions(session_dirs: list[str | Path]) -> list[CapturedLine]:
    """把多次錄製串成一條序列（例如一章分好幾次錄）。seq 會重新編號。"""
    merged: list[CapturedLine] = []
    for d in session_dirs:
        for line in load_session(d):
            line.seq = len(merged)
            merged.append(line)
    return merged
