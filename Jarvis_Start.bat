@echo off
title Jarvis Command Center
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" jarvis_app.py
    exit /b 0
)
where pythonw >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python environment not found.
    echo Run phase 0 setup or install the requirements first.
    pause
    exit /b 1
)
start "" pythonw jarvis_app.py
