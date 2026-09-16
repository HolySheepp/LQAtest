"""版本號。

規則：最前面是大型更新／翻修，中間是新功能或較多修復，最後是小修。
這幾個測試不判斷版本「對不對」（那是人的決定），只確保它的格式站得住、
而且全專案只有一個來源 —— 兩邊各寫一份遲早會對不上，而安裝檔上的
版本號寫錯是使用者第一眼就會看到的東西。
"""

from __future__ import annotations

import re
from pathlib import Path

import lqa

ROOT = Path(__file__).resolve().parent.parent


def test_version_looks_like_a_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", lqa.__version__), lqa.__version__


def test_installer_does_not_hardcode_a_version():
    """安裝精靈的版本要從 Python 帶過去，不能自己寫一份。

    只看 #define 那幾行，註解裡提到機制是正常的。
    """
    script = (ROOT / "packaging/installer.iss").read_text(encoding="utf-8")
    defines = [line for line in script.splitlines()
               if line.strip().startswith("#define AppVersion")]
    assert defines == ['  #define AppVersion "0.0.0"'], defines
    assert "#ifndef AppVersion" in script, "沒帶版本時要有退路"


def test_build_scripts_read_the_version_from_python():
    for name in ("tools/build_app.py", "tools/build_installer.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "from lqa import __version__" in source, name
