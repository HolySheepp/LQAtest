"""把比對結果攤平成「每一列一個結果」。

比對輸出的是一串 Issue，但使用者看的是文本 —— 一條譯文旁邊就該有
遊戲內文和判定結果。這裡負責把 Issue 掛回它所屬的那一列。

一列可能有不只一筆 Issue：發話者錯誤是獨立追加的，
所以「一致 + 發話者錯誤」是正常組合。

純資料處理，不碰 Qt，這樣列的篩選與跳轉邏輯測得動。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import CATEGORY_LABEL_ZH, Category, Issue

# 篩選模式
ALL = "all"
FLAGGED = "flagged"
NOT_CAPTURED = "not_captured"

FILTER_LABELS = [
    (ALL, "顯示全部"),
    (FLAGGED, "只顯示疑慮條目"),
    (NOT_CAPTURED, "只顯示未截圖條目"),
]


@dataclass
class RowResult:
    """一列文本的比對結果。"""

    issues: list[Issue] = field(default_factory=list)

    @property
    def not_captured(self) -> bool:
        return any(i.category is Category.NOT_CAPTURED for i in self.issues)

    @property
    def flagged(self) -> bool:
        """有疑慮。

        「未截圖」不算 —— 那是使用者自己跳過的，不是遊戲的問題，
        混進疑慮裡會把真正要看的東西淹掉。
        """
        return any(i.category not in (Category.PASS, Category.NOT_CAPTURED)
                   for i in self.issues)

    @property
    def categories(self) -> list[Category]:
        """要顯示的分類。只有在沒有其他分類時才顯示「一致」。"""
        others = [i.category for i in self.issues if i.category is not Category.PASS]
        picked = others or [i.category for i in self.issues]
        seen: list[Category] = []
        for category in picked:
            if category not in seen:
                seen.append(category)
        return seen

    @property
    def label(self) -> str:
        return "／".join(CATEGORY_LABEL_ZH[c] for c in self.categories)

    @property
    def captured(self):
        """畫面文字。挑第一筆有抓到東西的，發話者那筆也可能帶著。"""
        for issue in self.issues:
            if issue.captured is not None:
                return issue.captured
        return None

    @property
    def detail(self) -> str:
        parts = [i.detail for i in self.issues
                 if i.detail and i.category is not Category.PASS]
        return "\n".join(dict.fromkeys(parts))

    @property
    def similarity(self) -> float:
        values = [i.similarity for i in self.issues if i.similarity]
        return max(values) if values else 0.0


def rows_from_result(result) -> dict[int, RowResult]:
    """Issue -> 列號。

    用物件identity而不是對話ID：同一個頁簽裡ID可能重複，
    而 compare() 拿到的就是 result.expected 裡的那些物件本身。
    """
    index_of = {id(line): i for i, line in enumerate(result.expected)}
    rows: dict[int, RowResult] = {}
    for issue in result.issues:
        if issue.expected is None:
            continue        # 文本中無此句，沒有列可以掛，留在總結裡
        index = index_of.get(id(issue.expected))
        if index is None:
            continue
        rows.setdefault(index, RowResult()).issues.append(issue)
    return rows


def is_visible(row: RowResult | None, mode: str) -> bool:
    if mode == ALL:
        return True
    if row is None:
        return False        # 還沒解析的列在篩選模式下沒有結論可看
    if mode == FLAGGED:
        return row.flagged
    return row.not_captured


def next_flagged(current: int, count: int, rows: dict[int, RowResult],
                 step: int) -> int:
    """往 step 方向找下一個有疑慮的列。找不到就停在原地，不要繞回去。"""
    if current < 0:
        current = count if step < 0 else -1
    index = current + step
    while 0 <= index < count:
        row = rows.get(index)
        if row is not None and row.flagged:
            return index
        index += step
    return current
