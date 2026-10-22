@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title PriceHub
cd /d "%~dp0"

echo ==========================================
echo   PriceHub 价格管理工作台（Windows 版）
echo ==========================================

rem ---------- 1. 查找 Python 3.10 及以上 ----------
set "PY="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
    if !errorlevel! equ 0 set "PY=%%~C"
  )
)
if not defined PY (
  echo.
  echo [需要安装 Python] 没有找到 Python 3.10 或更高版本（64 位）。
  echo.
  echo  安装方法：
  echo   1. 打开 https://www.python.org/downloads/windows/
  echo      （国内较慢时可用镜像 https://mirrors.huaweicloud.com/python/ ）
  echo   2. 下载 "Windows installer (64-bit)"，推荐 Python 3.12
  echo   3. 安装时务必勾选最下面的 "Add python.exe to PATH"
  echo   4. 安装完成后，重新双击本文件 启动PriceHub.bat
  echo.
  echo  提示：如果在命令行输入 python 会打开微软商店，说明只是商店占位程序，
  echo        请按上面的方法安装正式版 Python。
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%V in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%V"
echo 使用 Python !PYVER!（%PY%）

rem ---------- 2. 首次运行：创建虚拟环境并安装依赖 ----------
if not exist ".venv\Scripts\python.exe" (
  echo 首次运行：正在创建虚拟环境 .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo 创建虚拟环境失败。请确认 Python 安装完整（包含 venv），或以管理员身份重试。
    pause
    exit /b 1
  )
)
set "VPY=.venv\Scripts\python.exe"
rem 若缺少桌面窗口依赖，强制重装一次
"%VPY%" -c "import webview" >nul 2>&1
if errorlevel 1 if exist ".venv\.deps_ok" del ".venv\.deps_ok"
if not exist ".venv\.deps_ok" (
  echo 正在安装依赖（只需一次，约 1–5 分钟）...
  "%VPY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60 >nul 2>&1
  "%VPY%" -m pip install -r requirements-windows.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60
  if errorlevel 1 (
    echo 清华镜像安装失败，改用默认源 pypi.org 重试 ...
    "%VPY%" -m pip install -r requirements-windows.txt --timeout 60
    if errorlevel 1 (
      echo.
      echo 依赖安装失败。请检查网络后重新双击 启动PriceHub.bat。
      echo 如使用公司代理，可先在命令行设置：set HTTPS_PROXY=http://代理地址:端口
      pause
      exit /b 1
    )
  )
  echo ok> ".venv\.deps_ok"
)

rem ---------- 3. 启动（127.0.0.1:8800，SQLite）并打开桌面窗口 ----------
set "PRICEHUB_DB=%CD%\data\pricehub.db"
set "PRICEHUB_DB_URL="
set "PRICEHUB_QUEUE=memory"
echo 正在启动 PriceHub 桌面窗口：http://127.0.0.1:8800/  （关闭本窗口即停止）
"%VPY%" run_windows.py
if errorlevel 1 pause
endlocal
