@echo off
setlocal EnableExtensions
title gname ykj web
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

%PYTHON_CMD% -c "import fastapi, uvicorn, pydantic" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing web dependencies...
    %PYTHON_CMD% -m pip install fastapi uvicorn pydantic
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

start "" "http://127.0.0.1:8000"
%PYTHON_CMD% -m uvicorn web_app:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
    echo.
    echo [ERROR] Web server exited with an error.
    pause
)
