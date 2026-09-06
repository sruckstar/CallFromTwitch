<#
    "cft config" - edit CallFromTwitch.ini without opening a text editor.

        cft config                    browse and edit, menu-driven
        cft config twitch             jump straight to the [Twitch] settings
        cft config Channel kreyg      set one value and exit
        cft config Channel            show one value
        cft config --list             print everything
        cft config --file             print the path to the file being edited
        cft config --reset Volume     put one value back to its default

    Edits the DEPLOYED file in GTA V\scripts\, because that is the one the
    game reads. The copy in the repo is a template for the next deploy; when
    the deployed one does not exist yet, that template is offered as a seed.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args = @()
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue

. (Join-Path $PSScriptRoot '_ini.ps1')
. (Join-Path $PSScriptRoot '_gta_path.ps1')

$esc = [char]27
$green = "$esc[92m"; $red = "$esc[91m"; $yellow = "$esc[93m"
$dim = "$esc[90m"; $white = "$esc[97m"; $cyan = "$esc[96m"
$purple = "$esc[38;5;141m"; $reset = "$esc[0m"
if ($env:CFT_NO_COLOR) { $green = $red = $yellow = $dim = $white = $cyan = $purple = $reset = '' }

$schema = Get-CftIniSchema
$repoRoot = Split-Path $PSScriptRoot -Parent

# ---------------------------------------------------------------- helpers --

function Show-Value {
    <# The stored value, or the default shown as such. #>
    param([hashtable]$Item, [string]$File)
    $raw = Get-IniValue -File $File -Section $Item.Section -Key $Item.Key
    if ($null -eq $raw) {
        return @{ Text = "$dim$($Item.Default)  (default)$reset"; Raw = $Item.Default; Set = $false }
    }
    if ($raw -eq '') {
        return @{ Text = "$dim(empty)$reset"; Raw = ''; Set = $true }
    }
    if ($Item.Secret) {
        # Never print a token in full: this window gets screenshotted.
        $shown = if ($raw.Length -gt 6) { $raw.Substring(0, 3) + ('*' * 8) } else { '*' * 8 }
        return @{ Text = "$white$shown$reset"; Raw = $raw; Set = $true }
    }
    return @{ Text = "$white$raw$reset"; Raw = $raw; Set = $true }
}

function Format-Hint {
    <# What this field accepts, in the shortest honest form. #>
    param([hashtable]$Item)
    switch ($Item.Type) {
        'bool'   { return 'true / false' }
        'int'    { return "$($Item.Min)-$($Item.Max)" }
        'float'  { return "$($Item.Min)-$($Item.Max)" }
        'choice' { return ($Item.Options -join ', ') }
        default  { return 'text' }
    }
}

function Find-Item {
    <#
        Match what the player typed against the schema: "Channel",
        "Twitch.Channel" and "twitch channel" all name the same setting.
        Returns every match so an ambiguous name can be reported as such.
    #>
    param([string]$Name)
    $name = "$Name".Trim()
    if (-not $name) { return @() }

    $wanted = $name -replace '[\s._-]', ''
    $exact = @($schema | Where-Object {
        ("$($_.Section)$($_.Key)" -ieq $wanted) -or ($_.Key -ieq $wanted)
    })
    if ($exact.Count -gt 0) { return $exact }

    return @($schema | Where-Object {
        $_.Key -imatch [regex]::Escape($wanted) -or
        ($_.Title -replace '[\s._-]', '') -imatch [regex]::Escape($wanted)
    })
}

function Resolve-IniFile {
    <#
        Which file to edit. The deployed one wins; failing that, offer to
        create it from the repo template, since an INI the game never reads
        is not what "edit my settings" means.
    #>
    param([switch]$Quiet)

    # Locating the game must never itself turn into a question here: the
    # player asked to edit settings, not to be interviewed. Whatever is
    # already known is used, and "cft where" is the place to change it.
    $gta = Resolve-GtaPath -NoPrompt
    if ($gta) {
        $deployed = Join-Path $gta 'scripts\CallFromTwitch.ini'
        if (Test-Path -LiteralPath $deployed) { return $deployed }

        if ($Quiet) { return '' }

        Write-Host ''
        Write-Host "$yellow  The mod is not deployed to GTA V yet.$reset"
        Write-Host "$dim  Expected: $deployed$reset"
        Write-Host ''
        Write-Host "  Run $cyan cft deploy $reset first, or edit the template that will be"
        Write-Host "  copied there on the next deploy."
        Write-Host ''
        if (Test-Interactive) {
            Write-Host -NoNewline '  Edit the template instead? [Y/n]: '
            $answer = [Console]::ReadLine()
            if ($answer -match '^\s*(n|no)\s*$') { return '' }
        }
        return (Join-Path $repoRoot 'CallFromTwitch.ini')
    }

    if ($Quiet) { return '' }
    Write-Host ''
    Write-Host "$yellow  GTA V was not found, so there is no deployed config to edit.$reset"
    Write-Host "$dim  Editing the repo template - it is copied on the next 'cft deploy'.$reset"
    Write-Host ''
    return (Join-Path $repoRoot 'CallFromTwitch.ini')
}

function Set-One {
    <# Validate, normalise, write, report. The single place a value changes. #>
    param([hashtable]$Item, [AllowEmptyString()][string]$Value, [string]$File)

    $cleaned = Format-IniInput -Item $Item -Value $Value
    $check = Test-IniValue -Item $Item -Value $cleaned
    if (-not $check.Ok) {
        Write-Host "$red  $($check.Problem)$reset"
        return $false
    }

    Set-IniValue -File $File -Section $Item.Section -Key $Item.Key -Value $check.Value
    $shown = if ($Item.Secret -and $check.Value) { '(saved)' }
             elseif ($check.Value -eq '') { '(empty)' }
             else { $check.Value }
    Write-Host "$green  $($Item.Section).$($Item.Key) = $shown$reset"
    return $true
}

function Prompt-One {
    <#
        Edit one setting interactively. Enter keeps the current value, so
        stepping through a section is safe; "-" clears an optional field,
        which is otherwise impossible to express at a prompt.
    #>
    param([hashtable]$Item, [string]$File)

    $current = Show-Value -Item $Item -File $File

    Write-Host ''
    Write-Host "  $white$($Item.Title)$reset  $dim[$($Item.Section)] $($Item.Key)$reset"
    Write-Host "  $dim$($Item.Help)$reset"
    Write-Host "  $dim Accepts: $(Format-Hint $Item)$reset"
    Write-Host "   Now: $($current.Text)"

    if ($Item.Type -eq 'bool') {
        $flip = if ($current.Raw -match '^(true|1|yes|on)$') { 'false' } else { 'true' }
        Write-Host "  $dim Enter = switch to $flip, or type true/false. 'q' to go back.$reset"
        Write-Host -NoNewline '  > '
        $answer = [Console]::ReadLine()
        if ($null -eq $answer) { return }
        $answer = $answer.Trim()
        if ($answer -match '^(q|quit|back)$') { return }
        if (-not $answer) { $answer = $flip }
        [void](Set-One -Item $Item -Value $answer -File $File)
        return
    }

    if ($Item.Type -eq 'choice') {
        $n = 0
        foreach ($opt in $Item.Options) {
            $n++
            $mark = if ($opt -ieq $current.Raw) { "$green<$reset" } else { '' }
            Write-Host ("    {0,2}. {1} {2}" -f $n, $opt, $mark)
        }
        Write-Host "  $dim Enter a number or the name. Enter alone keeps it. 'q' to go back.$reset"
        Write-Host -NoNewline '  > '
        $answer = [Console]::ReadLine()
        if ($null -eq $answer) { return }
        $answer = $answer.Trim()
        if (-not $answer -or $answer -match '^(q|quit|back)$') { return }
        if ($answer -match '^\d+$' -and [int]$answer -ge 1 -and [int]$answer -le $Item.Options.Count) {
            $answer = $Item.Options[[int]$answer - 1]
        }
        [void](Set-One -Item $Item -Value $answer -File $File)
        return
    }

    $clearable = ($Item.Type -eq 'text')
    $tip = if ($clearable) { "Enter keeps it, '-' clears it, 'q' goes back." }
           else { "Enter keeps it, 'q' goes back." }
    Write-Host "  $dim $tip$reset"
    Write-Host -NoNewline '  > '
    $answer = [Console]::ReadLine()
    if ($null -eq $answer) { return }
    $answer = $answer.Trim()
    if (-not $answer) { return }
    if ($answer -match '^(q|quit|back)$') { return }
    if ($clearable -and $answer -eq '-') { $answer = '' }
    [void](Set-One -Item $Item -Value $answer -File $File)
}

function Show-Section {
    <# Every setting in one section, numbered so it can be picked. #>
    param([string]$Section, [string]$File)

    while ($true) {
        $items = @($schema | Where-Object { $_.Section -eq $Section })
        Write-Host ''
        Write-Host "  $purple[$Section]$reset"
        Write-Host ''
        $n = 0
        foreach ($item in $items) {
            $n++
            $v = Show-Value -Item $item -File $File
            Write-Host ("   {0,2}. {1,-28} {2}" -f $n, $item.Title, $v.Text)
        }
        Write-Host ''
        Write-Host "  $dim A number edits it. Enter goes back.$reset"
        Write-Host -NoNewline '  > '
        $answer = [Console]::ReadLine()
        if ($null -eq $answer) { return }
        $answer = $answer.Trim()
        if (-not $answer -or $answer -match '^(q|quit|back)$') { return }
        if ($answer -match '^\d+$' -and [int]$answer -ge 1 -and [int]$answer -le $items.Count) {
            Prompt-One -Item $items[[int]$answer - 1] -File $File
        } else {
            # A name typed instead of a number, which people do.
            $found = @(Find-Item $answer)
            if ($found.Count -eq 1) { Prompt-One -Item $found[0] -File $File }
            else { Write-Host "$yellow  Pick a number from the list.$reset" }
        }
    }
}

function Show-Menu {
    <# The top level: sections, in the order players care about them. #>
    param([string]$File)

    $sections = @('Twitch', 'Call', 'Voice', 'Audio', 'Server')
    $blurbs = @{
        Twitch = 'chat commands, who may call, cooldowns'
        Call   = 'caller name, picture, ring timing'
        Voice  = 'character voice'
        Audio  = 'volume and subtitles'
        Server = 'address of the voice server'
    }

    while ($true) {
        Write-Host ''
        Write-Host "  $dim Editing: $File$reset"
        Write-Host ''
        $n = 0
        foreach ($s in $sections) {
            $n++
            Write-Host ("   {0}. {1,-10} {2}{3}{4}" -f $n, $s, $dim, $blurbs[$s], $reset)
        }
        Write-Host ''
        Write-Host "   $dim a. show everything      q. done$reset"
        Write-Host ''
        Write-Host -NoNewline '  Choose: '
        $answer = [Console]::ReadLine()
        if ($null -eq $answer) { return }
        $answer = $answer.Trim()

        if (-not $answer -or $answer -match '^(q|quit|exit|done)$') { return }
        if ($answer -match '^(a|all|list)$') { Show-All -File $File; continue }
        if ($answer -match '^\d+$' -and [int]$answer -ge 1 -and [int]$answer -le $sections.Count) {
            Show-Section -Section $sections[[int]$answer - 1] -File $File
            continue
        }

        $bySection = $sections | Where-Object { $_ -ieq $answer } | Select-Object -First 1
        if ($bySection) { Show-Section -Section $bySection -File $File; continue }

        $found = @(Find-Item $answer)
        if ($found.Count -eq 1) { Prompt-One -Item $found[0] -File $File; continue }
        if ($found.Count -gt 1) {
            Write-Host "$yellow  Several settings match '$answer':$reset"
            foreach ($f in $found) { Write-Host "    $($f.Section).$($f.Key)" }
            continue
        }
        Write-Host "$yellow  No setting called '$answer'.$reset"
    }
}

function Show-All {
    param([string]$File)
    $lastSection = ''
    Write-Host ''
    foreach ($item in $schema) {
        if ($item.Section -ne $lastSection) {
            Write-Host ''
            Write-Host "  $purple[$($item.Section)]$reset"
            $lastSection = $item.Section
        }
        $v = Show-Value -Item $item -File $File
        Write-Host ("    {0,-24} {1}" -f $item.Key, $v.Text)
    }
    Write-Host ''
}

# ------------------------------------------------------------------- main --

$rest = @($Args | Where-Object { $_ -ne $null -and "$_".Trim() -ne '' })
$first = if ($rest.Count -gt 0) { "$($rest[0])".Trim() } else { '' }

# Help first: it must not go looking for GTA V, let alone ask about it.
if ($first -match '^--?(help|h|\?)$') {
    Write-Host ''
    Write-Host "  $white cft config$reset                  $dim menu$reset"
    Write-Host "  $white cft config twitch$reset           $dim jump to a section$reset"
    Write-Host "  $white cft config Channel$reset          $dim show one setting$reset"
    Write-Host "  $white cft config Channel kreyg$reset    $dim set one setting$reset"
    Write-Host "  $white cft config --list$reset           $dim show everything$reset"
    Write-Host "  $white cft config --reset Volume$reset   $dim back to the default$reset"
    Write-Host "  $white cft config --edit$reset           $dim open it in Notepad$reset"
    Write-Host ''
    exit 0
}

# --file / --path: for scripts and for "where is that file again?"
if ($first -match '^--?(file|path)$') {
    $file = Resolve-IniFile -Quiet
    if (-not $file) { Write-Host "$red  No config found. Run: cft deploy$reset"; exit 1 }
    Write-Output $file
    exit 0
}

# --edit: hand it to the player's text editor, for anyone who prefers that.
if ($first -match '^--?(edit|open|notepad)$') {
    $file = Resolve-IniFile
    if (-not $file) { exit 1 }
    if (-not (Test-Path -LiteralPath $file)) {
        Write-Host "$red  $file does not exist yet. Run: cft deploy$reset"
        exit 1
    }
    Start-Process notepad.exe -ArgumentList $file
    Write-Host "$green  Opened $file$reset"
    exit 0
}

$file = Resolve-IniFile
if (-not $file) { exit 1 }

# The template is only a seed; make sure there is something to edit.
if (-not (Test-Path -LiteralPath $file)) {
    $template = Join-Path $repoRoot 'CallFromTwitch.ini'
    if (Test-Path -LiteralPath $template) {
        Copy-Item -LiteralPath $template -Destination $file -Force
        Write-Host "$dim  Created $file from the template.$reset"
    } else {
        Write-IniLines -File $file -Lines @('; CallFromTwitch')
    }
}

if ($first -match '^--?(list|all|show)$') { Show-All -File $file; exit 0 }

if ($first -match '^--?reset$') {
    $name = if ($rest.Count -gt 1) { $rest[1] } else { '' }
    $found = @(Find-Item $name)
    if ($found.Count -ne 1) {
        Write-Host "$red  Name one setting to reset, for example: cft config --reset Volume$reset"
        exit 1
    }
    [void](Set-One -Item $found[0] -Value $found[0].Default -File $file)
    exit 0
}

# No arguments: the full interactive editor.
if (-not $first) {
    & (Join-Path $PSScriptRoot '_banner.ps1') 'Settings'
    Show-Menu -File $file
    Write-Host ''
    Write-Host "$green  Saved to $file$reset"
    Write-Host "$dim  Restart GTA V (or reload scripts) for the changes to take effect.$reset"
    Write-Host ''
    exit 0
}

# A section name: straight into that section.
$sectionMatch = @('Twitch','Call','Voice','Keys','Audio','Server') |
    Where-Object { $_ -ieq $first } | Select-Object -First 1
if ($sectionMatch -and $rest.Count -eq 1) {
    Show-Section -Section $sectionMatch -File $file
    Write-Host ''
    Write-Host "$dim  Saved to $file$reset"
    exit 0
}

# Otherwise: a setting name, with or without a value.
$found = @(Find-Item $first)
if ($found.Count -eq 0) {
    Write-Host ''
    Write-Host "$red  No setting called '$first'.$reset"
    Write-Host "$dim  Run 'cft config' for the menu, or 'cft config --list'.$reset"
    Write-Host ''
    exit 1
}
if ($found.Count -gt 1) {
    Write-Host ''
    Write-Host "$yellow  '$first' matches several settings:$reset"
    foreach ($f in $found) { Write-Host "    $($f.Section).$($f.Key)   $dim$($f.Title)$reset" }
    Write-Host ''
    Write-Host "$dim  Use the full name, e.g. cft config Twitch.Channel$reset"
    Write-Host ''
    exit 1
}

$item = $found[0]

if ($rest.Count -eq 1) {
    # A bare name is usually a look before a change, so show the value and
    # offer the prompt - which prints the same header, hence no preamble here.
    if (Test-Interactive) {
        Prompt-One -Item $item -File $file
    } else {
        $v = Show-Value -Item $item -File $file
        Write-Host "$($item.Section).$($item.Key) = $($v.Raw)"
    }
    exit 0
}

# Everything after the name is the value, so an unquoted phrase still works.
$value = ($rest[1..($rest.Count - 1)] -join ' ')
if (Set-One -Item $item -Value $value -File $file) {
    Write-Host "$dim  Saved to $file$reset"
    exit 0
}
exit 1
