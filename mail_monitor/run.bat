@echo off
chcp 65001 >nul
echo ========================================
echo   邮件售出提醒监控 - 手动运行
echo ========================================
cd /d "%~dp0"
python mail_monitor.py
echo.
echo 按任意键退出...
pause >nul
