<#
    Ask where CallFromTwitch should put its ~6 GB working directory.

    Prints the chosen path on stdout and nothing else, so setup.bat can capture
    it with a for /f loop; everything the user reads goes to stderr. That split
    is why this is a separate script rather than inline PowerShell.

    Exit 1 means the user cancelled.
#>
[CmdletBinding()]
param(
    # Space the venv, pip cache and fairseq checkout need between them.
    [int]$NeedGb = 12,
    [string]$Current = ''
)

$esc = [char]27
$dim = "$esc[90m"; $white = "$esc[97m"; $green = "$esc[92m"
$yellow = "$esc[93m"; $red = "$esc[91m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $dim = $white = $green = $yellow = $red = $reset = '' }

function Say { param([string]$Text = '') [Console]::Error.WriteLine($Text) }
function Ask { param([string]$Text) [Console]::Error.Write($Text); return [Console]::ReadLine() }

# Fixed local disks only: a removable or network drive is a bad home for a venv
# whose absolute paths get baked into the installed console scripts.
$drives = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType = 3' |
    Sort-Object -Property @{Expression = 'FreeSpace'; Descending = $true}

if (-not $drives) {
    [Console]::Error.WriteLine('No fixed drives found.')
    exit 1
}

Say ''
Say "${white}  Where should CallFromTwitch keep its files?${reset}"
Say "${dim}  PyTorch with CUDA, the pip cache and a fairseq checkout need about ${NeedGb} GB.${reset}"
Say "${dim}  This lives outside the repo on purpose - OneDrive should not sync it.${reset}"
Say ''

$options = @()
foreach ($d in $drives) {
    $freeGb = [math]::Round($d.FreeSpace / 1GB, 1)
    $totalGb = [math]::Round($d.Size / 1GB, 1)
    $fits = $d.FreeSpace / 1GB -ge $NeedGb
    $options += [pscustomobject]@{
        Path   = "$($d.DeviceID)\CallFromTwitch"
        Letter = $d.DeviceID
        FreeGb = $freeGb
        Fits   = $fits
        Label  = "$($d.DeviceID)\CallFromTwitch"
        Note   = if ($fits) { "$green$freeGb GB free$reset" }
                 else { "$red$freeGb GB free - not enough$reset" }
        Total  = $totalGb
    }
}

$i = 0
foreach ($o in $options) {
    $i++
    $marker = if ($o.Letter -eq $env:SystemDrive) { "${dim}(system drive)${reset}" } else { '' }
    Say ("   {0}. {1,-28} {2}  {3}" -f $i, $o.Label, $o.Note, $marker)
}
Say ("   {0}. {1}" -f ($i + 1), "Enter a custom path...")
Say ''

# Default to the roomiest drive that actually fits, else the roomiest overall.
$defaultIndex = 1
for ($n = 0; $n -lt $options.Count; $n++) {
    if ($options[$n].Fits) { $defaultIndex = $n + 1; break }
}
if ($Current) { Say "${dim}  Currently installed at: $Current${reset}"; Say '' }

while ($true) {
    $prompt = "  Choose [1-$($i + 1), Enter = $($options[$defaultIndex - 1].Path)]: "
    $answer = Ask $prompt
    if ($null -eq $answer) { exit 1 }
    $answer = $answer.Trim()

    $chosen = $null
    if (-not $answer) {
        $chosen = $options[$defaultIndex - 1].Path
    }
    elseif ($answer -eq [string]($i + 1)) {
        $custom = Ask '  Full path for the CallFromTwitch folder: '
        if ([string]::IsNullOrWhiteSpace($custom)) { Say "${yellow}  Nothing entered.${reset}"; continue }
        $chosen = $custom.Trim().Trim('"')
    }
    elseif ($answer -match '^\d+$' -and [int]$answer -ge 1 -and [int]$answer -le $i) {
        $chosen = $options[[int]$answer - 1].Path
    }
    # A bare drive letter is the shape people type without thinking; accept it.
    elseif ($answer -match '^([A-Za-z]):?\\?$') {
        $chosen = "$($Matches[1].ToUpper()):\CallFromTwitch"
    }
    else {
        $chosen = $answer.Trim('"')
    }

    if (-not $chosen) { continue }

    # Reject a relative string before GetFullPath silently resolves it against
    # the repo folder and hands back a plausible-looking path on the wrong disk.
    if ($chosen -notmatch '^[A-Za-z]:\\') {
        Say "${red}  Use a full path on a local drive, like D:\CallFromTwitch${reset}"; continue
    }

    try {
        $full = [System.IO.Path]::GetFullPath($chosen)
    } catch {
        Say "${red}  Not a valid path.${reset}"; continue
    }
    if ($full -notmatch '^[A-Za-z]:\\') {
        Say "${red}  Use a full path on a local drive, like D:\CallFromTwitch${reset}"; continue
    }

    $root = [System.IO.Path]::GetPathRoot($full)
    $disk = $drives | Where-Object { $_.DeviceID -eq $root.TrimEnd('\') }
    if (-not $disk) {
        Say "${yellow}  $root is not a fixed local drive - the venv may break there.${reset}"
        if ((Ask '  Use it anyway? [y/N]: ') -notmatch '^(y|yes)$') { continue }
    }
    elseif ($disk.FreeSpace / 1GB -lt $NeedGb) {
        $freeGb = [math]::Round($disk.FreeSpace / 1GB, 1)
        Say "${yellow}  Only $freeGb GB free on $root, and setup needs about $NeedGb GB.${reset}"
        if ((Ask '  Continue anyway? [y/N]: ') -notmatch '^(y|yes)$') { continue }
    }

    # Prove it is writable now rather than failing three steps into the install.
    try {
        $null = New-Item -ItemType Directory -Path $full -Force -ErrorAction Stop
        $probe = Join-Path $full '.cft_write_test'
        [System.IO.File]::WriteAllText($probe, 'ok')
        Remove-Item $probe -Force
    } catch {
        Say "${red}  Cannot write to $full${reset}"
        Say "${dim}  $($_.Exception.Message)${reset}"
        continue
    }

    Say ''
    Say "${green}  Installing to: $full${reset}"
    Say ''
    # The only stdout line: setup.bat reads exactly this.
    Write-Output $full
    exit 0
}
