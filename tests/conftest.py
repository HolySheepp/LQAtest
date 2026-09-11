"""測試共用 fixture。

sample_xlsx 刻意複製實際專案檔的版面特徵：
  - 第 1 列是尺規列，標題在第 2 列
  - 第 3 列是「場景 / 出場人物」說明列，沒有對話ID
  - 中途插入的 80208118 / 80208119 讓 ID 順序與列順序不一致
  - 中間有一列只有備註、沒有對話
"""

from __future__ import annotations

import pytest

# 依實際文本抄下來的樣本（含彎引號、破折號、刪節號）
SAMPLE_ROWS = [
    ("80208001", "", "", "第二次模擬考當天，學生們陸續入座，教室內氣氛顯得格外嚴肅。",
     "On the day of the second mock exam, students filed into their seats. "
     "A heavy silence settled over the room.", "黑幕淡入-"),
    ("80208002", "", "", "涅維站在門口，眼睛還緊緊盯著複習到最後一刻的筆記。",
     "Nev stood by the doorway, his eyes still fixed intently on the study notes "
     "he had been reviewing right up until the last minute.", ""),
    ("80208003", "蘇青", "苦笑", "好了，先別看了，再看下去等等連題目都要混在一起了。",
     "Alright, stop looking at them now. If you keep cramming, you're going to "
     "scramble the answers when the test starts.", ""),
    ("80208004", "涅維", "生氣", "還有幾個公式沒記熟......",
     "There are still a few formulas I haven’t memorized properly...", ""),
    ("80208005", "蘇青", "欣慰", "放輕鬆，只要盡力就好。",
     "Take it easy. Do your best, that's all that matters.", ""),
    ("80208006", "涅維", "受擊", "可是——", "But—", ""),
    ("80208007", "蘇青", "放鬆", "不管最後考幾分都沒關係......",
     "It doesn't matter what score you end up with...", ""),
    ("80208008", "蘇青", "放鬆", "無論結果如何，我們再一起想想其他辦法。",
     "No matter how it turns out, we'll figure something out.", ""),
    ("80208009", "", "", "涅維沉默片刻，終於闔上筆記，深吸一口氣。",
     "Nev fell silent. After a moment, he snapped his notebook shut and "
     "exhaled slowly.", ""),
    ("80208010", "涅維", "生氣", "......我知道了。", "...Fine.", ""),
    ("80208118", "涅維", "生氣", "鴉老......我哥可也花了不少時間教我，我會努力不讓你們失望的。",
     "The Owls' boss—I mean, your brother also spent a lot of time helping me. "
     "I will not disappoint you.", ""),
    ("80208119", "蘇青", "放鬆", "涅維......", "Nev...", ""),
    ("", "", "", "", "", "轉場-刷新"),
    ("80208011", "", "", "站在教室外，透過窗戶能看見學生們埋頭作答的身影。",
     "Through the classroom windows, they watched the students hunch over "
     "their exam papers.", "學校鐘聲"),
]

SPEAKER_MAP = {"蘇青": "Suqing", "涅維": "Nev"}


@pytest.fixture
def sample_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "全線文本"

    ws.append(["", "", "一二三四五六七八九十", "", "", "AVG ID : 80208", ""])  # 尺規列
    ws.append(["對話ID", "名字", "情緒", "文字對話", "英文翻譯", "備註（不需翻譯）", "中字數"])
    ws.append(["", "", "", "場景：教室+走廊\n出場人物：蘇青、涅維", "", "", ""])  # 場景說明列

    for dialogue_id, speaker, emotion, zh, en, note in SAMPLE_ROWS:
        ws.append([dialogue_id, speaker, emotion, zh, en, note, len(zh)])

    path = tmp_path / "script.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def speaker_csv(tmp_path):
    path = tmp_path / "speakers.csv"
    lines = ["名字,English"] + [f"{zh},{en}" for zh, en in SPEAKER_MAP.items()]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
