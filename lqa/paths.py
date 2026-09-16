"""設定、專案、記錄要放在哪裡。

從原始碼跑的時候一切都相對於目前目錄，和以前一樣。
打包成執行檔之後改成放在**執行檔旁邊** —— 這是 portable 版的重點：
整個資料夾複製到隨身碟或另一台機器就能接著用，不會有東西被偷偷寫到
使用者找不到的地方（AppData 之類的）。

打包後的程式本體是唯讀的（可能裝在 Program Files），所以模型與素材
從 _MEIPASS 讀，可寫的東西一律放執行檔旁邊。這兩件事要分開。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """可寫的東西放這裡：config、projects、logs。"""
    if is_frozen():
        return Path(sys.executable).parent
    return Path.cwd()


def resource_dir() -> Path:
    """唯讀的東西在這裡：模型、圖示、音效。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def config_path(name: str) -> Path:
    return base_dir() / "config" / name


def project_root() -> Path:
    return base_dir() / "projects"


def logs_dir() -> Path:
    return base_dir() / "logs"
