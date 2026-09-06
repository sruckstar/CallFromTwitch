@echo off
REM ===================================================================
REM  CallFromTwitch - installer
REM
REM  Creates the Python 3.10 venv, installs PyTorch/Piper/RVC and fetches
REM  the reference Piper voice. Run it once.
REM
REM  The venv lands OUTSIDE this folder on purpose: it weighs ~6 GB, which
REM  belongs neither in OneDrive nor on a full system drive. Where exactly
REM  is asked on the first run and remembered in venv_path.txt.
REM
REM    setup.bat                  ask where to install (or reuse the answer)
REM    setup.bat D:\some\venv     install there without asking
REM    setup.bat --reinstall      ask again even if a path is remembered
REM ===================================================================
REM UTF-8 so a repo path with non-ASCII characters survives (this file must
REM itself stay pure ASCII - chcp re-decodes the lines that follow it).
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PS=powershell -NoProfile -ExecutionPolicy Bypass"
REM Full pip/build output goes here; the console only shows progress.
set "LOG=%~dp0setup.log"
> "%LOG%" echo CallFromTwitch setup - %DATE% %TIME%

%PS% -File "_banner.ps1" "Installer"

REM ---------- Python 3.10 ----------
REM Checked before anything else: without it nothing below can run.
set "PY310=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not exist "%PY310%" (
    for /f "delims=" %%q in ('py -3.10 -c "import sys;print(sys.executable)" 2^>nul') do set "PY310=%%q"
)
if not exist "%PY310%" (
    call :err "Python 3.10 is required and was not found."
    echo      Install it:  winget install Python.Python.3.10
    echo.
    echo      3.11+ will NOT work: rvc-python needs fairseq, which cannot build there.
    echo      Installing 3.10 does not affect any other Python you have.
    goto :abort
)

REM ---------- Where to install ----------
set "VENV="
set "ASK=1"
if "%~1"=="--reinstall" (
    set "ASK=1"
) else if not "%~1"=="" (
    set "VENV=%~1"
    set "ASK=0"
) else if exist "venv_path.txt" (
    set /p VENV=<venv_path.txt
    set "ASK=0"
)

if "!ASK!"=="1" (
    for /f "usebackq delims=" %%p in (`%PS% -File "_pick_dir.ps1" -NeedGb 12`) do set "VENV=%%p"
    if not defined VENV (
        call :err "Cancelled - nothing was installed."
        goto :abort
    )
    REM The picker returns the working directory; the venv sits inside it.
    set "WORK=!VENV!"
    set "VENV=!VENV!\venv"
) else (
    REM A remembered or explicitly passed path always names the venv itself.
    for %%w in ("!VENV!\..") do set "WORK=%%~fw"
    call :info "Installing to !VENV!"
)

if not exist "!WORK!" mkdir "!WORK!" 2>nul
set "VPY=!VENV!\Scripts\python.exe"

REM pip unpacks multi-GB wheels through TEMP; keep that off a full system drive.
set "TEMP=!WORK!\tmp"
set "TMP=!WORK!\tmp"
if not exist "!TEMP!" mkdir "!TEMP!"
set "CACHE=!WORK!\pipcache"

echo.
call :step 1 7 "Creating the Python 3.10 environment"
if exist "!VPY!" (
    call :skip "already there"
) else (
    "%PY310%" -m venv "!VENV!" >>"%LOG%" 2>&1 || goto :failed_step
    call :done
)

call :step 2 7 "Pinning pip (rvc-python needs pip<24.1)"
REM rvc-python requires omegaconf==2.0.6, whose metadata newer pip rejects.
"!VPY!" -m pip install "pip==23.3.2" >>"%LOG%" 2>&1 || goto :failed_step
call :done

call :step 3 7 "Installing PyTorch with CUDA (~2.5 GB, several minutes)"
"!VPY!" -m pip install torch==2.1.1+cu118 torchaudio==2.1.1+cu118 --index-url https://download.pytorch.org/whl/cu118 --cache-dir "!CACHE!" >>"%LOG%" 2>&1 || goto :failed_step
call :done

call :step 4 7 "Building fairseq from source (several minutes)"
REM Skip the whole compiler dance when fairseq already imports: a re-run
REM should not demand MSVC just to confirm what is already installed.
"!VPY!" -c "import fairseq" >nul 2>&1
if not errorlevel 1 (
    call :skip "already there"
    goto :after_fairseq
)
REM fairseq (via rvc-python) has no Windows wheel and compiles a C extension,
REM so the MSVC environment has to be on PATH before pip runs.
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
set "VCVARS="
if exist "!VSWHERE!" (
    for /f "usebackq tokens=*" %%i in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`) do (
        if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars64.bat"
    )
)
if not defined VCVARS (
    call :failed_quiet
    call :err "No MSVC C++ compiler found - fairseq cannot build."
    echo      Install "Desktop development with C++" from:
    echo      https://visualstudio.microsoft.com/visual-cpp-build-tools/
    goto :abort
)
call "!VCVARS!" >>"%LOG%" 2>&1
REM torch's cpp_extension aborts when vcvars is already active unless this is
REM set, to stop distutils activating the VC environment a second time.
set "DISTUTILS_USE_SDK=1"

where git >nul 2>&1
if errorlevel 1 (
    call :failed_quiet
    call :err "git is required to build fairseq and was not found."
    echo      Install it:  winget install Git.Git
    goto :abort
)

REM fairseq's setup.py imports torch, so it cannot build in pip's isolated
REM environment. Install its build deps against the venv that already has torch.
"!VPY!" -m pip install "setuptools<70" wheel cython >>"%LOG%" 2>&1 || goto :failed_step
"!VPY!" -m pip install numpy==1.23.5 --cache-dir "!CACHE!" >>"%LOG%" 2>&1 || goto :failed_step

REM Cloned here rather than letting pip do it: pip clones into a temp dir that
REM git may reject as "dubious ownership", failing before the compiler runs.
REM The git tag rather than PyPI on purpose: the 0.12.2 sdist omits a .cpp file
REM that its own setup.py compiles, so the PyPI build always fails.
if not exist "!WORK!\fairseq-src\setup.py" (
    git clone --depth 1 --branch v0.12.2 https://github.com/facebookresearch/fairseq.git "!WORK!\fairseq-src" >>"%LOG%" 2>&1 || goto :failed_step
)
REM setup.py symlinks examples/ into the package, which needs admin rights on
REM Windows. Copying the directory first makes it skip that step.
if not exist "!WORK!\fairseq-src\fairseq\examples" (
    xcopy /E /I /Q /Y "!WORK!\fairseq-src\examples" "!WORK!\fairseq-src\fairseq\examples" >>"%LOG%" 2>&1 || goto :failed_step
)
"!VPY!" -m pip install "!WORK!\fairseq-src" --no-build-isolation --no-deps --cache-dir "!CACHE!" >>"%LOG%" 2>&1 || goto :failed_step
"!VPY!" -m pip install hydra-core==1.0.7 omegaconf==2.0.6 bitarray sacrebleu regex tqdm cffi --cache-dir "!CACHE!" >>"%LOG%" 2>&1 || goto :failed_step
call :done

:after_fairseq
call :step 5 7 "Installing Piper, RVC and the web server"
"!VPY!" -m pip install -r requirements.txt --cache-dir "!CACHE!" >>"%LOG%" 2>&1 || goto :failed_step
call :done

call :step 6 7 "Downloading the Piper reference voice"
if exist "models\piper\en_US-ryan-high.onnx" (
    call :skip "already there"
) else (
    if not exist "models\piper" mkdir "models\piper"
    REM Straight from the official Piper voice repository on Hugging Face, so
    REM the ~110 MB model never has to live in git.
    REM American English on purpose: RVC transfers timbre but keeps the carrier's
    REM accent, so an en_US reference is what makes character models sound native.
    "!VPY!" -m piper.download_voices en_US-ryan-high --data-dir "models\piper" >>"%LOG%" 2>&1 || goto :failed_step
    call :done
)

echo   [7/7] Downloading the GTA V character voices
REM Progress bars go to the console rather than the log, so a several-hundred-MB
REM download does not look like a hang. Links live in voices.json.
REM Never fatal: without a character voice the mod still runs on plain Piper,
REM so a dead link must not undo an otherwise finished install.
"!VPY!" download_voices.py

REM Remembered so run_server.bat and any later setup find the same environment.
> venv_path.txt echo !VENV!

%PS% -File "_done.ps1" -Venv "!VENV!" -Work "!WORK!"
pause
exit /b 0

REM ---------- helpers ----------
REM Each writes one short line so the console stays readable; the verbose
REM output of every command above is in setup.log instead.

:step
<nul set /p "=  [%~1/%~2] %~3 ... "
exit /b 0

:done
%PS% -Command "$e=[char]27; Write-Host ($e + '[92mok' + $e + '[0m')"
exit /b 0

:skip
%PS% -Command "$e=[char]27; Write-Host ($e + '[90m%~1' + $e + '[0m')"
exit /b 0

:failed_quiet
%PS% -Command "$e=[char]27; Write-Host ($e + '[91mfailed' + $e + '[0m')"
exit /b 0

:info
%PS% -Command "$e=[char]27; Write-Host ($e + '[90m  %~1' + $e + '[0m')"
exit /b 0

:err
%PS% -Command "$e=[char]27; Write-Host ($e + '[91m  ! %~1' + $e + '[0m')"
exit /b 0

:failed_step
call :failed_quiet
echo.
call :err "Setup did not finish."
echo      Full log: %LOG%
echo      Last lines:
echo.
%PS% -Command "Get-Content '%LOG%' -Tail 15 | ForEach-Object { '       ' + $_ }"
echo.
pause
exit /b 1

:abort
echo.
pause
exit /b 1