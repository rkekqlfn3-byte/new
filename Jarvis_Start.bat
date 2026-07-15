@echo off
setlocal
title Jarvis Command Center
cd /d "%~dp0"

rem Respect explicit interpreters. JARVIS_PYTHON performs the visible
rem preflight; JARVIS_PYTHONW starts the windowed application.
if defined JARVIS_PYTHONW (
    if not defined JARVIS_PYTHON (
        if /i "%JARVIS_PYTHONW%"=="pythonw" set "JARVIS_PYTHON=python"
        if not defined JARVIS_PYTHON set "JARVIS_PYTHON=%JARVIS_PYTHONW:pythonw.exe=python.exe%"
    )
    goto validate
)

rem A copied workspace can contain a stale venv. Require both interpreters
rem and verify that the console interpreter can actually start.
if exist ".venv\Scripts\python.exe" if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "JARVIS_PYTHON=.venv\Scripts\python.exe"
        set "JARVIS_PYTHONW=.venv\Scripts\pythonw.exe"
        goto validate
    )
)

where python >nul 2>&1
if errorlevel 1 goto python_missing
where pythonw >nul 2>&1
if errorlevel 1 goto python_missing
set "JARVIS_PYTHON=python"
set "JARVIS_PYTHONW=pythonw"

:validate
"%JARVIS_PYTHON%" -m engine.startup_check
if errorlevel 1 (
    echo.
    echo [ERROR] Jarvis was not started.
    pause
    exit /b 1
)

start "" "%JARVIS_PYTHONW%" jarvis_app.py
exit /b 0

:python_missing
echo [ERROR] Python environment not found.
echo Install the pinned requirements before starting Jarvis.
pause
exit /b 1
