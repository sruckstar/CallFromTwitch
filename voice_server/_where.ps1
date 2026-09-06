<#
    "cft where" - which GTA V install the mod deploys into.

        cft where              show it, and offer to change it
        cft where --set        pick again from everything found
        cft where <path>       set it directly
        cft where --clear      forget it and detect again next time

    The choice lives in gta_path.txt next to venv_path.txt: a property of this
    machine, not of the repo.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
. (Join-Path $PSScriptRoot '_gta_path.ps1')

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $cyan = "$esc[96m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $cyan = $reset = '' }

$rest = @($Args | Where-Object { $_ -ne $null -and "$_".Trim() -ne '' })
$first = if ($rest.Count -gt 0) { "$($rest[0])".Trim() } else { '' }

if ($first -match '^--?(clear|forget|reset)$') {
    Clear-GtaPath
    Write-Host ''
    Write-Host "$green  Forgotten. The next command will detect GTA V again.$reset"
    Write-Host ''
    exit 0
}

if ($first -match '^--?(help|h|\?)$') {
    Write-Host ''
    Write-Host "  $white cft where$reset          $dim show the folder the mod deploys into$reset"
    Write-Host "  $white cft where --set$reset    $dim choose from every install found$reset"
    Write-Host "  $white cft where <path>$reset   $dim set it directly$reset"
    Write-Host "  $white cft where --clear$reset  $dim forget it and detect again$reset"
    Write-Host ''
    exit 0
}

# A bare path argument sets it outright.
if ($first -and $first -notmatch '^--') {
    $path = ($rest -join ' ').Trim().Trim('"')
    $resolved = Resolve-GtaPath -Path $path -Save
    if (-not $resolved) {
        Write-Host ''
        Write-Host "$red  That is not a GTA V folder:$reset $path"
        Write-Host "$dim  It must be the folder containing GTA5.exe.$reset"
        Write-Host ''
        exit 1
    }
    Write-Host ''
    Write-Host "$green  Deploying into: $resolved$reset"
    Write-Host ''
    exit 0
}

& (Join-Path $PSScriptRoot '_banner.ps1') 'GTA V location'

$saved = Get-SavedGtaPath
$forcePick = ($first -match '^--?(set|change|pick|choose)$')

if ($saved -and -not $forcePick) {
    Write-Host "  $white GTA V:$reset $saved"
    Write-Host "  $dim Edition: $(Get-GtaEdition $saved)$reset"
    $scripts = Join-Path $saved 'scripts'
    if (Test-Path -LiteralPath (Join-Path $scripts 'CallFromTwitch.dll')) {
        Write-Host "  $dim Mod:     deployed$reset"
    } else {
        Write-Host "  $yellow Mod:     not deployed yet - run: cft deploy$reset"
    }
    Write-Host ''
    Write-Host -NoNewline "  Change it? [y/N]: "
    $answer = [Console]::ReadLine()
    if ($answer -notmatch '^\s*(y|yes)\s*$') {
        Write-Host ''
        Write-Host "$dim  Unchanged.$reset"
        Write-Host ''
        exit 0
    }
}

Write-Host "$dim  Looking for GTA V...$reset"
$candidates = @(Find-GtaCandidates -IncludeScan)

$chosen = Select-GtaPath -Candidates $candidates -Current $saved
if (-not $chosen) {
    Write-Host ''
    Write-Host "$yellow  Cancelled - nothing changed.$reset"
    Write-Host ''
    exit 1
}

[void](Save-GtaPath $chosen)
Write-Host "$green  Deploying into: $chosen$reset"
Write-Host ''
Write-Host "  Next: $cyan cft deploy $reset$dim to build and install the mod there.$reset"
Write-Host ''
exit 0
