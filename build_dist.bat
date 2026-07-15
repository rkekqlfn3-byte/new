@echo off
setlocal
cd /d "%~dp0"

rem Respect an explicit interpreter, otherwise use the project virtual
rem environment only when it can actually start.  A copied workspace may
rem contain a stale venv whose pyvenv.cfg still points at another PC.
if defined JARVIS_PYTHON goto python_selected
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 set "JARVIS_PYTHON=.venv\Scripts\python.exe"
)
if not defined JARVIS_PYTHON set "JARVIS_PYTHON=python"

:python_selected

echo ==============================================
echo    Jarvis reproducible build
echo ==============================================

"%JARVIS_PYTHON%" -c "import PyInstaller, eel" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Build dependencies are missing.
    echo Run: pip install -r requirements-build.txt
    exit /b 1
)

echo [1/6] Auditing release inputs...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint verification.release_security_audit ^
    --project-root . ^
    --json-report verification\phase3_prebuild_audit_report.json
if errorlevel 1 exit /b 1

echo [2/6] Running regression tests...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint tests.test_runner all
if errorlevel 1 exit /b 1

echo [3/6] Stamping Git commit and Windows version metadata...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint verification.generate_build_identity --project-root .
if errorlevel 1 exit /b 1

echo [4/6] Building dist\Jarvis...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint PyInstaller --noconfirm --clean Jarvis.spec
if errorlevel 1 exit /b 1

echo [5/6] Creating dist\Jarvis.zip...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint verification.release_archive ^
    --source dist\Jarvis ^
    --output dist\Jarvis.zip
if errorlevel 1 exit /b 1

echo [6/6] Auditing EXE and archive...
"%JARVIS_PYTHON%" -m verification.runtime_entrypoint verification.release_security_audit ^
    --project-root . ^
    --dist dist\Jarvis ^
    --archive dist\Jarvis.zip ^
    --json-report verification\phase3_release_audit_report.json
if errorlevel 1 exit /b 1

echo ==============================================
echo Build complete: dist\Jarvis\Jarvis.exe
echo Archive: dist\Jarvis.zip
echo Persistent data: %%LOCALAPPDATA%%\Jarvis\data
echo ==============================================
exit /b 0
