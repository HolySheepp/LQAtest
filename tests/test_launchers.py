"""批次檔的編碼守則。

cmd.exe 讀 .bat 用的是系統 ANSI 編碼（這台機器是 cp950），不是 UTF-8。
把中文存進 .bat 會變成亂碼，而且會連指令本身一起拆壞 ——
實際發生過，整個啟動檔一行都跑不起來。

所以 .bat 一律只能放純 ASCII，中文提示交給 Python 印。
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BAT_FILES = sorted(PROJECT_ROOT.glob("*.bat"))


def test_there_is_at_least_one_launcher():
    assert BAT_FILES, "專案根目錄應該要有啟動用的 .bat"


@pytest.mark.parametrize("path", BAT_FILES, ids=lambda p: p.name)
class TestBatchFilesAreAscii:
    def test_contains_no_non_ascii_bytes(self, path: Path):
        raw = path.read_bytes()
        offending = [(i, hex(b)) for i, b in enumerate(raw) if b > 0x7F]
        assert not offending, (
            f"{path.name} 含有非 ASCII 位元組 {offending[:5]}；"
            "cmd.exe 會以 ANSI 編碼讀取而變成亂碼，中文請改由 Python 輸出"
        )

    def test_has_no_utf8_bom(self, path: Path):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), (
            f"{path.name} 開頭有 UTF-8 BOM，cmd 會把它當成指令的一部分"
        )

    def test_disables_command_echo(self, path: Path):
        first = path.read_text(encoding="ascii").splitlines()[0].strip().lower()
        assert first == "@echo off", f"{path.name} 第一行應該是 @echo off"
