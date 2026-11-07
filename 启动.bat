@echo off
title IVL 2026 Fall - Survivor BP Helper
cd /d "%~dp0"

echo.
echo   ============================================
echo     IVL 2026 秋季赛 · 求生方 BP 辅助工具
echo   ============================================
echo.

rem ── 检测 Python ──
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
    echo   [X] 没有检测到 Python
    echo.
    echo   请先安装 Python 3.8 或更高版本：
    echo       https://www.python.org/downloads/
    echo.
    echo   安装时务必勾选 "Add Python to PATH"
    echo   装完后重新双击本文件即可
    echo.
    pause
    exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
if errorlevel 1 echo   [!] Python 版本低于 3.8，可能有兼容问题
echo.

echo   正在启动服务，浏览器会自动打开...
echo   关闭本窗口即可停止服务
echo.
%PY% scripts\serve.py 8766

echo.
echo   服务已停止
pause
