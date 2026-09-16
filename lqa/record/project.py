"""專案：一份翻譯文本的所有截圖與進度。

    projects/<文本檔名>/
        project.json          文本路徑與各頁簽的進度
        <頁簽>/
            meta.json         給離線辨識用的 profile 與模式
            shots/00042.png   檔名就是條目索引
            lines.jsonl       辨識結果

每個頁簽是一個固定目錄（不帶時間戳），所以切換頁簽或關掉軟體再開，
都找得回同一批截圖。先前截圖只存在記憶體裡而且目錄帶時間戳，
切個頁簽進度就沒了。

state.json 另外記下每個索引當時對應的對話ID。文本後來被改動時
索引會位移，有這份紀錄才知道舊截圖對不對得上。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..config import Profile
from .store import SessionStore

from ..paths import project_root

PROJECT_ROOT = project_root()


def safe_name(text: str) -> str:
    """把檔名/頁簽名變成安全的資料夾名稱。"""
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", text).strip(" .")
    return cleaned[:80] or "untitled"


@dataclass
class SheetProgress:
    """一個頁簽的進度。"""

    sheet: str
    shots: dict[int, str]              # 條目索引 -> 相對路徑
    dialogue_ids: dict[int, str]       # 條目索引 -> 當時的對話ID
    analysed: bool = False

    @property
    def taken(self) -> int:
        return len(self.shots)


class Project:
    def __init__(self, script_path: str, root: Path = PROJECT_ROOT):
        self.script_path = script_path
        self.dir = root / safe_name(Path(script_path).stem)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "project.json"
        self._state = self._load()

    # --- 狀態 ---

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"script": self.script_path, "sheets": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"script": self.script_path, "sheets": {}}
        data.setdefault("sheets", {})
        return data

    def save(self) -> None:
        self._state["script"] = self.script_path
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def sheet_dir(self, sheet: str) -> Path:
        return self.dir / safe_name(sheet)

    def store(self, sheet: str) -> SessionStore:
        return SessionStore(self.dir, safe_name(sheet), timestamped=False)

    # --- 進度 ---

    def progress(self, sheet: str) -> SheetProgress:
        """從磁碟重讀這個頁簽的進度。

        以實際存在的檔案為準而不是只看 state.json —— 使用者可能手動
        刪過檔案，以紀錄為準的話介面會顯示不存在的截圖。
        """
        entry = self._state["sheets"].get(sheet, {})
        recorded = {int(k): v for k, v in entry.get("dialogue_ids", {}).items()}
        shots: dict[int, str] = {}
        shots_dir = self.sheet_dir(sheet) / "shots"
        if shots_dir.is_dir():
            for path in shots_dir.glob("*.png"):
                try:
                    shots[int(path.stem)] = f"shots/{path.name}"
                except ValueError:
                    continue
        return SheetProgress(sheet=sheet, shots=shots, dialogue_ids=recorded,
                             analysed=bool(entry.get("analysed")))

    def record_shot(self, sheet: str, index: int, dialogue_id: str) -> None:
        entry = self._state["sheets"].setdefault(sheet, {"dialogue_ids": {}})
        entry.setdefault("dialogue_ids", {})[str(index)] = dialogue_id
        entry["analysed"] = False
        self.save()

    def forget_shot(self, sheet: str, index: int) -> None:
        entry = self._state["sheets"].get(sheet)
        if entry:
            entry.get("dialogue_ids", {}).pop(str(index), None)
            entry["analysed"] = False
            self.save()

    def mark_analysed(self, sheet: str) -> None:
        self._state["sheets"].setdefault(sheet, {"dialogue_ids": {}})["analysed"] = True
        self.save()

    # --- 清除 ---

    def clear_entry(self, sheet: str, index: int) -> bool:
        """刪掉一個條目的截圖，回到「未截圖」。"""
        path = self.sheet_dir(sheet) / "shots" / f"{index:05d}.png"
        existed = path.exists()
        path.unlink(missing_ok=True)
        self.forget_shot(sheet, index)
        return existed

    # --- 人工判定 ---

    def verdicts(self, sheet: str) -> dict[int, str]:
        """使用者手動改過的判定。條目索引 -> 分類代號。"""
        entry = self._state["sheets"].get(sheet, {})
        stored = entry.get("verdicts", {})
        return {int(k): v["category"] for k, v in stored.items()
                if isinstance(v, dict) and v.get("category")}

    def set_verdict(self, sheet: str, index: int, category: str | None,
                    dialogue_id: str = "") -> None:
        """記下（或取消）一條的人工判定。

        連對話ID一起存：文本中間插入或刪除句子時索引會整段位移，
        有這個才判斷得出這筆判定是不是已經指到別條去了。
        """
        entry = self._state["sheets"].setdefault(sheet, {"dialogue_ids": {}})
        verdicts = entry.setdefault("verdicts", {})
        if category:
            verdicts[str(index)] = {"category": category,
                                    "dialogue_id": dialogue_id}
        else:
            verdicts.pop(str(index), None)
        self.save()

    def clear_verdicts(self, sheet: str) -> None:
        entry = self._state["sheets"].get(sheet)
        if entry:
            entry.pop("verdicts", None)
            self.save()

    def clear_sheet(self, sheet: str) -> int:
        """刪掉整個頁簽的截圖與辨識結果。回傳刪掉幾張。"""
        folder = self.sheet_dir(sheet)
        removed = 0
        shots_dir = folder / "shots"
        if shots_dir.is_dir():
            for path in shots_dir.glob("*.png"):
                path.unlink(missing_ok=True)
                removed += 1
        try:
            (folder / "lines.jsonl").unlink(missing_ok=True)
            (folder / "ocr_cache.json").unlink(missing_ok=True)
        except OSError:
            # Windows 不讓人刪除開啟中的檔案。刪不掉就算了，不能讓整個
            # 清除卡在這一步 —— 截圖已經刪掉了，狀態一定要跟著更新，
            # 否則進度紀錄會和磁碟上的東西對不起來。
            # 反正下次解析會整份覆寫過去
            pass
        self._state["sheets"].pop(sheet, None)
        self.save()
        return removed

    # --- 完整性 ---

    def stale_entries(self, sheet: str, expected) -> list[int]:
        """文本改動後，索引對應的對話ID 已經不一樣的那些條目。

        截圖是按索引存的，文本中間插入或刪除句子就會整段位移，
        舊截圖看起來還在卻對到了別條。這裡把對不上的挑出來。
        """
        progress = self.progress(sheet)
        stale: list[int] = []
        for index in sorted(progress.shots):
            was = progress.dialogue_ids.get(index)
            if was is None:
                continue
            if index >= len(expected) or expected[index].dialogue_id != was:
                stale.append(index)
        return stale

    def write_sheet_meta(self, sheet: str, profile: Profile,
                         sheets: list[str]) -> None:
        store = self.store(sheet)
        store.write_meta({
            "mode": "bound",
            "profile": profile.to_dict(),
            "script": self.script_path,
            "sheets": sheets,
        })
        store.close()

    def save_shot(self, sheet: str, index: int, frame: np.ndarray,
                  dialogue_id: str) -> str:
        store = self.store(sheet)
        try:
            rel = store.save_shot(frame, index)
        finally:
            store.close()
        self.record_shot(sheet, index, dialogue_id)
        return rel
