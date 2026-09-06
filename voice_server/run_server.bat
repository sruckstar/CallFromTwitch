@echo off
REM ===================================================================
REM  CallFromTwitch - voice server
REM  Leave this window open while playing. Ctrl+C stops it.
REM
REM    run_server.bat              start normally
REM    run_server.bat --verbose    show full logs (for diagnosing problems)
REM ===================================================================
REM itself stay pure ASCII - chcp re-decodes the lines that follow it).
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PS=powershell -NoProfile -ExecutionPolicy Bypass"

set "VENV=D:\CallFromTwitch\venv"
if exist "venv_path.txt" set /p VENV=<venv_path.txt

if not exist "!VENV!\Scripts\python.exe" (
    %PS% -File "_banner.ps1" "Voice server"
    %PS% -Command "$e=[char]27; Write-Host ($e + '[91m  ! No installation found at !VENV!' + $e + '[0m')"
    echo.
    echo      Run setup.bat first.
    echo      If you moved the folder, delete venv_path.txt and run setup.bat again.
    echo.
    pause
    exit /b 1
)

REM --verbose keeps every log line; the default run is deliberately quiet, so
REM the window shows the call activity rather than framework chatter.
if /i "%~1"=="--verbose" (
    set "CFT_QUIET="
) else (
    set "CFT_QUIET=1"
)

REM The window is named as early as possible: the player alt-tabs away while
REM the models load, and an unnamed "Windows PowerShell" among ten others is
REM no help. server.py renames it again once it knows the voice and port.
title CallFromTwitch - starting...

%PS% -File "_banner.ps1" "Voice server"

"!VENV!\Scripts\python.exe" -u server.py
set "CODE=!ERRORLEVEL!"

title CallFromTwitch - stopped

REM Exit code 0 also covers Ctrl+C, which is the normal way to stop this.
if not "!CODE!"=="0" (
    echo.
    %PS% -Command "$e=[char]27; Write-Host ($e + '[91m  ! The server stopped with an error (code !CODE!).' + $e + '[0m')"
    echo.
    echo      Run  run_server.bat --verbose  to see the full output.
    echo.
    pause
)
exit /b !CODE!