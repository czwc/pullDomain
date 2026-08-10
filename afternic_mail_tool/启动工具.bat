@echo off
setlocal EnableExtensions
title Afternic 售出邮件监控
chcp 65001 >nul
cd /d "%~dp0"

set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装并添加到 PATH。
    pause
    exit /b 1
)

%PY% --version
if errorlevel 1 ( echo [错误] Python 无法运行。 & pause & exit /b 1 )

echo.
echo ============================================
echo   Afternic 售出邮件监控工具
echo ============================================
echo.
echo  电脑访问：http://127.0.0.1:8001
echo  手机访问：http://你的电脑IP:8001
echo  （手机和电脑需在同一 WiFi 下）
echo.
echo  手机可"添加到主屏幕"当 App 用
echo.

REM 检查依赖
%PY% -c "import fastapi,uvicorn,pydantic" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装依赖...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 ( echo [错误] 依赖安装失败。 & pause & exit /b 1 )
)

start "" "http://127.0.0.1:8001"
%PY% -m uvicorn app:app --host 0.0.0.0 --port 8001

if errorlevel 1 ( echo. & echo [错误] 服务异常退出。 & pause )
