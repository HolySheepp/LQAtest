"""產生 GitHub Release 要貼的說明。

在 GitHub 上發佈版本時，選好 tag 之後說明欄是空的。GitHub 自動產生的
那種只會列 commit 標題，看不出這一版到底解決了什麼。

這裡直接拿 commit 訊息的內文 —— 那本來就寫了「為什麼這樣改」，
正是看發佈說明的人想知道的事。跨多個 commit 的版本會把中間的都列出來。

    .venv/Scripts/python.exe tools/release_notes.py            目前版本
    .venv/Scripts/python.exe tools/release_notes.py v0.22.0    指定版本
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失敗：{result.stderr.strip()}")
    return result.stdout.strip()


def sorted_tags() -> list[str]:
    def key(tag: str) -> tuple[int, ...]:
        try:
            return tuple(int(p) for p in tag.lstrip("v").split("."))
        except ValueError:
            return (0,)

    return sorted((t for t in git("tag").splitlines() if t), key=key)


def previous_tag(tag: str) -> str | None:
    tags = sorted_tags()
    if tag not in tags:
        raise SystemExit(f"沒有這個標籤：{tag}")
    index = tags.index(tag)
    return tags[index - 1] if index else None


def strip_trailers(body: str) -> str:
    """拿掉 Co-Authored-By 那類結尾，那不是發佈說明的內容。"""
    lines = [line for line in body.splitlines()
             if not line.startswith(("Co-Authored-By:", "Signed-off-by:"))]
    return "\n".join(lines).strip()


def tag_summary(tag: str) -> str:
    """標籤自己的說明。標籤訊息寫成「版本號 一句話」，這裡取後半。"""
    subject = git("for-each-ref", "--format=%(contents:subject)",
                  f"refs/tags/{tag}")
    version = tag.lstrip("v")
    if subject.startswith(version):
        subject = subject[len(version):].strip()
    return subject


def commits_in(tag: str, previous: str | None) -> list[tuple[str, str]]:
    """這一版包含哪些 commit。回傳 (標題, 內文)，新的在前面。"""
    span = f"{previous}..{tag}" if previous else tag
    raw = git("log", "--no-merges", "--format=%H", span)
    out = []
    for sha in raw.splitlines():
        if not sha:
            continue
        out.append((git("log", "-1", "--format=%s", sha),
                    strip_trailers(git("log", "-1", "--format=%b", sha))))
    return out


def notes(tag: str) -> str:
    previous = previous_tag(tag)
    version = tag.lstrip("v")
    summary = tag_summary(tag)
    changes = commits_in(tag, previous)

    parts = [f"## {version}", ""]
    if summary:
        parts += [summary, ""]

    if len(changes) > 1:
        parts += ["### 這一版的內容", ""]
        parts += [f"- {subject}" for subject, _body in changes]
        parts.append("")

    # commit 內文寫的是「為什麼這樣改」，正是看發佈說明的人想知道的
    details = [(subject, body) for subject, body in changes if body]
    if details:
        parts += ["### 說明", ""]
        for subject, body in details:
            if len(details) > 1:
                parts += [f"#### {subject}", ""]
            parts += [body, ""]

    parts += [
        "### 下載",
        "",
        "| 檔案 | 用途 |",
        "|---|---|",
        f"| `LQA-Checker-{version}-setup.exe` | 安裝精靈，會建捷徑、可從"
        "「應用程式與功能」移除 |",
        f"| `LQA-Checker-{version}-portable.zip` | 解壓即用，不必安裝 |",
        "",
        "不需要安裝 Python 或任何其他環境。",
        "第一次使用要先到「設定 → 進階 → 開啟調試視窗」確認取字範圍，"
        "那是照模擬器視窗大小定的。",
        "",
        "開不起來的話執行 `\"LQA Checker.exe\" --self-test`，"
        "它會在同一個資料夾寫出 `self-test.txt`。",
        "",
        "重新安裝不會蓋掉 `config/`，移除也不會刪掉 `projects/`。",
    ]
    if previous:
        parts += ["",
                  f"**完整差異**："
                  f"https://github.com/HolySheepp/LQAtest/compare/{previous}...{tag}"]
    return "\n".join(parts) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        tag = argv[1] if argv[1].startswith("v") else f"v{argv[1]}"
    else:
        sys.path.insert(0, str(ROOT))
        from lqa import __version__

        tag = f"v{__version__}"

    text = notes(tag)
    out = ROOT / "dist" / f"release-notes-{tag.lstrip('v')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"（同一份也寫到 {out.relative_to(ROOT)}，直接複製貼到 GitHub）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
