<#
.SYNOPSIS
    One-command install for CallFromTwitch: clone the repo, then set up "cft".

.DESCRIPTION
    Meant to be piped straight from GitHub:

        irm https://raw.githubusercontent.com/sruckstar/CallFromTwitch/main/bootstrap.ps1 | iex

    Clones into .\CallFromTwitch in the current folder (like a plain git clone),
    then hands over to install.ps1, which puts the "cft" command on PATH.
    It deliberately stops there: "cft install" downloads several GB and asks
    where to put them, so it stays a separate, explicit step.

    Re-running is safe - an existing clone is updated with git pull.

    Piped through iex there is no script file and no parameter binding: PS 5.1
    rejects [CmdletBinding()] and param() inside Invoke-Expression outright.
    So the knobs are environment variables instead, set before the pipe:

        $env:CFT_PATH = 'D:\dev\CallFromTwitch'
        $env:CFT_WITH_SERVER = '1'
        irm .../bootstrap.ps1 | iex

    CFT_PATH, CFT_REPO, CFT_BRANCH, CFT_WITH_SERVER, CFT_NO_COLOR.

.NOTES
    Nothing here needs admin rights; everything is per-user.
#>

$ErrorActionPreference = 'Stop'

$cftPath = $env:CFT_PATH
$cftRepo = if ($env:CFT_REPO) { $env:CFT_REPO } else { 'https://github.com/sruckstar/CallFromTwitch.git' }
$cftBranch = if ($env:CFT_BRANCH) { $env:CFT_BRANCH } else { 'main' }
$cftWithServer = [bool]$env:CFT_WITH_SERVER

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $reset = '' }

function Invoke-Git {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & git @args 2>&1 | Out-Null; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
}

function Fail {
    param([string]$Text)
    throw $Text
}

try {

    Write-Host ''
    Write-Host "$white  CallFromTwitch$reset $dim- fetching the repository$reset"
    Write-Host ''

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail @'
git is required and was not found.
    Install it:  winget install Git.Git
    Then open a new terminal and run this command again.
'@
    }

    if (-not $cftPath) { $cftPath = Join-Path $PWD 'CallFromTwitch' }
    $cftPath = [System.IO.Path]::GetFullPath($cftPath)

    if ($cftPath -like "*$([System.IO.Path]::DirectorySeparatorChar)OneDrive*") {
        Write-Host "$yellow  Note: $cftPath is inside OneDrive, which will sync it.$reset"
        Write-Host "$dim  The voice server's several GB are installed elsewhere, so this is$reset"
        Write-Host "$dim  only the source - but a folder outside OneDrive is still tidier.$reset"
        Write-Host ''
    }

    if (Test-Path (Join-Path $cftPath '.git')) {
        Write-Host "  Already cloned at $cftPath"
        Write-Host -NoNewline '  Updating... '
        try {
            if ((Invoke-Git -C $cftPath pull --ff-only --quiet) -ne 0) { throw "pull failed" }
            Write-Host "${green}done$reset"
        } catch {
            Write-Host "${yellow}skipped$reset"
            Write-Host "$dim  Could not fast-forward - carrying on with what is there.$reset"
        }
    } else {
        if (Test-Path $cftPath) {
            $hasFiles = (Get-ChildItem $cftPath -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
            if ($hasFiles) {
                Fail "$cftPath already exists and is not a CallFromTwitch clone. Move it, or set `$env:CFT_PATH to somewhere else."
            }
        }
        Write-Host -NoNewline "  Cloning into $cftPath ... "
        if ((Invoke-Git clone --branch $cftBranch --depth 1 --quiet $cftRepo $cftPath) -ne 0) {
            Fail "git clone failed. Check the URL and your connection."
        }
        Write-Host "${green}done$reset"
    }

    $installer = Join-Path $cftPath 'install.ps1'
    if (-not (Test-Path $installer)) { Fail "install.ps1 is missing from the clone at $cftPath." }

    & $installer

    if ($cftWithServer) {
        Write-Host ''
        Write-Host "$white  Continuing with the voice server...$reset"
        Write-Host ''
        & (Join-Path $cftPath 'cft.cmd') 'install'
    } else {
        Write-Host "$white  Repository:$reset $cftPath"
        Write-Host ''
        Write-Host "$white  Next step - install the voice server (several GB, asks where):$reset"
        Write-Host ''
        Write-Host "    cft install"
        Write-Host ''
        Write-Host "$dim  In this window 'cft' works right away; elsewhere, open a new terminal.$reset"
        Write-Host ''
    }

} catch {
    Write-Host ''
    Write-Host "$red  ! $($_.Exception.Message)$reset"
    Write-Host ''
}
