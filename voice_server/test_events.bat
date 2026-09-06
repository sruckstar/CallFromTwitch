@echo off
REM ===================================================================
REM  CallFromTwitch - fake a donation or a channel-point redemption
REM
REM  Nobody can donate to themselves or buy their own channel points, so
REM  this is the only way to see those paths work before a paying viewer
REM  hits them. The event travels the real route - only its origin is
REM  invented.
REM
REM  Start run_server.bat and the game first, then run this.
REM
REM    test_events.bat                     menu
REM    test_events.bat points "hello"      one redemption
REM    test_events.bat donation 500 "hi"   one donation
REM ===================================================================
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PS=powershell -NoProfile -ExecutionPolicy Bypass"

set "VENV=D:\CallFromTwitch\venv"
if exist "venv_path.txt" set /p VENV=<venv_path.txt

REM The emulator only needs urllib, so the system python will do when the
REM venv is not there - which is the case when someone grabbed the repo to
REM test the mod without installing the voice stack.
set "PY=!VENV!\Scripts\python.exe"
if not exist "!PY!" set "PY=python"

"!PY!" -u test_events.py %*
set "CODE=!ERRORLEVEL!"

if not "!CODE!"=="0" (
    echo.
    pause
)
exit /b !CODE!
