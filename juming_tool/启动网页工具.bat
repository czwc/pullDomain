@echo off
setlocal EnableExtensions
title juming ykj web
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python was not found.
    echo Install Python and enable "Add Python to PATH".
    pause
    exit /b 1
)

%PYTHON_CMD% --version
if errorlevel 1 (
    echo [ERROR] Python command exists but cannot run.
    pause
    exit /b 1
)

%PYTHON_CMD% -c "import fastapi, uvicorn, pydantic, aiohttp" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing web dependencies...
    %PYTHON_CMD% -m pip install fastapi uvicorn pydantic aiohttp
    if errorlevel 1 (
        echo [ERROR] Failed to install web dependencies.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing playwright...
    %PYTHON_CMD% -m pip install playwright
    if errorlevel 1 (
        echo [ERROR] Failed to install playwright.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% -m playwright install chromium

set "WEB_PORT=8003"
echo [INFO] Starting web server at http://127.0.0.1:%WEB_PORT%
start "juming ykj web server" cmd /k "%PYTHON_CMD% -m uvicorn web_app:app --host 127.0.0.1 --port %WEB_PORT%"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%WEB_PORT%"
exit /b 0
