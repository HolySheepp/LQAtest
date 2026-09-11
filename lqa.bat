@echo off
rem Shortcut so you can type "lqa <command>" instead of the full interpreter path.
rem PYTHONPATH is set (rather than changing directory) so relative paths you pass
rem on the command line still resolve against wherever you ran this from.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run this once in the project folder:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ocr.txt
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m lqa %*
