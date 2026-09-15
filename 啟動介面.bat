@echo off
rem This file must stay PURE ASCII: cmd.exe reads batch files with the system
rem ANSI codepage, so Chinese text here turns into mojibake and breaks parsing.
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv not found. Run setup first.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m lqa.gui.app
