"""從 git 標籤產生 CHANGELOG.md。

版本說明寫在標籤上，不另外維護一份 —— 兩邊各寫一份遲早會對不上，
而且標籤是跟著 commit 走的，回頭查「這個功能是哪一版進來的」查得到。

    .venv/Scripts/python.exe tools/make_changelog.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEADER = """# 版本紀錄

版本號規則：

| 位置 | 什麼時候加 |
|---|---|
| 最前面 | 大型更新、翻修 |
| 中間 | 新功能、較多 bug 修復或優化 |
| 最後 | 小更新、bug 修復 |

1.0 之前一律 0.x：那段時間還在摸索要怎麼抓字、要不要自動偵測打字結束，
架構翻過好幾次。1.0.0 是第一個能直接交給同事的版本。

這份檔案由 tools/make_changelog.py 從 git 標籤產生，不要手改。

"""


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.lstrip("v").split("."))


def main() -> int:
    raw = subprocess.run(
        ["git", "tag", "-l", "--format=%(refname:short)\t%(contents:subject)\t"
         "%(objectname:short)\t%(*objectname:short)"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)

    rows = []
    for line in raw.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        tag, subject = parts[0], parts[1]
        commit = (parts[3] if len(parts) > 3 and parts[3] else parts[2])
        rows.append((tag, subject, commit))
    rows.sort(key=lambda r: version_key(r[0]), reverse=True)

    lines = [HEADER]
    for tag, subject, commit in rows:
        note = subject.split(" ", 1)[1] if " " in subject else subject
        lines.append(f"### {tag.lstrip('v')}　`{commit}`\n\n{note}\n")
    (ROOT / "CHANGELOG.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"寫出 CHANGELOG.md，共 {len(rows)} 個版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
