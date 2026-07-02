@echo off
cd /d "%~dp0"

python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install from https://www.python.org/downloads/
    echo During install check "Add Python to PATH"
    pause
    exit /b 1
)

python -c "import requests" >nul 2>&1
if errorlevel 1 pip install requests -q

python -c "import playwright" >nul 2>&1
if errorlevel 1 pip install playwright -q

python -m playwright install chromium --quiet >nul 2>&1

echo Starting...
python query_gname_gui.py
if errorlevel 1 (
    echo.
    echo ERROR: see message above
    pause
)
