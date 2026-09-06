@echo off
REM ===================================================================
REM  CallFromTwitch - one command for everything.
REM
REM    cft install     set up the voice server (asks where to install)
REM    cft start       run the voice server
REM    cft deploy      build the mod and copy it into GTA V
REM    cft config      change the mod's settings
REM    cft voices      download the GTA character voices
REM    cft where       show or change which GTA V it deploys into
REM    cft status      show what is installed and whether the server responds
REM    cft uninstall   remove the installed environment
REM
REM  Bare "cft" prints this list.
REM ===================================================================
REM itself stay pure ASCII - chcp re-decodes the lines that follow it).
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PS=powershell -NoProfile -ExecutionPolicy Bypass"
set "SRV=%~dp0voice_server"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=help"

REM Everything after the command word, forwarded verbatim. %* keeps the
REM command itself, so it is stripped here; a plain `shift` would not help
REM because %* ignores it. This is what lets `cft config Channel my name`
REM arrive intact rather than truncated at the third word.
set "REST=%*"
if defined REST (
    call set "REST=%%REST:*%1=%%"
)

if /i "%CMD%"=="install"   goto :install
if /i "%CMD%"=="setup"     goto :install
if /i "%CMD%"=="start"     goto :start
if /i "%CMD%"=="run"       goto :start
if /i "%CMD%"=="server"    goto :start
if /i "%CMD%"=="deploy"    goto :deploy
if /i "%CMD%"=="config"    goto :config
if /i "%CMD%"=="settings"  goto :config
if /i "%CMD%"=="voices"    goto :voices
if /i "%CMD%"=="voice"     goto :voices
if /i "%CMD%"=="where"     goto :where
if /i "%CMD%"=="gta"       goto :where
if /i "%CMD%"=="status"    goto :status
if /i "%CMD%"=="uninstall" goto :uninstall
if /i "%CMD%"=="help"      goto :help
if /i "%CMD%"=="--help"    goto :help
if /i "%CMD%"=="-h"        goto :help

%PS% -File "%SRV%\_banner.ps1" "Unknown command: %CMD%"
goto :help_body

:install
call "%SRV%\setup.bat" %REST%
exit /b %ERRORLEVEL%

:start
call "%SRV%\run_server.bat" %REST%
exit /b %ERRORLEVEL%

:deploy
%PS% -File "%~dp0deploy.ps1" %REST%
exit /b %ERRORLEVEL%

:config
%PS% -File "%SRV%\_config.ps1" %REST%
exit /b %ERRORLEVEL%

:voices
%PS% -File "%SRV%\_voices.ps1" %REST%
exit /b %ERRORLEVEL%

:where
%PS% -File "%SRV%\_where.ps1" %REST%
exit /b %ERRORLEVEL%

:status
%PS% -File "%SRV%\_status.ps1"
exit /b %ERRORLEVEL%

:uninstall
%PS% -File "%SRV%\_uninstall.ps1"
exit /b %ERRORLEVEL%

:help
%PS% -File "%SRV%\_banner.ps1" "Call From Twitch"
:help_body
echo   Usage:  cft ^<command^>
echo.
echo     install     set up the voice server ^(asks where to install^)
echo     start       run the voice server ^(keep the window open while playing^)
echo     deploy      build the mod and copy it into GTA V
echo     config      change the mod's settings ^(channel, voice, call...^)
echo     voices      download the GTA character voices ^(--list to see them^)
echo     where       show or change which GTA V it deploys into
echo     status      show what is installed and whether the server responds
echo     uninstall   remove the installed environment
echo.
echo   First time:   cft install  -^>  cft deploy  -^>  cft config
echo   Every time:   cft start
echo.
exit /b 0
