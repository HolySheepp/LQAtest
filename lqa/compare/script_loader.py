"""讀取翻譯文本（正確答案）。

支援 .xlsx / .csv / .tsv。Google Sheet 可直接下載成 xlsx，或複製成 tsv 貼成檔案。

表格特性（依實際專案檔）：
  - 標題不一定在第 1 列，要自動找
  - 「場景 / 出場人物」這類說明列沒有對話ID，跳過
  - 對話ID 不等於播放順序，**列順序才是期望順序**
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from dataclasses import dataclass, field
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


def _read_xlsx_sheets(path: Path) -> dict[str, list[list[Any]]]:
    """讀出活頁簿裡的所有工作表，保持原本順序。

    實際的專案檔一個活頁簿會有十幾個工作表（活動簡介、大綱、AVG1..AVG10），
    對白只在其中幾個裡面，所以不能假設資料在第一個工作表。
    """
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            name: [list(r) for r in wb[name].iter_rows(values_only=True)]
            for name in wb.sheetnames
        }
    finally:
        wb.close()


def _read_delimited(path: Path) -> list[list[Any]]:
    delimiter = "\t" if path.suffix.lower() in (".tsv", ".txt") else ","
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [list(r) for r in csv.reader(fh, delimiter=delimiter)]


# 對照表的欄位標題別名。這份表是跨檔案共用的，格式不一定由我們決定，
# 所以中英兩欄都盡量多認幾種寫法。
SPEAKER_ZH_ALIASES = (
    "名字", "中文", "中文名", "中文名字", "中文姓名", "發話者", "角色",
    "角色名", "說話者", "原文", "speaker", "name", "chinese", "cn", "zh",
)
SPEAKER_EN_ALIASES = (
    "english", "en", "英文", "英文名", "英文名字", "英文姓名", "英譯",
    "翻譯", "譯名", "name_en", "englishname",
)


# 對照表可以用的純文字格式。xlsx 走另一條路
SPEAKER_TEXT_SUFFIXES = (".csv", ".tsv", ".txt", ".md")
# 一行裡用來分開中英文的符號。中文輸入法打出來的逗號是全形，
# 只認半形只會讓人打了一份看起來沒問題、卻一個都對不上的表
SPEAKER_SEPARATORS = re.compile(r"[,，	;；]")


def _read_speaker_text(path: Path) -> list[tuple[int, list[str]]]:
    """讀手寫的對照表：一行一個名字，中文在前、英文在後。

    這份表是人手打的，不是程式產生的，所以盡量收：
      - 半形或全形的逗號、分號、tab 都算分隔
      - markdown 的表格（| 中文 | English |）直接認得，連分隔線一起跳過
      - 以 # 開頭的行當成標題或註解略過
      - 空行略過
    """
    rows: list[tuple[int, list[str]]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if set(line) <= set("|-: "):
            continue                    # markdown 表格的分隔線
        if "|" in line:
            parts = line.strip("|").split("|")
        else:
            parts = SPEAKER_SEPARATORS.split(line)
        rows.append((number, [cell.strip() for cell in parts]))
    return rows


def _speaker_columns(header: Sequence[Any]) -> tuple[int, int] | None:
    """從標題列找出中文名與英文名各在第幾欄。找不到就回 None。"""
    zh_idx = en_idx = None
    for idx, cell in enumerate(header):
        key = _norm_header(cell)
        if zh_idx is None and key in SPEAKER_ZH_ALIASES:
            zh_idx = idx
        elif en_idx is None and key in SPEAKER_EN_ALIASES:
            en_idx = idx
    if zh_idx is None or en_idx is None:
        return None
    return zh_idx, en_idx


@dataclass
class SpeakerEntry:
    """對照表裡的一列。帶著列號才改得動原檔。"""

    zh: str
    en: str
    row: int        # 1 起算。xlsx 是工作表列號，純文字是行號


@dataclass
class SpeakerMap:
    """對照表的內容，外加讀的時候發現的問題。"""

    names: dict[str, str]
    # 同一個中文名被填了兩種以上的英文名。第一個會被採用，其餘忽略 ——
    # 但這幾乎一定是表本身填錯了，不講出來就會一路錯下去
    conflicts: dict[str, list[str]]
    # 中英文都一樣的重複列。不影響結果，但大表裡會越積越多
    duplicates: dict[str, list[int]] = field(default_factory=dict)
    entries: list[SpeakerEntry] = field(default_factory=list)
    path: Path | None = None


def read_speaker_map(path: str | Path | None) -> SpeakerMap:
    """讀取跨檔案共用的『中文發話者名 -> 英文發話者名』對照表。

    這張表是發話者檢查的正確答案來源：文本裡的發話者是中文，
    遊戲畫面顯示的是英文，所以要先用中文名查出對應英文名，
    再拿去和畫面 OCR 到的名字比對。

    格式隨便挑：

      xlsx / xlsm      第一欄中文、第二欄英文
      csv / txt / md   一行一個，中文在前、英文在後，用逗號分開
                       （全形逗號、分號、tab、markdown 表格也認）

    兩種都會先看有沒有標題列，有的話靠標題文字找中英兩欄，
    沒有就當成「第一欄中文、第二欄英文」。
    """
    if not path:
        return SpeakerMap({}, {})
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到發話者對照表：{p}")

    suffix = p.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        sheets = _read_xlsx_sheets(p)
        raw = next(iter(sheets.values())) if sheets else []
        rows = list(enumerate(raw, 1))
    elif suffix in SPEAKER_TEXT_SUFFIXES:
        rows = _read_speaker_text(p)
    else:
        raise ValueError(
            f"不支援的對照表格式：{p.suffix}"
            "（可用 xlsx、xlsm、csv、tsv、txt、md）")

    numbered = [(n, r) for n, r in rows
                if any(str(c).strip() for c in r if c is not None)]
    rows = [r for _n, r in numbered]
    if not rows:
        return SpeakerMap({}, {}, path=p)

    columns = None
    body = numbered
    for idx, row in enumerate(rows[:5]):
        found = _speaker_columns(row)
        if found:
            columns, body = found, numbered[idx + 1:]
            break
    if columns is None:
        columns = (0, 1)  # 沒有可辨識的標題，當成兩欄表

    zh_idx, en_idx = columns
    mapping: dict[str, str] = {}
    seen: dict[str, list[str]] = {}
    entries: list[SpeakerEntry] = []
    dupes: dict[str, list[int]] = {}
    for number, row in body:
        zh, en = _cell(row, zh_idx), _cell(row, en_idx)
        if not zh or not en:
            continue
        if _norm_header(zh) in SPEAKER_ZH_ALIASES:
            continue  # 殘留的標題列
        mapping.setdefault(zh, en)
        entries.append(SpeakerEntry(zh, en, number))
        if en in seen.setdefault(zh, []):
            # 中英文都一樣的重複列。第一列留著，其餘記下來供修剪
            dupes.setdefault(f"{zh}\t{en}", []).append(number)
        else:
            seen[zh].append(en)
    return SpeakerMap(
        mapping,
        {zh: names for zh, names in seen.items() if len(names) > 1},
        duplicates=dupes,
        entries=entries,
        path=p,
    )


def load_speaker_map(path: str | Path | None) -> dict[str, str]:
    """只要對照表本身。想知道表有沒有填錯請用 read_speaker_map。"""
    return read_speaker_map(path).names


ALL_SHEETS = "all"


@dataclass
class SheetInfo:
    """一個看起來裝著對白的工作表。"""

    name: str
    line_count: int
    first_id: str
    last_id: str


def _read_sheets(path: Path) -> dict[str, list[list[Any]]]:
    """統一成 {工作表名稱: 列資料}。csv/tsv 視為單一工作表。"""
    if not path.exists():
        raise FileNotFoundError(f"找不到翻譯文本：{path}")
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return _read_xlsx_sheets(path)
    if suffix in (".csv", ".tsv", ".txt"):
        return {path.stem: _read_delimited(path)}
    raise ValueError(f"不支援的檔案格式：{path.suffix}（請用 xlsx / csv / tsv）")


def _parse_sheet(
    name: str,
    rows: Sequence[Sequence[Any]],
    speaker_map: dict[str, str],
    start_order: int,
) -> list[ExpectedLine]:
    header_idx = _find_header_row(rows)
    cols = _build_column_map(rows[header_idx])

    lines: list[ExpectedLine] = []
    for offset, row in enumerate(rows[header_idx + 1:], start=1):
        dialogue_id = _cell(row, cols.get("dialogue_id"))
        if not dialogue_id:
            continue  # 場景說明列 / 轉場註記列
        speaker_zh = _cell(row, cols.get("speaker_zh"))
        lines.append(
            ExpectedLine(
                order=start_order + len(lines),
                dialogue_id=dialogue_id,
                speaker_zh=speaker_zh,
                speaker_en=speaker_map.get(speaker_zh, ""),
                source_zh=_cell(row, cols.get("source_zh")),
                target_en=_cell(row, cols.get("target_en")),
                note=_cell(row, cols.get("note")),
                sheet=name,
                sheet_row=header_idx + 1 + offset,  # 1-based，對應試算表列號
            )
        )
    return lines


def list_dialogue_sheets(path: str | Path) -> list[SheetInfo]:
    """列出活頁簿裡看起來裝著對白的工作表。

    判準是該工作表找得到標題列，而且至少有一列有對話ID。
    活動簡介、大綱這類工作表會自然被排除。
    """
    found: list[SheetInfo] = []
    for name, rows in _read_sheets(Path(path)).items():
        try:
            lines = _parse_sheet(name, rows, {}, 0)
        except ValueError:
            continue  # 沒有標題列，不是對白表
        if lines:
            found.append(
                SheetInfo(
                    name=name,
                    line_count=len(lines),
                    first_id=lines[0].dialogue_id,
                    last_id=lines[-1].dialogue_id,
                )
            )
    return found


def load_script(
    path: str | Path,
    speaker_map: dict[str, str] | None = None,
    sheets: str | Sequence[str] | None = None,
) -> list[ExpectedLine]:
    """讀進翻譯文本，回傳依列順序排好的 ExpectedLine 清單。

    sheets 可以是單一名稱、名稱清單、"all"（全部串起來），或 None。
    None 時：只有一個對白工作表就直接用；有多個則報錯要求指定，
    免得把十個場景串成一條序列後，只錄了其中一個場景卻報出滿江紅的缺句。
    """
    p = Path(path)
    all_rows = _read_sheets(p)
    speaker_map = speaker_map or {}

    requested = [sheets] if isinstance(sheets, str) else list(sheets or [])
    if any(name.lower() == ALL_SHEETS for name in requested):
        wanted = list(all_rows)
    elif requested:
        wanted = requested
    else:
        available = list_dialogue_sheets(p)
        if not available:
            raise ValueError(
                "翻譯文本裡找不到任何對白工作表。"
                "需要至少有『對話ID』與『英文翻譯』兩個欄位標題。"
            )
        if len(available) > 1:
            listing = "、".join(
                f"{s.name}({s.line_count}句)" for s in available
            )
            raise ValueError(
                f"這份文本有多個對白工作表：{listing}。\n"
                f"請用 --sheet 指定要用哪一個（例如 --sheet {available[0].name}），"
                f"或用 --sheet all 把全部串成一條序列。"
            )
        wanted = [available[0].name]

    missing = [name for name in wanted if name not in all_rows]
    if missing:
        raise ValueError(
            f"找不到工作表：{'、'.join(missing)}。"
            f"這個檔案有：{'、'.join(all_rows)}"
        )

    lines: list[ExpectedLine] = []
    skipped: list[str] = []
    for name in wanted:
        try:
            lines.extend(_parse_sheet(name, all_rows[name], speaker_map, len(lines)))
        except ValueError:
            skipped.append(name)

    if not lines:
        detail = f"（略過了沒有標題列的工作表：{'、'.join(skipped)}）" if skipped else ""
        raise ValueError(f"翻譯文本解析後沒有任何有效對話列。{detail}")
    return lines


def unknown_speakers(lines: Iterable[ExpectedLine]) -> list[str]:
    """列出有中文名字但對照表查不到英文名的發話者，提醒使用者補齊。"""
    return sorted({ln.speaker_zh for ln in lines if ln.speaker_zh and not ln.speaker_en})


def write_speaker_edits(path: str | Path, delete_rows: set[int],
                        updates: dict[int, str]) -> Path:
    """就地修改對照表：刪掉指定列、改掉指定列的英文名。

    先備份再動手。這是使用者自己維護的檔案，而且可能是整個團隊共用的 ——
    改壞了沒有第二份，所以一定留一份原樣的在旁邊。

    xlsx 走 openpyxl 原地改，只動需要動的儲存格與列；純文字檔重寫整份，
    但保留註解與空行。
    """
    p = Path(path)
    # 時間戳而不是固定的 .bak：改第二次時不能把唯一一份原樣的蓋掉
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = p.with_name(f"{p.stem}_{stamp}{p.suffix}.bak")
    backup.write_bytes(p.read_bytes())

    if p.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(p)
        ws = wb[wb.sheetnames[0]]
        table = read_speaker_map(p)
        columns = {e.row: e for e in table.entries}
        for row, english in updates.items():
            entry = columns.get(row)
            if entry is None:
                continue
            for column in range(1, ws.max_column + 1):
                if str(ws.cell(row=row, column=column).value or "").strip() == entry.en:
                    ws.cell(row=row, column=column, value=english)
                    break
        for row in sorted(delete_rows, reverse=True):
            ws.delete_rows(row)
        wb.save(p)
        wb.close()
        return backup

    lines = p.read_text(encoding="utf-8-sig").splitlines()
    kept: list[str] = []
    for number, line in enumerate(lines, 1):
        if number in delete_rows:
            continue
        english = updates.get(number)
        if english is not None:
            separator = "," if "," in line or "\uff0c" in line else "\t"
            head = SPEAKER_SEPARATORS.split(line.strip(), 1)[0].strip()
            line = f"{head}{separator}{english}"
        kept.append(line)
    p.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return backup
