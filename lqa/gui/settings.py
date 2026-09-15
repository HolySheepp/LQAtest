"""介面設定的存取。

和 profile 分開：profile 是「怎麼抓畫面」（跟解析度綁在一起），
這裡是「介面長什麼樣、熱鍵是哪幾個」（跟使用者習慣綁在一起）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SETTINGS_PATH = Path("config/gui.json")

DEFAULT_HOTKEYS = {
    "shoot": "f9",      # 拍下目前這條
    "skip": "f8",       # 跳過這條（留空）
    "back": "f7",       # 游標退回一條，下一張會覆蓋
    "toggle": "f12",    # 開始／結束拍攝
    "clear": "f4",      # 刪掉目前這條的截圖，退回「未截圖」
}

HOTKEY_LABELS = {
    "shoot": "截圖",
    "skip": "跳過這條",
    "back": "退回一條",
    "clear": "清除這條截圖",
    "toggle": "開始／結束",
}


@dataclass
class GuiSettings:
    dark: bool = True
    accent: str = "blue"
    # 自訂副色（#rrggbb）。accent 設成 custom 時使用
    custom_accent: str = "#4f8cff"
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    script_path: str = ""
    profile_path: str = "config/profile.json"
    speakers_path: str = "config/speakers.csv"
    notify_on_finish: bool = True
    # 開發者模式：主視窗多出實驗中的自動錄製
    developer_mode: bool = False
    auto_poll_ms: int = 60

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "GuiSettings":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = set(cls.__dataclass_fields__)
        settings = cls(**{k: v for k, v in data.items() if k in known})
        # 舊設定檔可能缺新增的熱鍵
        settings.hotkeys = {**DEFAULT_HOTKEYS, **(settings.hotkeys or {})}
        return settings

    def save(self, path: Path = SETTINGS_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                        encoding="utf-8")
