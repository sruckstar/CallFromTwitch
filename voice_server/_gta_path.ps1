<#
    Finding the GTA V install, in one place.

    Dot-source it (`. "$PSScriptRoot\_gta_path.ps1"`) to get the functions;
    run it as a script to print the resolved path on stdout and nothing else,
    the way _pick_dir.ps1 does, so a .bat can capture it with `for /f`.

    Deploy and status both used to carry their own hardcoded list of six
    folders. That list is now the LAST resort rather than the only one: the
    launchers record where they installed the game, and asking them beats
    guessing. Order, cheapest and most reliable first:

        1. -Path / $env:CALLFROMTWITCH_GTA  (an explicit answer wins outright)
        2. gta_path.txt                     (what the player chose last time)
        3. the registry                     (Rockstar, Steam, Epic, uninstall)
        4. Steam's libraryfolders.vdf       (the game is often on another disk)
        5. the old fixed list               (still right for a default install)
        6. a bounded scan of every fixed disk
        7. ask, with everything found offered as a numbered choice

    Legacy ships GTA5.exe; Enhanced ships GTA5_Enhanced.exe. Either is a hit.
#>
[CmdletBinding()]
param(
    # An explicit path from the command line: validated, then used as-is.
    [string]$Path = '',
    # Never prompt - fail instead. For status, which must not block.
    [switch]$NoPrompt,
    # Ask even when a good answer is already known ("cft where --set").
    [switch]$Force
)

$script:GtaExeNames = @('GTA5.exe', 'GTA5_Enhanced.exe')

# Remembered next to venv_path.txt, and for the same reason: the answer is a
# property of this machine, not of the repo, so it stays out of git.
function Get-GtaPathFile {
    Join-Path $PSScriptRoot 'gta_path.txt'
}

<#
    True when $Path is a GTA V folder. Everything downstream trusts this, so
    it is the single definition of "a valid target".
#>
function Test-GtaFolder {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    foreach ($exe in $script:GtaExeNames) {
        if (Test-Path -LiteralPath (Join-Path $Path $exe)) { return $true }
    }
    return $false
}

<#
    Which edition lives in a folder - shown in the picker so two entries that
    differ only by a folder name are still tellable apart.
#>
function Get-GtaEdition {
    param([string]$Path)
    $legacy = Test-Path -LiteralPath (Join-Path $Path 'GTA5.exe')
    $enhanced = Test-Path -LiteralPath (Join-Path $Path 'GTA5_Enhanced.exe')
    if ($legacy -and $enhanced) { return 'Legacy + Enhanced' }
    if ($enhanced) { return 'Enhanced' }
    if ($legacy) { return 'Legacy' }
    return ''
}

<#
    Ask the launchers where they put it. Every read is wrapped: a missing key
    is the normal case, not an error worth surfacing.
#>
function Get-GtaPathsFromRegistry {
    $found = @()

    # Rockstar's own launcher, and the WOW6432Node view of it.
    $rockstar = @(
        'HKLM:\SOFTWARE\WOW6432Node\Rockstar Games\Grand Theft Auto V',
        'HKLM:\SOFTWARE\Rockstar Games\Grand Theft Auto V',
        'HKLM:\SOFTWARE\WOW6432Node\Rockstar Games\GTAV',
        'HKLM:\SOFTWARE\Rockstar Games\GTAV',
        'HKCU:\SOFTWARE\Rockstar Games\Grand Theft Auto V'
    )
    foreach ($key in $rockstar) {
        foreach ($name in @('InstallFolder', 'InstallLocation', 'Install Directory')) {
            try {
                $value = (Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction Stop).$name
                if ($value) { $found += $value }
            } catch {}
        }
    }

    # Steam and Epic both register an uninstall entry, which carries
    # InstallLocation. Cheaper and more reliable than guessing folder names.
    $uninstall = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $uninstall) {
        try {
            Get-ChildItem -LiteralPath $root -ErrorAction Stop | ForEach-Object {
                try {
                    $item = Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop
                    if ("$($item.DisplayName)" -match 'Grand Theft Auto V' -and $item.InstallLocation) {
                        $found += $item.InstallLocation
                    }
                } catch {}
            }
        } catch {}
    }

    # Epic keeps a manifest per app; the GTA V one names its install dir.
    $manifestDir = Join-Path $env:ProgramData 'Epic\EpicGamesLauncher\Data\Manifests'
    if (Test-Path -LiteralPath $manifestDir) {
        try {
            Get-ChildItem -LiteralPath $manifestDir -Filter *.item -File -ErrorAction Stop |
                ForEach-Object {
                    try {
                        $json = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction Stop |
                            ConvertFrom-Json
                        if ("$($json.DisplayName)" -match 'Grand Theft Auto V' -and $json.InstallLocation) {
                            $found += $json.InstallLocation
                        }
                    } catch {}
                }
        } catch {}
    }

    return $found
}

<#
    Steam installs into whichever library the player picked, which is very
    often a different disk from Steam itself. libraryfolders.vdf lists them
    all; pulling the quoted "path" lines out of it is enough - no VDF parser
    needed for a file this shape.
#>
function Get-GtaPathsFromSteam {
    $found = @()

    $steamRoots = @()
    foreach ($key in @('HKCU:\SOFTWARE\Valve\Steam', 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam')) {
        foreach ($name in @('SteamPath', 'InstallPath')) {
            try {
                $value = (Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction Stop).$name
                if ($value) { $steamRoots += $value }
            } catch {}
        }
    }
    $steamRoots += (Join-Path ${env:ProgramFiles(x86)} 'Steam')
    $steamRoots = $steamRoots | Where-Object { $_ } | Sort-Object -Unique

    $libraries = @()
    foreach ($steam in $steamRoots) {
        $libraries += $steam
        foreach ($vdf in @(
            (Join-Path $steam 'steamapps\libraryfolders.vdf'),
            (Join-Path $steam 'config\libraryfolders.vdf'))) {
            if (-not (Test-Path -LiteralPath $vdf)) { continue }
            try {
                foreach ($line in (Get-Content -LiteralPath $vdf -ErrorAction Stop)) {
                    # A library line looks like:   "path"		"D:\\SteamLibrary"
                    if ($line -match '"path"\s+"(.+?)"') {
                        $libraries += $Matches[1] -replace '\\\\', '\'
                    }
                }
            } catch {}
        }
    }

    foreach ($lib in ($libraries | Where-Object { $_ } | Sort-Object -Unique)) {
        foreach ($name in @('Grand Theft Auto V', 'Grand Theft Auto V Enhanced', 'GTAV')) {
            $found += (Join-Path $lib "steamapps\common\$name")
        }
    }

    return $found
}

<#
    The list deploy.ps1 and _status.ps1 used to hold privately, generalised:
    the same folder shapes, but tried under every fixed disk instead of only
    the two that happened to be written down.
#>
function Get-GtaCommonPaths {
    $names = @('Grand Theft Auto V', 'Grand Theft Auto V Enhanced', 'GTAV')
    $prefixes = @(
        (Join-Path $env:ProgramFiles 'Rockstar Games'),
        (Join-Path ${env:ProgramFiles(x86)} 'Rockstar Games'),
        (Join-Path $env:ProgramFiles 'Epic Games'),
        (Join-Path ${env:ProgramFiles(x86)} 'Epic Games')
    )
    foreach ($drive in (Get-FixedDriveLetters)) {
        $prefixes += @(
            "$drive\Rockstar Games",
            "$drive\Games\Rockstar Games",
            "$drive\Games",
            "$drive\SteamLibrary\steamapps\common",
            "$drive\Games\Steam\steamapps\common",
            "$drive\Steam\steamapps\common",
            "$drive\Epic Games"
        )
    }

    $paths = @()
    foreach ($prefix in ($prefixes | Where-Object { $_ } | Sort-Object -Unique)) {
        foreach ($name in $names) { $paths += (Join-Path $prefix $name) }
    }
    return $paths
}

<#
    Is there a human at the keyboard? When stdin is redirected (a pipe, a CI
    run, output captured by another script) ReadLine returns $null forever,
    and a prompt loop would either spin or silently take a default. Better to
    behave as if -NoPrompt had been passed.
#>
function Test-Interactive {
    try {
        if ([Console]::IsInputRedirected) { return $false }
    } catch {}
    return $true
}

function Get-FixedDriveLetters {
    try {
        return @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType = 3' -ErrorAction Stop |
            ForEach-Object { $_.DeviceID })
    } catch {
        return @($env:SystemDrive)
    }
}

<#
    Last resort before asking: look for the exe itself. Bounded to a few
    levels below each disk root, which covers D:\Games\Rockstar\GTAV without
    walking a whole drive - an unbounded scan can take minutes and reads as
    a hang.
#>
function Get-GtaPathsFromScan {
    param([int]$Depth = 3)
    $found = @()
    foreach ($drive in (Get-FixedDriveLetters)) {
        foreach ($exe in $script:GtaExeNames) {
            try {
                $found += Get-ChildItem -LiteralPath "$drive\" -Filter $exe -File -Recurse `
                    -Depth $Depth -Force -ErrorAction SilentlyContinue |
                    ForEach-Object { $_.DirectoryName }
            } catch {}
        }
    }
    return $found
}

<#
    Every candidate, deduplicated and validated, best guess first. Sources are
    tried cheapest-first and the disk scan only runs when nothing else
    produced a hit, so the common case costs a handful of registry reads.
#>
function Find-GtaCandidates {
    param([switch]$IncludeScan)

    $ordered = @()
    $ordered += Get-GtaPathsFromRegistry
    $ordered += Get-GtaPathsFromSteam
    $ordered += Get-GtaCommonPaths

    # The scan is the expensive source, so it is only appended when the cheap
    # ones came back empty; dedup then runs over the whole list either way.
    if ($IncludeScan) {
        $cheapHits = @($ordered | Where-Object { $_ -and (Test-GtaFolder $_) })
        if ($cheapHits.Count -eq 0) { $ordered += Get-GtaPathsFromScan }
    }

    $valid = @()
    $seen = @{}
    foreach ($candidate in $ordered) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try { $full = [System.IO.Path]::GetFullPath("$candidate".Trim().Trim('"')) } catch { continue }
        $key = $full.TrimEnd('\').ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-GtaFolder $full) { $valid += $full }
    }

    return $valid
}

function Get-SavedGtaPath {
    $file = Get-GtaPathFile
    if (-not (Test-Path -LiteralPath $file)) { return '' }
    try {
        $saved = (Get-Content -LiteralPath $file -Raw -Encoding UTF8).Trim()
    } catch { return '' }
    if (Test-GtaFolder $saved) { return $saved }
    return ''
}

function Save-GtaPath {
    param([Parameter(Mandatory)][string]$Path)
    try {
        [System.IO.File]::WriteAllText((Get-GtaPathFile), $Path, (New-Object System.Text.UTF8Encoding($false)))
        return $true
    } catch {
        return $false
    }
}

function Clear-GtaPath {
    $file = Get-GtaPathFile
    if (Test-Path -LiteralPath $file) {
        Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue
    }
}

<#
    The interactive half: show what was found, let the player pick one, type a
    path, or point at GTA5.exe itself (dragging the exe into the window is the
    gesture people reach for, so accept it and take its folder).

    Prompts go to stderr, exactly like _pick_dir.ps1, so this stays usable as
    a stdout-is-the-answer script.
#>
function Select-GtaPath {
    param([string[]]$Candidates = @(), [string]$Current = '')

    $esc = [char]27
    $dim = "$esc[90m"; $white = "$esc[97m"; $green = "$esc[92m"
    $yellow = "$esc[93m"; $red = "$esc[91m"; $reset = "$esc[0m"
    if ($env:CFT_NO_COLOR) { $dim = $white = $green = $yellow = $red = $reset = '' }

    function Say { param([string]$Text = '') [Console]::Error.WriteLine($Text) }
    function Ask { param([string]$Text) [Console]::Error.Write($Text); return [Console]::ReadLine() }

    Say ''
    if ($Candidates.Count -gt 0) {
        Say "${white}  Where is GTA V installed?${reset}"
        Say "${dim}  Found these - pick the one you actually play.${reset}"
    } else {
        Say "${yellow}  GTA V was not found automatically.${reset}"
        Say "${dim}  Tell me where it is: the folder that contains GTA5.exe.${reset}"
    }
    Say ''

    $i = 0
    foreach ($c in $Candidates) {
        $i++
        $edition = Get-GtaEdition $c
        $mark = if ($Current -and $c.TrimEnd('\') -ieq $Current.TrimEnd('\')) { "${green}(current)${reset}" } else { '' }
        Say ("   {0}. {1}  {2}{3}{4}  {5}" -f $i, $c, $dim, $edition, $reset, $mark)
    }
    $manual = $i + 1
    Say ("   {0}. {1}" -f $manual, 'Enter the path myself...')
    Say ''
    Say "${dim}  Tip: you can also drag GTA5.exe into this window and press Enter.${reset}"
    Say ''

    while ($true) {
        $default = if ($Candidates.Count -gt 0) { ", Enter = 1" } else { '' }
        $answer = Ask "  Choose [1-$manual$default]: "
        if ($null -eq $answer) { return '' }
        $answer = $answer.Trim()

        $chosen = $null
        if (-not $answer) {
            if ($Candidates.Count -gt 0) { $chosen = $Candidates[0] } else { continue }
        }
        elseif ($answer -eq [string]$manual -and $Candidates.Count -gt 0) {
            $typed = Ask '  Full path to the GTA V folder: '
            if ([string]::IsNullOrWhiteSpace($typed)) { Say "${yellow}  Nothing entered.${reset}"; continue }
            $chosen = $typed
        }
        elseif ($answer -match '^\d+$' -and [int]$answer -ge 1 -and [int]$answer -le $i) {
            $chosen = $Candidates[[int]$answer - 1]
        }
        else {
            $chosen = $answer
        }

        $chosen = "$chosen".Trim().Trim('"')
        if (-not $chosen) { continue }

        # A dragged exe arrives as a full path to the file, not the folder.
        if ($chosen -match '\.exe$') {
            $parent = Split-Path -LiteralPath $chosen -Parent
            if ($parent) { $chosen = $parent }
        }

        try { $chosen = [System.IO.Path]::GetFullPath($chosen) } catch {
            Say "${red}  That is not a valid path.${reset}"; continue
        }

        if (Test-GtaFolder $chosen) {
            Say ''
            Say "${green}  Using: $chosen${reset}"
            Say ''
            return $chosen
        }

        Say "${red}  No GTA5.exe or GTA5_Enhanced.exe in:${reset}"
        Say "${dim}    $chosen${reset}"
        Say "${dim}  Pick the game folder itself, not Rockstar Games or steamapps.${reset}"
    }
}

<#
    The one entry point callers should use. Returns a validated path, or ''
    when it could not be determined (only possible with -NoPrompt, or if the
    player cancels).

    -Save records a freshly chosen path so the next command does not ask again.
#>
function Resolve-GtaPath {
    param(
        [string]$Path = '',
        [switch]$NoPrompt,
        [switch]$Force,
        [switch]$Save
    )

    # Nobody to ask means nobody to ask, whoever called.
    if (-not (Test-Interactive)) { $NoPrompt = [switch]$true }

    # 1. An explicit answer wins. A wrong one is worth a clear failure rather
    #    than a silent fallback to some other install.
    if ($Path) {
        try { $Path = [System.IO.Path]::GetFullPath($Path.Trim().Trim('"')) } catch {}
        if (Test-GtaFolder $Path) {
            if ($Save) { [void](Save-GtaPath $Path) }
            return $Path
        }
        if ($NoPrompt) { return '' }
        [Console]::Error.WriteLine("  No GTA5.exe / GTA5_Enhanced.exe in '$Path'.")
    }
    elseif ($env:CALLFROMTWITCH_GTA -and -not $Force) {
        $fromEnv = $env:CALLFROMTWITCH_GTA.Trim().Trim('"')
        if (Test-GtaFolder $fromEnv) { return $fromEnv }
    }

    # 2. What the player chose last time.
    $saved = Get-SavedGtaPath
    if ($saved -and -not $Force) { return $saved }

    # 3-6. Everything the machine can tell us.
    $candidates = @(Find-GtaCandidates -IncludeScan:(-not $NoPrompt))

    if ($NoPrompt) {
        if ($candidates.Count -gt 0) { return $candidates[0] }
        return ''
    }

    # A single unambiguous hit and nothing saved yet: take it, but remember it
    # so the next run is instant and the player can see what was picked.
    if ($candidates.Count -eq 1 -and -not $Force) {
        [void](Save-GtaPath $candidates[0])
        return $candidates[0]
    }

    # 7. Ask.
    $chosen = Select-GtaPath -Candidates $candidates -Current $saved
    if ($chosen) { [void](Save-GtaPath $chosen) }
    return $chosen
}

# Run as a script (not dot-sourced): print the answer on stdout, nothing else.
if ($MyInvocation.InvocationName -ne '.') {
    $resolved = Resolve-GtaPath -Path $Path -NoPrompt:$NoPrompt -Force:$Force
    if (-not $resolved) { exit 1 }
    Write-Output $resolved
    exit 0
}
