@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo [INFO] This script only pushes committed changes.
echo [INFO] It will NOT run git add or git commit.
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Current folder is not a Git repository.
    pause
    exit /b 1
)

for /f "delims=" %%b in ('git branch --show-current') do set "BRANCH=%%b"
if not defined BRANCH (
    echo [ERROR] Cannot detect current branch.
    pause
    exit /b 1
)

echo [INFO] Current branch: %BRANCH%
echo.

echo [INFO] Uncommitted local changes, if any:
git --no-pager status --short
echo.

echo [INFO] Commits waiting to push:
git --no-pager log --oneline origin/%BRANCH%..HEAD 2>nul
echo.

echo [INFO] Pushing committed changes by SSH...
git push git@github.com:czwc/pullDomain.git %BRANCH%
if errorlevel 1 (
    echo.
    echo [ERROR] Push failed. Check network, SSH key, or GitHub permissions.
    pause
    exit /b 1
)

echo.
echo [OK] Push finished.
pause
