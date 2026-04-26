@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo [live2dagent] Launching live2dagent (Launcher mode)...
echo.
echo This will automatically start Gateway and then Frontend.
echo.

set PYTHON=python
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON="%~dp0.venv\Scripts\python.exe"
)

%PYTHON% -m aipet

if %errorlevel% neq 0 (
    echo.
    echo [live2dagent] Launcher exited with error code %errorlevel%.
    pause
)
