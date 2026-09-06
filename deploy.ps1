<#
.SYNOPSIS
    Builds CallFromTwitch and copies it into GTA V's scripts folder.
.DESCRIPTION
    With no -GtaPath, the game is located by voice_server\_gta_path.ps1: the
    launchers' own registry entries, Steam's library list, the usual folders,
    then a bounded disk scan, and finally by asking. The answer is remembered,
    so this is a one-time question - change it later with "cft where --set".
.EXAMPLE
    .\deploy.ps1
.EXAMPLE
    .\deploy.ps1 -GtaPath "D:\Games\Grand Theft Auto V"
#>
[CmdletBinding()]
param(
    [string]$GtaPath,
    [string]$Configuration = 'Release',
    # Ask which install to use even if one is already remembered.
    [switch]$Ask
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

. (Join-Path $root 'voice_server\_gta_path.ps1')

$GtaPath = Resolve-GtaPath -Path $GtaPath -Force:$Ask -Save

if (-not $GtaPath) {
    Write-Error "GTA V not found. Pass it explicitly: .\deploy.ps1 -GtaPath 'D:\Games\...\Grand Theft Auto V'"
}

Write-Host "GTA V: $GtaPath  ($(Get-GtaEdition $GtaPath))" -ForegroundColor DarkGray

# The mod compiles against iFruit Jailbreak (the incoming-call screen). Prefer
# the copy already deployed in the target game, so the build matches what will
# actually load at runtime; fall back to the one vendored in lib\.
$scripts = Join-Path $GtaPath 'scripts'
$jailbreakCandidates = @(
    (Join-Path $scripts 'iFruit Jailbreak.dll'),
    (Join-Path $root 'lib\iFruit Jailbreak.dll')
)
$jailbreak = $jailbreakCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $jailbreak) {
    Write-Error @'
iFruit Jailbreak.dll not found. It provides the incoming-call screen.
Build it from the iFruitJailbreak repo and drop the DLL into this repo's lib\,
or install it into GTA V\scripts\ first.
'@
}
Write-Host "iFruit Jailbreak: $jailbreak" -ForegroundColor DarkGray

Write-Host "Building ($Configuration)..." -ForegroundColor Cyan
$proj = Join-Path $root 'src\CallFromTwitch\CallFromTwitch.csproj'
& dotnet build $proj -c $Configuration -v minimal "-p:IFruitJailbreakPath=$jailbreak"
if ($LASTEXITCODE -ne 0) { Write-Error 'Build failed.' }

if (-not (Test-Path $scripts)) {
    New-Item -ItemType Directory -Path $scripts | Out-Null
    Write-Host "Created $scripts"
}

$outDir = Join-Path $root "src\CallFromTwitch\bin\$Configuration"

# The script DLL plus the NAudio assemblies it loads at runtime.
$payload = @('CallFromTwitch.dll', 'NAudio.dll', 'NAudio.Core.dll', 'NAudio.WinMM.dll',
             'NAudio.Wasapi.dll', 'NAudio.Asio.dll', 'NAudio.Midi.dll', 'NAudio.WinForms.dll')

foreach ($file in $payload) {
    $src = Join-Path $outDir $file
    if (Test-Path $src) {
        Copy-Item $src -Destination $scripts -Force
        Write-Host "  -> $file" -ForegroundColor Green
    } else {
        Write-Warning "missing: $file"
    }
}

# Never clobber a config the player has already tuned.
$seeds = @('CallFromTwitch.ini')
foreach ($seed in $seeds) {
    $dst = Join-Path $scripts $seed
    if (Test-Path $dst) {
        Write-Host "  -- $seed already present, left untouched" -ForegroundColor DarkGray
    } else {
        Copy-Item (Join-Path $root $seed) -Destination $dst
        Write-Host "  -> $seed" -ForegroundColor Green
    }
}

# An INI from an older version keeps every value the player tuned, but it also
# predates whole sections - and a missing section is silently "all defaults",
# which for [Twitch] means Enabled = false and a feature that looks broken.
# Appending only the sections that are absent adds the new keys without
# touching a single existing line.
$iniPath = Join-Path $scripts 'CallFromTwitch.ini'
$template = Join-Path $root 'CallFromTwitch.ini'
if ((Test-Path $iniPath) -and (Test-Path $template)) {
    $current = Get-Content $iniPath -Raw -Encoding UTF8
    $templateLines = Get-Content $template -Encoding UTF8

    # Section names in the shipped template, in the order they appear.
    $sections = $templateLines |
        Where-Object { $_ -match '^\s*\[(.+)\]\s*$' } |
        ForEach-Object { $Matches[1] }

    foreach ($section in $sections) {
        if ($current -match "(?m)^\s*\[$([regex]::Escape($section))\]\s*$") { continue }

        # Take the section header through to the line before the next one.
        $start = [Array]::FindIndex([string[]]$templateLines,
            [Predicate[string]]{ param($l) $l -match "^\s*\[$([regex]::Escape($section))\]\s*$" })
        $end = [Array]::FindIndex([string[]]$templateLines, $start + 1,
            [Predicate[string]]{ param($l) $l -match '^\s*\[.+\]\s*$' })
        if ($end -lt 0) { $end = $templateLines.Count }

        $block = $templateLines[$start..($end - 1)]
        Add-Content -Path $iniPath -Value (@('') + $block) -Encoding UTF8
        Write-Host "  ++ added missing [$section] to CallFromTwitch.ini" -ForegroundColor Yellow
    }
}

Write-Host "`nDeployed to $scripts" -ForegroundColor Cyan
Write-Host "Start voice_server\run_server.bat before launching the game." -ForegroundColor Yellow
