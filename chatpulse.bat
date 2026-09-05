@echo off
setlocal enabledelayedexpansion

REM ChatPulse 啟動器（Windows）
REM
REM 這個檔案刻意很薄：引導流程的邏輯全部在 scripts\onboard.py，與 macOS/Linux
REM 的 chatpulse.sh 共用同一份。各寫一份必然漂移，而 Windows 那份的坑，
REM 用 macOS 的維護者永遠踩不到——所以這裡只做一件事：找到能跑的 Python。
REM
REM 用法（可直接雙擊，或在命令提示字元執行）：
REM   chatpulse.bat          完整安裝引導（可重複執行）
REM   chatpulse.bat check    只檢查安裝狀態
REM   chatpulse.bat auth     只重新做 Google 授權
REM   chatpulse.bat web      只啟動 Web 儀表板

REM 切成 UTF-8，否則下面與 Python 輸出的中文會變成亂碼
chcp 65001 >nul 2>&1

REM 強制 Python 以 UTF-8 讀寫。chcp 只管 console 顯示，管不到 Python 的
REM stdout 編碼——一旦輸出被導向檔案或 pipe，就會退回 cp950，
REM 那時所有中文與 emoji 會直接拋 UnicodeEncodeError 讓程式中止。
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
set "ONBOARD=%BASE_DIR%\scripts\onboard.py"
set "VENV_PYTHON=%BASE_DIR%\.venv\Scripts\python.exe"

REM 判斷是不是被雙擊執行的：是的話結尾要停住，否則視窗會直接關掉，
REM 使用者連錯誤訊息都看不到
set "DOUBLE_CLICKED="
echo %CMDCMDLINE% | find /i "/c" >nul 2>&1 && set "DOUBLE_CLICKED=1"

REM 1) 專案自己的環境優先——版本正確且套件都裝好了
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" "%ONBOARD%" %*
    set "EXITCODE=!ERRORLEVEL!"
    goto :finish
)

REM 2) 還沒建環境時，找一個系統 Python 來跑引導（它會負責建 .venv）
REM    py.exe 是 Windows 的 Python Launcher，最可靠，優先用它指定版本
set "PYBIN="
for %%V in (3.12 3.13) do (
    if not defined PYBIN (
        py -%%V -c "import sys" >nul 2>&1 && set "PYBIN=py -%%V"
    )
)
if not defined PYBIN (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYBIN=py -3"
)
if not defined PYBIN (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYBIN=python"
)

if not defined PYBIN goto :nopython

%PYBIN% "%ONBOARD%" %*
set "EXITCODE=!ERRORLEVEL!"
goto :finish

:nopython
echo.
echo   [X] 找不到可用的 Python（需要 3.10 以上，建議 3.12）
echo.
echo       請到 https://www.python.org/downloads/ 下載安裝。
echo.
echo       安裝時務必勾選 "Add python.exe to PATH"（在安裝畫面最下方），
echo       否則裝完這裡還是找不到它。
echo.
echo       裝好之後重新執行：chatpulse.bat
echo.
set "EXITCODE=1"

:finish
if defined DOUBLE_CLICKED (
    echo.
    pause
)
endlocal & exit /b %EXITCODE%
