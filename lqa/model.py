"""核心資料結構。

兩個階段共用：
  - ExpectedLine：從翻譯文本（Excel/CSV）讀進來的「正確答案」
  - CapturedLine：錄製階段從遊戲畫面 OCR 出來的「實際內容」
  - Issue：比對階段產出的問題紀錄
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Category(str, Enum):
    """比對結果分類。

    PASS 以外的每一項都會進報告。
    """

    PASS = "PASS"                      # 一致
    UNTRANSLATED = "UNTRANSLATED"      # 未翻譯 / 未套用（畫面出現中文）
    TRUNCATED = "TRUNCATED"            # 超框（畫面文字是譯文的前綴且明顯較短）
    MISMATCH = "MISMATCH"              # 不一致
    ORDER = "ORDER"                    # 順序不一致
    MISSING = "MISSING"                # 遊戲中沒這句
    NOT_CAPTURED = "NOT_CAPTURED"      # 使用者沒有截到這條（跳過或漏拍）
    SPEAKER = "SPEAKER"                # 發話者錯誤
    EXTRA = "EXTRA"                    # 畫面出現、但文本中找不到對應


CATEGORY_LABEL_ZH = {
    Category.PASS: "一致",
    Category.UNTRANSLATED: "未翻譯/未套用",
    Category.TRUNCATED: "超框",
    Category.MISMATCH: "不一致",
    Category.ORDER: "順序不一致",
    Category.MISSING: "遊戲中沒這句",
    Category.NOT_CAPTURED: "未截圖",
    Category.SPEAKER: "發話者錯誤",
    Category.EXTRA: "文本中無此句",
}


@dataclass
class ExpectedLine:
    """翻譯文本中的一列（正確答案）。"""

    order: int                 # 期望播放順序，以檔案列順序為準（不是 ID 大小）
    dialogue_id: str           # 對話ID，例如 80208001
    speaker_zh: str = ""       # 名字欄（中文）
    speaker_en: str = ""       # 由 speaker map 解析出的英文發話者名
    source_zh: str = ""        # 文字對話（中文原文）
    target_en: str = ""        # 英文翻譯（比對的正確答案）
    note: str = ""             # 備註（不需翻譯，不參與比對）
    sheet: str = ""            # 來源工作表名稱，例如 AVG1
    sheet_row: int = 0         # 原始檔案列號，方便回查

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapturedLine:
    """錄製階段抓到的一句遊戲畫面文字。"""

    seq: int                   # 擷取順序，從 0 開始
    timestamp: float           # epoch 秒
    # 這張截圖對應翻譯文本的第幾條。介面版拍攝時游標已經綁死了對應關係，
    # 所以比對不必靠序列對齊猜。-1 代表未綁定（命令列的自由拍攝模式）。
    expected_index: int = -1
    body_text: str = ""        # 對白框 OCR 結果
    speaker_text: str = ""     # 姓名框 OCR 結果
    screenshot: str = ""       # 截圖相對路徑
    layout: str = ""           # 這張用的是哪一套版面（normal / npc），空字串代表舊資料
    body_conf: float = 0.0     # OCR 平均信心值
    stable_ms: int = 0         # 保留欄位，舊 session 相容用
    samples: int = 0           # 這句被取樣幾次。太少代表可能沒抓到完整狀態
    still_growing: bool = False  # 直到換句前文字都還在增加，八成沒顯示完
    touches_bottom: bool = False   # 文字 bbox 是否貼齊對白框下緣（超框輔助訊號）
    touches_right: bool = False    # 文字 bbox 是否貼齊對白框右緣

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CapturedLine":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Issue:
    """一筆比對結果。category == PASS 的也會產生，報告時可過濾。"""

    category: Category
    expected: Optional[ExpectedLine] = None
    captured: Optional[CapturedLine] = None
    similarity: float = 0.0
    detail: str = ""                        # 人類可讀的說明
    expected_order: Optional[int] = None    # 期望出現在第幾句
    actual_order: Optional[int] = None      # 實際出現在第幾句
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def dialogue_id(self) -> str:
        return self.expected.dialogue_id if self.expected else ""
