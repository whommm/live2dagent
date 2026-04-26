@echo off
chcp 65001 > nul
cd /d "%~dp0"

set PYTHON=python
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON="%~dp0.venv\Scripts\python.exe"
)

echo [live2dagent Frontend] Starting desktop pet...
echo Using Python: %PYTHON%
echo.

%PYTHON% -m aipet frontend
if %errorlevel% neq 0 (
    echo.
    echo [live2dagent Frontend] Exited with error code %errorlevel%.
    pause
)
