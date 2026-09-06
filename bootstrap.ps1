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

.NOTES
    Nothing here needs admin rights; everything is per-user.
#>
[CmdletBinding()]
param(
    # Where to clone. Defaults to a CallFromTwitch folder in the current directory.
    [string]$Path,
    [string]$Repo = 'https://github.com/sruckstar/CallFromTwitch.git',
    [string]$Branch = 'main',
    # Run "cft install" straight after, instead of leaving it to the user.
    [switch]$WithServer
)

$ErrorActionPreference = 'Stop'

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $reset = '' }

# PowerShell 5.1 wraps a native command's stderr in ErrorRecords, so with
# $ErrorActionPreference = 'Stop' even a harmless git warning ("--depth is
# ignored in local clones") becomes a terminating error. Git is therefore run
# with the preference relaxed, and judged only by its exit code.
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments)][string[]]$GitArgs)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & git @GitArgs 2>&1 | Out-Null; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $old }
}
function Fail { param([string]$Text)
    Write-Host ''
    Write-Host "$red  ! $Text$reset"
    Write-Host ''
    exit 1
}

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

# $PWD rather than [Environment]::CurrentDirectory: piped through iex there is
# no script file, and only $PWD tracks where the user actually is.
if (-not $Path) { $Path = Join-Path $PWD 'CallFromTwitch' }
$Path = [System.IO.Path]::GetFullPath($Path)

# OneDrive would sync the repo and, worse, whatever ends up beside it. Warn but
# let the user decide: some people keep their whole dev tree there on purpose.
if ($Path -like "*$([System.IO.Path]::DirectorySeparatorChar)OneDrive*") {
    Write-Host "$yellow  Note: $Path is inside OneDrive, which will sync it.$reset"
    Write-Host "$dim  The voice server's several GB are installed elsewhere, so this is$reset"
    Write-Host "$dim  only the source - but a folder outside OneDrive is still tidier.$reset"
    Write-Host ''
}

if (Test-Path (Join-Path $Path '.git')) {
    Write-Host "  Already cloned at $Path"
    Write-Host -NoNewline '  Updating... '
    try {
        if ((Invoke-Git -C $Path pull --ff-only --quiet) -ne 0) { throw "pull failed" }
        Write-Host "${green}done$reset"
    } catch {
        # A dirty tree or a diverged branch is the user's to resolve; the
        # existing clone is still perfectly usable for installing the command.
        Write-Host "${yellow}skipped$reset"
        Write-Host "$dim  Could not fast-forward - carrying on with what is there.$reset"
    }
} elseif (Test-Path $Path) {
    $hasFiles = (Get-ChildItem $Path -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0
    if ($hasFiles) {
        Fail "$Path already exists and is not a CallFromTwitch clone. Move it, or pass -Path somewhere else."
    }
    Write-Host -NoNewline "  Cloning into $Path ... "
    if ((Invoke-Git clone --branch $Branch --depth 1 --quiet $Repo $Path) -ne 0) {
        Fail "git clone failed. Check the URL and your connection."
    }
    Write-Host "${green}done$reset"
} else {
    Write-Host -NoNewline "  Cloning into $Path ... "
    if ((Invoke-Git clone --branch $Branch --depth 1 --quiet $Repo $Path) -ne 0) {
        Fail "git clone failed. Check the URL and your connection."
    }
    Write-Host "${green}done$reset"
}

$installer = Join-Path $Path 'install.ps1'
if (-not (Test-Path $installer)) { Fail "install.ps1 is missing from the clone at $Path." }

# Hand over to the real installer, which owns the shim and the PATH entry.
& $installer

if ($WithServer) {
    Write-Host ''
    Write-Host "$white  Continuing with the voice server...$reset"
    Write-Host ''
    & (Join-Path $Path 'cft.cmd') 'install'
    exit $LASTEXITCODE
}

Write-Host "$white  Repository:$reset $Path"
Write-Host ''
Write-Host "$white  Next step - install the voice server (several GB, asks where):$reset"
Write-Host ''
Write-Host "    cft install"
Write-Host ''
Write-Host "$dim  In this window 'cft' works right away; elsewhere, open a new terminal.$reset"
Write-Host ''
