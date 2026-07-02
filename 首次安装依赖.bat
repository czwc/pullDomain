@echo off
chcp 65001 >nul
echo ============================================
echo   gname 一口价查询工具 - 首次安装依赖
echo ============================================
echo.
echo 正在检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python！
    echo.
    echo 请先安装 Python：
    echo 1. 打开浏览器访问 https://www.python.org/downloads/
    echo 2. 点击 "Download Python" 下载安装包
    echo 3. 安装时必须勾选 "Add Python to PATH"
    echo 4. 安装完成后重新双击本文件
    echo.
    pause
    exit /b 1
)
echo [OK] Python 已安装

echo.
echo 正在安装所需库 requests...
pip install requests -q
if errorlevel 1 (
    echo [错误] 安装失败，请检查网络连接后重试
    pause
    exit /b 1
)
echo [OK] 依赖安装完成！
echo.
echo ============================================
echo   安装完成！以后直接双击 "启动工具.bat" 即可
echo ============================================
echo.
pause
