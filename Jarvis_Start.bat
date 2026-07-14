@echo off
title Jarvis Command Center
cd /d "%~dp0"

rem Respect an explicit interpreter, otherwise use the project virtual
rem environment only when it can actually start. A copied workspace may
rem contain a stale venv whose pyvenv.cfg still points at another PC, so
rem checking that pythonw.exe merely exists is not enough.
if defined JARVIS_PYTHONW goto launch
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "JARVIS_PYTHONW=.venv\Scripts\pythonw.exe"
)
if defined JARVIS_PYTHONW goto launch

where pythonw >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python environment not found.
    echo Run phase 0 setup or install the requirements first.
    pause
    exit /b 1
)
set "JARVIS_PYTHONW=pythonw"

:launch
start "" "%JARVIS_PYTHONW%" jarvis_app.py
exit /b 0
