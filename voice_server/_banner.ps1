<#
    Shared console branding for the installer and the launcher.

    Rendered from PowerShell rather than inline `echo` lines: the logo is full
    of characters cmd treats as syntax (| & < > ^ `), and escaping them by hand
    made the art unreadable and easy to break on edit. Here it is literal text.

    Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File _banner.ps1 "Subtitle"
#>
param([string]$Subtitle = '')

$esc = [char]27
# 256-colour codes: Twitch purple and GTA V's orange-gold.
$purple = "$esc[38;5;141m"
$orange = "$esc[38;5;214m"
$dim    = "$esc[90m"
$white  = "$esc[97m"
$reset  = "$esc[0m"

# Older consoles print escape codes literally; let anyone on one opt out.
if ($env:CFT_NO_COLOR) { $purple = $orange = $dim = $white = $reset = '' }

# The logo uses box-drawing glyphs, so the output encoding has to be UTF-8
# regardless of the console's code page. Setting it here rather than relying on
# the caller keeps the banner correct however it is invoked.
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}

# Left: the Twitch glitch mark (rounded top, notched tail, two eye bars).
# Right: a GTA V title plate. Drawn on a shared baseline.
$logo = @'
    ▄██████████▄         ╔══════════════════════╗
    ███▀▀██▀▀███▄        ║                      ║
    ███  ██  ████        ║  GRAND THEFT AUTO V  ║
    ███  ██  ███▀        ║                      ║
    ▀█████████▀          ╚══════════════════════╝
      ▀███▀                  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀
        ▀▀
'@

$word = @'
  ____      _ _   _____                    _____        _ _       _
 / ___|__ _| | | |  ___| __ ___  _ __ ___ |_   _|_ _____(_) |_ ___| |__
| |   / _` | | | | |_ | '__/ _ \| '_ ` _ \  | | \ V  V / | __/ __| '_ \
| |__| (_| | | | |  _|| | | (_) | | | | | | | |  \_/\_/| | || (__| | | |
 \____\__,_|_|_| |_|  |_|  \___/|_| |_| |_| |_|        |_|\__\___|_| |_|
'@

Write-Host ''
foreach ($line in $logo -split "`r?`n") { Write-Host "$purple$line$reset" }
Write-Host ''
foreach ($line in $word -split "`r?`n") { Write-Host "$orange$line$reset" }
Write-Host ''
Write-Host "$dim   GTA V  x  Twitch  -  your chat calls you in-game$reset"
if ($Subtitle) { Write-Host "$white   $Subtitle$reset" }
Write-Host "$dim   ---------------------------------------------------------------$reset"
Write-Host ''
