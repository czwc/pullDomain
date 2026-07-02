@echo off
cd /d "%~dp0"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Check and install requests
python -c "import requests" >nul 2>&1
if errorlevel 1 (
    echo Installing requests...
    pip install requests -q
)

:: Launch GUI
python query_gname_gui.py
if errorlevel 1 (
    echo Launch failed. Press any key to exit.
    pause
)
