"""介面設定的存取。

和 profile 分開：profile 是「怎麼抓畫面」（跟解析度綁在一起），
這裡是「介面長什麼樣、熱鍵是哪幾個」（跟使用者習慣綁在一起）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..paths import config_path

SETTINGS_PATH = config_path("gui.json")

DEFAULT_HOTKEYS = {
    "shoot": "f9",      # 拍下目前這條
    "skip": "f8",       # 跳過這條（留空）
    "back": "f7",       # 游標退回一條，下一張會覆蓋
    "toggle": "f12",    # 開始／結束拍攝
    "clear": "f4",      # 刪掉目前這條的截圖，退回「未截圖」
    # 檢視結果時把目前這條標成一致。只有視窗在前景時作用 ——
    # 全域監聽 Enter 等於在任何程式裡按 Enter 都會改判定
    "mark_pass": "enter",
}

HOTKEY_LABELS = {
    "shoot": "截圖",
    "skip": "下一條",
    "back": "上一條",
    "clear": "清除這條截圖",
    "toggle": "開始／結束",
    "mark_pass": "標為一致（僅視窗內）",
}


@dataclass
class GuiSettings:
    dark: bool = True
    accent: str = "blue"
    # 自訂副色（#rrggbb）。accent 設成 custom 時使用
    custom_accent: str = "#4f8cff"
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    script_path: str = ""
    profile_path: str = str(config_path("profile.json"))
    speakers_path: str = str(config_path("speakers.csv"))
    notify_on_finish: bool = True
    # 解析完成時播的音效，就是 assets/sounds 裡的檔名。空字串代表不播
    sound_on_finish: str = "啵_Bop"
    # 對照表裡有兩種譯法的名字。解析時這些句子標成「發話者需確認」，
    # 交給人判斷 —— 軟體沒有立場替使用者決定哪個譯名才對
    speaker_ask: list[str] = field(default_factory=list)
    # 開發者模式：主視窗多出實驗中的自動錄製
    developer_mode: bool = False
    auto_poll_ms: int = 60
    # 自動錄製：對白框區域的亮度中位數超過這個值就當成過場動畫，暫停偵測。
    # 0 代表不檢查。對白框是純黑的才適用，數值看調試視窗的「亮度中位數」
    auto_cutscene_median: int = 0

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
