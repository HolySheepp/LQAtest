# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 設定。

用 onedir 不用 onefile：onefile 每次啟動都要把整包解壓到暫存資料夾，
這裡光模型就 32 MB，每次開都等好幾秒。onedir 開得快，而且壓成 zip
之後對使用者來說一樣是「解壓就能用」。

要打包的話跑 tools/build_app.py，不要直接呼叫 pyinstaller ——
那個腳本會把 config 範例一起放好。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

# 模型與字典檔。rapidocr 是去自己的套件目錄找這些，所以路徑要照原樣擺
datas = collect_data_files("rapidocr")
datas += [(str(ROOT / "lqa/gui/assets"), "lqa/gui/assets")]

binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["onnxruntime", "openpyxl", "lqa.gui.app"],
    hookspath=[],
    runtime_hooks=[],
    # 這些都沒用到，去掉可以少掉上百 MB
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "pytest",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
        "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtNetwork", "PySide6.QtPdf", "PySide6.QtOpenGL",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LQA Checker",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # 介面程式，不要黑窗
    icon=str(ROOT / "lqa/gui/assets/icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LQA Checker",
)
