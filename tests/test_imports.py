"""每個模組都要 import 得起來。

有些模組是延遲載入的（開視窗時才 import），語法錯誤或漏掉的 import
不會在任何測試裡冒出來 —— 直到使用者去點那個按鈕才當場炸掉。
這裡把全部掃一遍，補上這個缺口。
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import lqa

MODULES = sorted(
    name for _finder, name, _pkg in pkgutil.walk_packages(lqa.__path__, "lqa.")
)


def test_there_are_modules_to_check():
    assert len(MODULES) > 20, MODULES


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    pytest.importorskip("PySide6", reason="介面模組需要 PySide6")
    importlib.import_module(name)
