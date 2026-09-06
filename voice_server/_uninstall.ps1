<#
    "cft uninstall" - remove the installed Python environment.

    Only ever touches the directory recorded in venv_path.txt, and only after
    the user confirms the exact path. Downloaded models are left alone: they
    are big, slow to fetch again, and not what "uninstall" is usually after.
#>
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $reset = '' }

& (Join-Path $PSScriptRoot '_banner.ps1') 'Uninstall'

$venvFile = Join-Path $PSScriptRoot 'venv_path.txt'
if (-not (Test-Path $venvFile)) {
    Write-Host "$yellow  Nothing to remove - no installation is recorded.$reset"
    Write-Host ''
    exit 0
}

$venv = (Get-Content $venvFile -Raw).Trim()
# The working directory holds the venv plus the pip cache, temp dir and
# fairseq checkout that setup.bat put beside it.
$work = Split-Path $venv -Parent

# Refuse to act on anything that is not a plausible install directory: a
# drive root or a two-segment system path is a mistake, not a target.
$workTrimmed = $work.TrimEnd('\')
if ($workTrimmed -notmatch '^[A-Za-z]:\\[^\\]+' -or
    [System.IO.Path]::GetPathRoot($workTrimmed).TrimEnd('\') -eq $workTrimmed) {
    Write-Host "$red  Refusing to act on '$work' - that does not look like an install folder.$reset"
    Write-Host ''
    exit 1
}

if (-not (Test-Path $work)) {
    Write-Host "$yellow  Recorded at $work, but that folder is already gone.$reset"
    Remove-Item $venvFile -Force
    Write-Host "$dim  Cleared venv_path.txt.$reset"
    Write-Host ''
    exit 0
}

$size = ''
try {
    $bytes = (Get-ChildItem $work -Recurse -File -Force -EA SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($bytes) { $size = " ($([math]::Round($bytes / 1GB, 1)) GB)" }
} catch {}

Write-Host "$white  This will permanently delete:$reset"
Write-Host "    $work$size"
Write-Host ''
Write-Host "$dim  Character models in models\rvc\ are NOT touched.$reset"
Write-Host ''

# Typing the folder name is deliberate friction: this is several GB, and an
# accidental Enter should not be enough to trigger it.
$leaf = Split-Path $work -Leaf
Write-Host -NoNewline "  Type '$leaf' to confirm, or press Enter to cancel: "
$answer = [Console]::ReadLine()

if ($answer -ne $leaf) {
    Write-Host ''
    Write-Host "$dim  Cancelled - nothing was removed.$reset"
    Write-Host ''
    exit 0
}

Write-Host ''
Write-Host -NoNewline '  Removing... '
try {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction Stop
    Write-Host "${green}done$reset"
} catch {
    Write-Host "${red}failed$reset"
    Write-Host "$dim  $($_.Exception.Message)$reset"
    Write-Host "$yellow  Is the server still running? Stop it and try again.$reset"
    Write-Host ''
    exit 1
}

Remove-Item $venvFile -Force -EA SilentlyContinue
Write-Host ''
Write-Host "$green  Uninstalled. Run 'cft install' to set it up again.$reset"
Write-Host ''
