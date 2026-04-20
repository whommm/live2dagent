@echo off
chcp 65001 > nul
cd /d "%~dp0"

set PYTHON=python
if exist "%~dp0.venv\Scripts\python.exe" (
    set PYTHON="%~dp0.venv\Scripts\python.exe"
)

echo [AIPet Gateway] Starting backend server...
echo Using Python: %PYTHON%
echo.

%PYTHON% -m aipet gateway
if %errorlevel% neq 0 (
    echo.
    echo [AIPet Gateway] Server exited with error code %errorlevel%.
    pause
)
