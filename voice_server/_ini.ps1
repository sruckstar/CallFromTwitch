<#
    Reading and writing CallFromTwitch.ini without disturbing it.

    The file is not just data - it is also the documentation the player reads,
    a comment above nearly every key. So editing is done in place: find the
    line for one key, replace the value on that line, leave every other byte
    alone. A parse-and-rewrite would produce a technically equal file with all
    the guidance stripped out, which is a bad trade for a config people are
    expected to open and read.

    Dot-source for the functions; there is no stand-alone mode.
#>

<#
    Every key in the file, as a value the editor can reason about: what it
    means in plain words, what it accepts, and what counts as valid. The
    ranges mirror the Clamp() calls in ModConfig.cs / TwitchConfig.cs, so the
    editor refuses what the mod would silently have clamped.

    Type drives both the prompt and the validation:
      text    free string          choice  one of Options
      int     whole number, Min..Max        bool    true / false
      float   decimal, Min..Max             key     a Windows key name
#>
function Get-CftIniSchema {
    @(
        # ---- Twitch: the reason most people open this file at all ----
        @{ Section='Twitch'; Key='Enabled'; Type='bool'; Default='false'
           Title='Read Twitch chat'
           Help='Off = only channel points and donations can place a call.' }
        @{ Section='Twitch'; Key='Channel'; Type='text'; Default=''
           Title='Channel to listen to'
           Help='Just the name, no link. For twitch.tv/kreyg type: kreyg' }
        @{ Section='Twitch'; Key='Command'; Type='text'; Default='!call'
           Title='Chat command'
           Help='Viewers type this before their line. Empty = every message is voiced (careful).' }
        @{ Section='Twitch'; Key='ModsOnly'; Type='bool'; Default='false'
           Title='Moderators only'
           Help='Only your mods can trigger a call.' }
        @{ Section='Twitch'; Key='SubsOnly'; Type='bool'; Default='false'
           Title='Subscribers only'
           Help='Only subscribers can trigger a call.' }
        @{ Section='Twitch'; Key='UserCooldownSeconds'; Type='int'; Min=0; Max=3600; Default='60'
           Title='Cooldown per viewer (sec)'
           Help='How long one viewer waits before calling again. 0 = no limit.' }
        @{ Section='Twitch'; Key='GlobalCooldownSeconds'; Type='int'; Min=0; Max=3600; Default='0'
           Title='Cooldown for everyone (sec)'
           Help='Minimum gap between any two calls. 0 = no limit.' }
        @{ Section='Twitch'; Key='MinLength'; Type='int'; Min=1; Max=100; Default='2'
           Title='Shortest message'
           Help='Messages shorter than this are ignored.' }
        @{ Section='Twitch'; Key='MaxLength'; Type='int'; Min=10; Max=2000; Default='300'
           Title='Longest message'
           Help='Longer lines are cut at a word boundary. Keep at or below 300.' }
        @{ Section='Twitch'; Key='MaxQueue'; Type='int'; Min=1; Max=200; Default='10'
           Title='Queue size'
           Help='How many lines can wait their turn. Extra ones are dropped.' }
        @{ Section='Twitch'; Key='UseViewerName'; Type='bool'; Default='true'
           Title='Show the viewer name'
           Help='On = the caller ID shows who wrote it. Off = always the name below.' }
        @{ Section='Twitch'; Key='Username'; Type='text'; Default=''
           Title='Twitch username'
           Help='Optional. Empty = the mod reads chat anonymously, which is enough.' }
        @{ Section='Twitch'; Key='Token'; Type='text'; Default=''; Secret=$true
           Title='Twitch token'
           Help='Optional, and stored as plain text - never show this file on stream.' }

        # ---- Call: how it looks and feels in game ----
        @{ Section='Call'; Key='CallerName'; Type='text'; Default='Michael'
           Title='Caller name'
           Help='Shown on the phone when UseViewerName is off.' }
        @{ Section='Call'; Key='CallerIcon'; Type='choice'; Default='CHAR_MICHAEL'
           Options=@('CHAR_DEFAULT','CHAR_MICHAEL','CHAR_FRANKLIN','CHAR_TREVOR','CHAR_LESTER',
                     'CHAR_LAMAR','CHAR_AMANDA','CHAR_JIMMY','CHAR_TRACEY','CHAR_CHOP',
                     'CHAR_DEVIN','CHAR_LS_CUSTOMS','CHAR_CALL911')
           Title='Caller picture'
           Help='Any CHAR_* image the game ships.' }
        @{ Section='Call'; Key='DelaySeconds'; Type='int'; Min=0; Max=120; Default='5'
           Title='Delay before ringing (sec)'
           Help='Pause between the voice being ready and the phone ringing.' }
        @{ Section='Call'; Key='RingSeconds'; Type='int'; Min=3; Max=120; Default='20'
           Title='Ring for (sec)'
           Help='How long it rings before the call counts as missed.' }
        @{ Section='Call'; Key='HangUpDelayMs'; Type='int'; Min=0; Max=10000; Default='1200'
           Title='Pause before hanging up (ms)'
           Help='Extra time after the last word, so it is not cut off.' }

        # ---- Voice ----
        @{ Section='Voice'; Key='Character'; Type='text'; Default='Trevor'
           Title='Character voice'
           Help='A folder or .pth name in voice_server\models\rvc\.' }

        # ---- Audio ----
        @{ Section='Audio'; Key='Volume'; Type='float'; Min=0.0; Max=1.0; Default='1.0'
           Title='Volume'
           Help='0.0 is silent, 1.0 is full.' }
        @{ Section='Audio'; Key='ShowSubtitle'; Type='bool'; Default='true'
           Title='Show subtitles'
           Help='Print the line on screen while it plays.' }

        # ---- Server: correct by default; only touched when it is not ----
        @{ Section='Server'; Key='Host'; Type='text'; Default='127.0.0.1'
           Title='Voice server address'
           Help='127.0.0.1 unless the server runs on another PC.' }
        @{ Section='Server'; Key='Port'; Type='int'; Min=1; Max=65535; Default='8765'
           Title='Voice server port'
           Help='Must match the port the server prints when it starts.' }
        @{ Section='Server'; Key='TimeoutSeconds'; Type='int'; Min=5; Max=600; Default='180'
           Title='Give up after (sec)'
           Help='The first call is slow - the server loads its models. Keep this generous.' }
    )
}

<#
    Read one value. Returns $null when the key is absent, which the caller
    shows as the default rather than as an empty string - those mean different
    things to a reader.
#>
function Get-IniValue {
    param(
        [Parameter(Mandatory)][string]$File,
        [Parameter(Mandatory)][string]$Section,
        [Parameter(Mandatory)][string]$Key
    )
    if (-not (Test-Path -LiteralPath $File)) { return $null }

    $inSection = $false
    foreach ($line in (Get-Content -LiteralPath $File -Encoding UTF8)) {
        if ($line -match '^\s*\[(.+?)\]\s*$') {
            $inSection = ($Matches[1] -ieq $Section)
            continue
        }
        if (-not $inSection) { continue }
        if ($line -match '^\s*[;#]') { continue }
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return $null
}

<#
    Write one value, preserving everything else byte for byte.

    Three cases, in order: the key exists in its section (rewrite that line),
    the section exists but the key does not (insert at the end of the section,
    before the trailing blank lines), or neither exists (append both).
#>
function Set-IniValue {
    param(
        [Parameter(Mandatory)][string]$File,
        [Parameter(Mandatory)][string]$Section,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )

    $lines = if (Test-Path -LiteralPath $File) {
        @(Get-Content -LiteralPath $File -Encoding UTF8)
    } else { @() }

    $sectionStart = -1
    $sectionEnd = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[(.+?)\]\s*$') {
            if ($Matches[1] -ieq $Section) {
                $sectionStart = $i
            } elseif ($sectionStart -ge 0) {
                $sectionEnd = $i
                break
            }
        }
    }

    if ($sectionStart -ge 0) {
        # Case 1: rewrite the existing line, keeping its leading whitespace.
        for ($i = $sectionStart + 1; $i -lt $sectionEnd; $i++) {
            if ($lines[$i] -match '^\s*[;#]') { continue }
            if ($lines[$i] -match "^(\s*)$([regex]::Escape($Key))\s*=") {
                $lines[$i] = "$($Matches[1])$Key = $Value"
                Write-IniLines -File $File -Lines $lines
                return
            }
        }

        # Case 2: add it to the section, after the last line that carries
        # content, so it lands under the section rather than after a gap.
        $insertAt = $sectionEnd
        while ($insertAt -gt $sectionStart + 1 -and
               [string]::IsNullOrWhiteSpace($lines[$insertAt - 1])) {
            $insertAt--
        }
        $head = if ($insertAt -gt 0) { $lines[0..($insertAt - 1)] } else { @() }
        $tail = if ($insertAt -lt $lines.Count) { $lines[$insertAt..($lines.Count - 1)] } else { @() }
        $lines = @($head) + @("$Key = $Value") + @($tail)
        Write-IniLines -File $File -Lines $lines
        return
    }

    # Case 3: no such section yet.
    $lines = @($lines)
    if ($lines.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace($lines[-1])) { $lines += '' }
    $lines += "[$Section]"
    $lines += "$Key = $Value"
    Write-IniLines -File $File -Lines $lines
}

<#
    UTF-8 without a BOM, CRLF. ScriptSettings reads the file itself, and a BOM
    on the first line would make the first section header unmatchable.
#>
function Write-IniLines {
    param([Parameter(Mandatory)][string]$File, [string[]]$Lines)
    $text = ($Lines -join "`r`n") + "`r`n"
    [System.IO.File]::WriteAllText($File, $text, (New-Object System.Text.UTF8Encoding($false)))
}

<#
    Is $Value acceptable for the schema entry $Item? Returns the value to
    store (normalised - "yes" becomes "true"), or $null with $Problem set.
#>
function Test-IniValue {
    param([Parameter(Mandatory)][hashtable]$Item, [AllowEmptyString()][string]$Value)

    $value = "$Value".Trim()

    switch ($Item.Type) {
        'bool' {
            if ($value -match '^(true|1|yes|y|on)$') { return @{ Ok = $true; Value = 'true' } }
            if ($value -match '^(false|0|no|n|off)$') { return @{ Ok = $true; Value = 'false' } }
            return @{ Ok = $false; Problem = 'Type true or false.' }
        }
        'int' {
            $n = 0
            if (-not [int]::TryParse($value, [ref]$n)) {
                return @{ Ok = $false; Problem = 'Whole numbers only.' }
            }
            if ($n -lt $Item.Min -or $n -gt $Item.Max) {
                return @{ Ok = $false; Problem = "Must be between $($Item.Min) and $($Item.Max)." }
            }
            return @{ Ok = $true; Value = "$n" }
        }
        'float' {
            $f = 0.0
            if (-not [double]::TryParse($value.Replace(',', '.'),
                    [Globalization.NumberStyles]::Float,
                    [Globalization.CultureInfo]::InvariantCulture, [ref]$f)) {
                return @{ Ok = $false; Problem = 'Numbers only, for example 0.8' }
            }
            if ($f -lt $Item.Min -or $f -gt $Item.Max) {
                return @{ Ok = $false; Problem = "Must be between $($Item.Min) and $($Item.Max)." }
            }
            # Keep a decimal point on a whole number: "1" is a valid float to
            # the parser, but "1.0" is what the file documents and what a
            # reader expects to see in a volume field.
            $text = $f.ToString([Globalization.CultureInfo]::InvariantCulture)
            if ($text -notmatch '[.]') { $text = "$text.0" }
            return @{ Ok = $true; Value = $text }
        }
        'choice' {
            $match = $Item.Options | Where-Object { $_ -ieq $value } | Select-Object -First 1
            if ($match) { return @{ Ok = $true; Value = $match } }
            return @{ Ok = $false; Problem = "Pick one of: $($Item.Options -join ', ')" }
        }
        default {
            # Free text. The parser cuts a value at ':', so a pasted URL would
            # be silently truncated - worth catching where it is typed.
            if ($value -match ':') {
                return @{ Ok = $false
                          Problem = 'Colons are not allowed here - the game cuts the line at ":".' }
            }
            return @{ Ok = $true; Value = $value }
        }
    }
}

<#
    Channel is the field people paste a URL into, so it gets the same cleanup
    the mod does rather than a rejection.
#>
function Format-IniInput {
    param([Parameter(Mandatory)][hashtable]$Item, [AllowEmptyString()][string]$Value)
    $value = "$Value".Trim()
    if ($Item.Section -eq 'Twitch' -and $Item.Key -eq 'Channel') {
        $value = $value -replace '^\s*https?://', ''
        $value = $value -replace '^www\.', ''
        $value = $value -replace '^twitch\.tv/', ''
        # "#kreyg" is how chat writes a channel, so the leading marker goes
        # before the split - otherwise the split treats it as a fragment and
        # leaves nothing behind.
        $value = $value.TrimStart('#')
        $value = ($value -split '[/?#]')[0]
        $value = $value.Trim().ToLowerInvariant()
    }
    return $value
}
