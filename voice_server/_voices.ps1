<#
    "cft voices" - install or re-install the GTA character voices.

    A thin wrapper over download_voices.py so the models can be fetched without
    re-running the whole installer: a link that was dead on install day, a voice
    added to voices.json later, or a one-off link shared by hand.

      cft voices                 download everything listed in voices.json
      cft voices Michael         just one
      cft voices --list          what is available and what is installed
      cft voices --force Trevor  download again over an existing voice
      cft voices --url <link> Lamar    install a voice the manifest lacks
#>
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args = @())

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$esc = [char]27
$dim = "$esc[90m"; $red = "$esc[91m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $dim = $red = $reset = '' }

& (Join-Path $PSScriptRoot '_banner.ps1') 'Character voices'

# The venv is preferred for consistency, but this script only needs the standard
# library - so any Python will do, and a missing venv is no reason to refuse.
$python = ''
$venvFile = Join-Path $PSScriptRoot 'venv_path.txt'
if (Test-Path $venvFile) {
    $candidate = Join-Path (Get-Content $venvFile -Raw).Trim() 'Scripts\python.exe'
    if (Test-Path $candidate) { $python = $candidate }
}
if (-not $python) {
    $fallback = "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    if (Test-Path $fallback) { $python = $fallback }
    else { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
if (-not $python) {
    Write-Host "$red  No Python found - run: cft install$reset"
    exit 1
}

& $python (Join-Path $PSScriptRoot 'download_voices.py') @Args
$code = $LASTEXITCODE

Write-Host ''
Write-Host "$dim  Voices live in: $(Join-Path $PSScriptRoot 'models\rvc')$reset"
Write-Host "$dim  Any RVC model works - drop <Name>\<model>.pth in there by hand too.$reset"
Write-Host ''
exit $code
