@echo off
rem ---------------------------------------------------------------------------
rem This file must stay PURE ASCII.
rem cmd.exe reads batch files using the system ANSI codepage (cp950 on this
rem machine), not UTF-8, so any Chinese text saved here turns into mojibake and
rem corrupts the commands themselves. All Chinese output is printed by Python
rem instead, which handles UTF-8 correctly.
rem ---------------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo.
    echo [ERROR] .venv not found. Run these two lines first:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ocr.txt
    echo.
    cmd /k
    exit /b 1
)

call "%~dp0lqa.bat" start
cmd /k
