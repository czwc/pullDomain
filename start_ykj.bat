@echo off
setlocal EnableExtensions EnableDelayedExpansion
title gname ykj fetcher
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

%PYTHON_CMD% -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [INSTALL] Installing playwright...
    %PYTHON_CMD% -m pip install playwright
    if errorlevel 1 (
        echo [ERROR] Failed to install playwright. Check the network and retry.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [WARN] Playwright Chromium install failed.
    echo If local Chrome is installed, the script may still work.
    pause
)

set "FBSJ_ARG="
if "%~1"=="" (
    echo.
    echo Release date filter:
    echo   Press Enter = all dates
    echo   Enter N = today and previous N-1 days
    set /p "FBSJ_DAYS=Days: "
    if not "!FBSJ_DAYS!"=="" set "FBSJ_ARG=--fbsj-days !FBSJ_DAYS!"
)

echo.
echo Starting...
%PYTHON_CMD% fetch_gname_ykj_ranges.py !FBSJ_ARG! %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" echo [ERROR] Script exited with code %EXIT_CODE%
pause
exit /b %EXIT_CODE%
