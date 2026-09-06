<#
    Closing screen for setup.bat: what was installed and what to do next.
    Split out of the batch file because coloured multi-line output is far
    easier to keep readable here than in escaped `echo` lines.
#>
param(
    [string]$Venv = '',
    [string]$Work = ''
)

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$esc = [char]27
$green = "$esc[92m"; $dim = "$esc[90m"; $white = "$esc[97m"
$orange = "$esc[38;5;214m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $dim = $white = $orange = $reset = '' }

$modelsDir = Join-Path $PSScriptRoot 'models\rvc'
$rvcModels = @()
if (Test-Path $modelsDir) {
    # Both layouts the engine accepts: <name>.pth, or <Character>\<any>.pth.
    $rvcModels += Get-ChildItem $modelsDir -Filter *.pth -File -ErrorAction SilentlyContinue |
        ForEach-Object { $_.BaseName }
    $rvcModels += Get-ChildItem $modelsDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { Get-ChildItem $_.FullName -Filter *.pth -File -ErrorAction SilentlyContinue } |
        ForEach-Object { $_.Name }
}
$rvcModels = $rvcModels | Sort-Object -Unique

Write-Host ''
Write-Host "$green  Setup complete.$reset"
Write-Host ''
if ($Venv) {
    $sizeGb = 0
    if ($Work -and (Test-Path $Work)) {
        try {
            $bytes = (Get-ChildItem $Work -Recurse -File -Force -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
            $sizeGb = [math]::Round($bytes / 1GB, 1)
        } catch {}
    }
    $suffix = if ($sizeGb -gt 0) { "  ${dim}($sizeGb GB)$reset" } else { '' }
    Write-Host "$dim  Installed to:$reset $Venv$suffix"
}
Write-Host ''

if ($rvcModels.Count -gt 0) {
    Write-Host "$white  Character voices found:$reset " -NoNewline
    Write-Host ($rvcModels -join ', ')
    Write-Host ''
    Write-Host "$white  Next:$reset  run_server.bat   $dim(leave the window open while playing)$reset"
} else {
    # Without a model the mod still works, so this is guidance and not an error.
    Write-Host "$white  Next: add a character voice$reset"
    Write-Host "$dim    Retry the download:  cft voices   (links live in voices.json)$reset"
    Write-Host "$dim    Or by hand: drop models into models\rvc\ as <Name>\<model>.pth$reset"
    Write-Host "$dim    More ready-made GTA V voices: weights.gg  or the AI Hub Discord$reset"
    Write-Host ''
    Write-Host "$dim    Without one the mod still runs - calls just use the plain$reset"
    Write-Host "$dim    Piper voice, which is fine for a first test.$reset"
    Write-Host ''
    Write-Host "$white  Then:$reset  run_server.bat   $dim(leave the window open while playing)$reset"
}
Write-Host ''
Write-Host "$orange  Deploy the mod itself with deploy.ps1 in the repo root.$reset"
Write-Host ''