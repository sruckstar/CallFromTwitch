<#
    "cft status" - what is installed, and is it working?
    Written to answer the questions asked when something is wrong, in order:
    is the environment there, are there voices, is the server up, is the mod
    deployed.
#>
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $reset = '' }

$root = Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot '_banner.ps1') 'Status'

function Line { param($Label, $State, $Detail = '')
    Write-Host ("  {0,-16} {1}  {2}" -f $Label, $State, "$dim$Detail$reset")
}
$ok   = "$green[ ok ]$reset"
$bad  = "$red[fail]$reset"
$warn = "$yellow[warn]$reset"
$idle = "$dim[ -- ]$reset"

# --- the Python environment ---
$venvFile = Join-Path $PSScriptRoot 'venv_path.txt'
$venv = if (Test-Path $venvFile) { (Get-Content $venvFile -Raw).Trim() } else { '' }
$venvPy = if ($venv) { Join-Path $venv 'Scripts\python.exe' } else { '' }

if ($venvPy -and (Test-Path $venvPy)) {
    $size = ''
    $work = Split-Path $venv -Parent
    try {
        $bytes = (Get-ChildItem $work -Recurse -File -Force -EA SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if ($bytes) { $size = "$([math]::Round($bytes / 1GB, 1)) GB" }
    } catch {}
    Line 'Environment' $ok "$venv  $size"
} else {
    Line 'Environment' $bad 'not installed - run: cft install'
}

# --- the reference Piper voice ---
$piper = Join-Path $PSScriptRoot 'models\piper\en_US-ryan-high.onnx'
if (Test-Path $piper) { Line 'Piper voice' $ok 'en_US-ryan-high' }
else { Line 'Piper voice' $bad 'missing - run: cft install' }

# --- character models ---
$rvcDir = Join-Path $PSScriptRoot 'models\rvc'
$voices = @()
if (Test-Path $rvcDir) {
    $voices += Get-ChildItem $rvcDir -Filter *.pth -File -EA SilentlyContinue | ForEach-Object { $_.BaseName }
    $voices += Get-ChildItem $rvcDir -Directory -EA SilentlyContinue |
        Where-Object { Get-ChildItem $_.FullName -Filter *.pth -File -EA SilentlyContinue } |
        ForEach-Object { $_.Name }
}
$voices = $voices | Sort-Object -Unique
if ($voices.Count -gt 0) { Line 'Character voices' $ok ($voices -join ', ') }
else { Line 'Character voices' $warn 'none - run: cft voices' }

# --- is the server actually answering right now ---
$port = if ($env:CFT_PORT) { $env:CFT_PORT } else { '8765' }
$host_ = if ($env:CFT_HOST) { $env:CFT_HOST } else { '127.0.0.1' }
try {
    $health = Invoke-RestMethod -Uri "http://${host_}:${port}/health" -TimeoutSec 2 -EA Stop
    Line 'Server' $ok "running on ${host_}:${port}"
} catch {
    Line 'Server' $idle "not running - start it with: cft start"
}

# --- is the mod deployed into the game ---
# -NoPrompt: status reports, it never stops to ask a question.
. (Join-Path $PSScriptRoot '_gta_path.ps1')
$gta = Resolve-GtaPath -NoPrompt

if ($gta) {
    $remembered = if (Get-SavedGtaPath) { '' } else { ' (guessed - set it with: cft where)' }
    Line 'GTA V' $ok "$gta$remembered"

    $dll = Join-Path $gta 'scripts\CallFromTwitch.dll'
    $jail = Join-Path $gta 'scripts\iFruit Jailbreak.dll'
    if (Test-Path $dll) {
        $when = (Get-Item $dll).LastWriteTime.ToString('yyyy-MM-dd HH:mm')
        Line 'Mod' $ok "deployed $when"
    } else {
        Line 'Mod' $warn 'not deployed - run: cft deploy'
    }
    # Without this the phone never rings, so it is worth calling out separately.
    if (Test-Path $jail) { Line 'iFruit Jailbreak' $ok 'present' }
    else { Line 'iFruit Jailbreak' $bad 'missing - the phone will never ring' }

    # The settings the player is most likely to have got wrong, read from the
    # file the game actually loads.
    $ini = Join-Path $gta 'scripts\CallFromTwitch.ini'
    if (Test-Path $ini) {
        . (Join-Path $PSScriptRoot '_ini.ps1')
        $twitchOn = Get-IniValue -File $ini -Section 'Twitch' -Key 'Enabled'
        $channel = Get-IniValue -File $ini -Section 'Twitch' -Key 'Channel'
        if ($twitchOn -match '^\s*true\s*$') {
            if (-not $channel) {
                Line 'Twitch chat' $bad 'enabled but no channel - run: cft config Channel'
            } elseif ($health -and $health.chat -and $health.chat.connected) {
                # The live answer, which is the only honest one: chat is read
                # by the server, so a configured channel with the server down
                # means nobody is listening.
                $seen = if ($health.chat.messages_seen) { " - $($health.chat.messages_seen) message(s) seen" } else { '' }
                Line 'Twitch chat' $ok "reading #$($health.chat.channel)$seen"
            } elseif ($health) {
                $why = if ($health.chat -and $health.chat.last_error) { " - $($health.chat.last_error)" } else { '' }
                Line 'Twitch chat' $warn "connecting to #$channel$why"
            } else {
                Line 'Twitch chat' $warn "#$channel is configured, but the server reads chat - start it with: cft start"
            }
        } else {
            Line 'Twitch chat' $idle 'off - enable it with: cft config Enabled'
        }
    }
} else {
    Line 'GTA V' $warn 'not found - run: cft where'
    Line 'Mod' $warn 'cannot check until GTA V is located'
}

Write-Host ''