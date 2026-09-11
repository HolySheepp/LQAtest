"""讀取翻譯文本（正確答案）。

支援 .xlsx / .csv / .tsv。Google Sheet 可直接下載成 xlsx，或複製成 tsv 貼成檔案。

表格特性（依實際專案檔）：
  - 標題不一定在第 1 列，要自動找
  - 「場景 / 出場人物」這類說明列沒有對話ID，跳過
  - 對話ID 不等於播放順序，**列順序才是期望順序**
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..model import ExpectedLine

# 標題別名。比對時會先去掉空白與括號內容。
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "dialogue_id": ("對話id", "對白id", "id", "對話編號", "編號"),
    "speaker_zh": ("名字", "發話者", "角色", "說話者", "speaker", "name"),
    "source_zh": ("文字對話", "中文", "原文", "中文對話", "對話", "source"),
    "target_en": ("英文翻譯", "英文", "english", "en", "翻譯", "target"),
    "note": ("備註", "note", "remark", "comment"),
}

_IGNORED = ("情緒", "中字數", "字數", "emotion")


def _norm_header(value: Any) -> str:
    s = str(value or "").strip().lower()
    # 「備註（不需翻譯）」-> 「備註」
    for opener, closer in (("(", ")"), ("（", "）")):
        if opener in s:
            s = s.split(opener, 1)[0]
    return "".join(s.split())


def _match_column(header: str) -> str | None:
    h = _norm_header(header)
    if not h or h in _IGNORED:
        return None
    for field, aliases in COLUMN_ALIASES.items():
        if h in aliases:
            return field
    return None


def _find_header_row(rows: Sequence[Sequence[Any]], scan_limit: int = 15) -> int:
    """找出標題列的索引。判準是該列同時出現 ID 欄與英文翻譯欄。"""
    best_idx, best_hits = -1, 0
    for idx, row in enumerate(rows[:scan_limit]):
        fields = {f for f in (_match_column(c) for c in row) if f}
        if "dialogue_id" in fields and len(fields) > best_hits:
            best_idx, best_hits = idx, len(fields)
    if best_idx < 0:
        raise ValueError(
            "找不到標題列。需要至少有『對話ID』與『英文翻譯』兩個欄位標題。"
        )
    return best_idx


def _build_column_map(header_row: Sequence[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for col_idx, cell in enumerate(header_row):
        field = _match_column(cell)
        if field and field not in mapping:
            mapping[field] = col_idx
    missing = [f for f in ("dialogue_id", "target_en") if f not in mapping]
    if missing:
        raise ValueError(f"翻譯文本缺少必要欄位：{missing}")
    return mapping


def _cell(row: Sequence[Any], col_idx: int | None) -> str:
    if col_idx is None or col_idx >= len(row):
        return ""
    value = row[col_idx]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _read_xlsx(path: Path, sheet: str | None) -> list[list[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _read_delimited(path: Path) -> list[list[Any]]:
    delimiter = "\t" if path.suffix.lower() in (".tsv", ".txt") else ","
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [list(r) for r in csv.reader(fh, delimiter=delimiter)]


def load_speaker_map(path: str | Path | None) -> dict[str, str]:
    """讀取『中文發話者名 -> 英文發話者名』對照表（兩欄 csv，可有標題）。"""
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到發話者對照表：{p}")
    mapping: dict[str, str] = {}
    for row in _read_delimited(p):
        if len(row) < 2:
            continue
        zh, en = str(row[0]).strip(), str(row[1]).strip()
        if not zh or not en:
            continue
        if _norm_header(zh) in COLUMN_ALIASES["speaker_zh"]:
            continue  # 標題列
        mapping[zh] = en
    return mapping


def load_script(
    path: str | Path,
    speaker_map: dict[str, str] | None = None,
    sheet: str | None = None,
) -> list[ExpectedLine]:
    """讀進翻譯文本，回傳依列順序排好的 ExpectedLine 清單。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到翻譯文本：{p}")

    if p.suffix.lower() in (".xlsx", ".xlsm"):
        rows = _read_xlsx(p, sheet)
    elif p.suffix.lower() in (".csv", ".tsv", ".txt"):
        rows = _read_delimited(p)
    else:
        raise ValueError(f"不支援的檔案格式：{p.suffix}（請用 xlsx / csv / tsv）")

    header_idx = _find_header_row(rows)
    cols = _build_column_map(rows[header_idx])
    speaker_map = speaker_map or {}

    lines: list[ExpectedLine] = []
    for offset, row in enumerate(rows[header_idx + 1:], start=1):
        dialogue_id = _cell(row, cols.get("dialogue_id"))
        if not dialogue_id:
            continue  # 場景說明列 / 轉場註記列
        target_en = _cell(row, cols.get("target_en"))
        speaker_zh = _cell(row, cols.get("speaker_zh"))
        lines.append(
            ExpectedLine(
                order=len(lines),
                dialogue_id=dialogue_id,
                speaker_zh=speaker_zh,
                speaker_en=speaker_map.get(speaker_zh, ""),
                source_zh=_cell(row, cols.get("source_zh")),
                target_en=target_en,
                note=_cell(row, cols.get("note")),
                sheet_row=header_idx + 1 + offset,  # 1-based，對應試算表列號
            )
        )
    if not lines:
        raise ValueError("翻譯文本解析後沒有任何有效對話列（每列都缺對話ID）。")
    return lines


def unknown_speakers(lines: Iterable[ExpectedLine]) -> list[str]:
    """列出有中文名字但對照表查不到英文名的發話者，提醒使用者補齊。"""
    return sorted({ln.speaker_zh for ln in lines if ln.speaker_zh and not ln.speaker_en})
