@echo off
setlocal enabledelayedexpansion

REM ============================================================================
REM  ChatPulse launcher (Windows)
REM
REM  *** THIS FILE MUST STAY PURE ASCII. DO NOT ADD CHINESE TEXT OR EMOJI. ***
REM
REM  Why: cmd.exe remembers its position in a batch file as a BYTE offset, but
REM  decodes the bytes using the CURRENT code page. The `chcp 65001` below
REM  switches that code page mid-file, so every byte after it is re-decoded as
REM  UTF-8. Any multi-byte character (Chinese is 3 bytes in UTF-8) shifts the
REM  character boundaries, the read position lands in the MIDDLE of a character,
REM  and cmd starts executing the second half of a comment line as a command.
REM
REM  Reported 2026-09-06 on a real Windows machine. The visible symptom was:
REM      'cp950,' is not recognized as an internal or external command
REM  which is the tail of a Chinese REM line that used to sit right below chcp.
REM  ASCII bytes are identical in cp950 and UTF-8, so ASCII-only cannot drift.
REM
REM  Chinese explanations for this file live in docs/HANDOFF.md, not here.
REM ============================================================================
REM
REM  This launcher is deliberately thin: the whole onboarding flow lives in
REM  scripts\onboard.py, shared with chatpulse.sh on macOS/Linux. Keeping two
REM  copies guarantees drift, and the Windows-only potholes are exactly the ones
REM  a macOS maintainer never steps in. So this file does one thing: find a
REM  usable Python and hand over.
REM
REM  Usage (double-click, or run from a command prompt):
REM    chatpulse.bat          full guided install (safe to re-run)
REM    chatpulse.bat check    check install status only
REM    chatpulse.bat auth     redo the Google authorisation only
REM    chatpulse.bat web      start the web dashboard only
REM    chatpulse.bat web --dev  ... with auto-reload for development

REM Switch the console to UTF-8, otherwise the Chinese that Python prints
REM below turns into garbage. Everything after this line must be ASCII.
chcp 65001 >nul 2>&1

REM Force Python itself to read and write UTF-8. chcp only covers what the
REM console displays; it does not reach Python's own stdout encoding. Once the
REM output is redirected to a file or a pipe, Python falls back to the legacy
REM code page and every Chinese character raises UnicodeEncodeError, which
REM kills the process outright.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "BASE_DIR=%~dp0"
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"
set "ONBOARD=%BASE_DIR%\scripts\onboard.py"
set "VENV_PYTHON=%BASE_DIR%\.venv\Scripts\python.exe"

REM Detect a double-click: if so, hold the window open at the end, otherwise it
REM vanishes and the user never gets to read the error. Both conditions matter
REM - a plain `cmd /c something-else` also carries /c, but only a double-click
REM puts this file's own name on the command line.
REM Kept as two flat probes plus a nested `if defined`: a `&&` followed by a
REM parenthesised block containing a pipe is exactly the kind of cmd parsing
REM corner that cannot be tested from macOS. The quotes around %CMDCMDLINE%
REM matter too - an unquoted `&` in the path would split the echo itself.
set "DOUBLE_CLICKED="
set "_HAS_SLASH_C="
set "_HAS_SELF="
echo "%CMDCMDLINE%" | find /i "/c" >nul 2>&1 && set "_HAS_SLASH_C=1"
echo "%CMDCMDLINE%" | find /i "%~nx0" >nul 2>&1 && set "_HAS_SELF=1"
if defined _HAS_SLASH_C if defined _HAS_SELF set "DOUBLE_CLICKED=1"

REM 1) Prefer the project's own environment - right version, packages present.
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" "%ONBOARD%" %*
    set "EXITCODE=!ERRORLEVEL!"
    goto :finish
)

REM 2) No environment yet: find any system Python to run the onboarding, which
REM    is what creates .venv. py.exe (the Python Launcher) is the most reliable
REM    way to ask for a specific version, so try it first.
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
REM ASCII only here too - see the header. The Chinese version of this message
REM is in SETUP_GUIDE.md (Q7), which is where the docs point people anyway.
echo.
echo   [X] No usable Python found (need 3.10 or newer, 3.12 recommended)
echo.
echo       Download it here:  https://www.python.org/downloads/
echo.
echo       IMPORTANT: tick "Add python.exe to PATH" near the bottom of the
echo       installer screen. Without it this script still cannot find Python.
echo.
echo       Then run chatpulse.bat again.
echo.
echo       Chinese instructions: see SETUP_GUIDE.md, section Q7.
echo.
set "EXITCODE=1"

:finish
if defined DOUBLE_CLICKED (
    echo.
    pause
)
endlocal & exit /b %EXITCODE%
