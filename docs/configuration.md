# Configuration

[← README](../README.md)

### `cft config` — no text editor needed

Every setting below can be edited with a command that finds the right file on
its own (the one the game actually reads, in `GTA V\scripts\`), validates the
value and tells you what it means:

```bat
cft config                    menu: sections and every parameter
cft config twitch             straight to the Twitch settings
cft config Channel kreyg      set a single parameter
cft config Channel            show it and offer to change it
cft config --list             show everything at once
cft config --reset Volume     restore the default
cft config --edit             open the file in Notepad
```

Values are checked before they're written: `Volume 9` and a typo in a key name
are rejected with an explanation, a link like `twitch.tv/kreyg` turns itself
into `kreyg`, and a colon in a field where the INI parser truncates the line
won't get through. The file is edited line by line — comments and everything
else stay where they are.

### `CallFromTwitch.ini` (next to the DLL in `scripts\`)

| Key | Default | Description |
|---|---|---|
| `Server/Url` | `http://127.0.0.1:8765` | voice server address |
| `Server/TimeoutSeconds` | `180` | request timeout; generous enough for a cold server |
| `Call/CallerName` | `Michael` | name on the call screen |
| `Call/CallerIcon` | `CHAR_MICHAEL` | contact picture: any `CHAR_*` from the game |
| `Call/DelaySeconds` | `5` | pause between the voice line being ready and the call |
| `Call/RingSeconds` | `20` | how long it rings before going to "missed" |
| `Call/HangUpDelayMs` | `1200` | pause after the line before hanging up |
| `Audio/Volume` | `1.0` | volume 0.0–1.0 |
| `Audio/ShowSubtitle` | `true` | show the subtitle |

### `[Twitch]` — same `CallFromTwitch.ini`

Read by the **voice server**, not by the mod: chat, points and donations all
arrive there, and they keep arriving with GTA closed. The server reads this
file straight out of `GTA V\scripts\`, and rereads it a few seconds after you
save — no restart of either half. (`EventPollMilliseconds` and `UseViewerName`
are the two the mod still reads for itself.)

| Key | Default | Description |
|---|---|---|
| `Enabled` | `false` | read chat; `false` — points and donations only |
| `Channel` | — | channel name, **without** `https://` — see below |
| `Username` | — | bot account; empty — anonymous login |
| `Token` | — | OAuth token; empty — anonymous login |
| `Command` | `!call` | the command; empty — speak **every** message |
| `AllowChat` | `true` | any viewer via the command |
| `AllowSubs` | `false` | subscribers (mods and the streamer too) |
| `AllowBits` | `false` | for bits, no less than `MinBits` |
| `MinBits` | `100` | bits threshold |
| `AllowPoints` | `false` | for channel points — **needs the event server** |
| `AllowDonations` | `false` | for a DonationAlerts donation — **needs the event server** |
| `MinDonation` | `0` | donation threshold; `0` — any amount |
| `EventPollMilliseconds` | `1000` | how often the **mod** asks whether a call is ready |
| `DefaultEventText` | `Hey, thanks for the support!` | the line to use when a paid event carries no text |
| `ModsOnly` | `false` | moderators and the streamer only (narrows `Allow*`) |
| `SubsOnly` | `false` | subscribers only (narrows `Allow*`) |
| `UserCooldownSeconds` | `60` | per-viewer cooldown; `0` — none |
| `GlobalCooldownSeconds` | `0` | shared cooldown for everyone |
| `MinLength` | `2` | shorter than this is ignored |
| `MaxLength` | `300` | longer than this is trimmed at a word boundary |
| `MaxQueue` | `10` | how many calls wait on the server; extras are dropped |
| `MaxAgeSeconds` | `300` | a call older than this is thrown away unheard; `0` — keep forever |
| `UseViewerName` | `true` | viewer's name on the call screen instead of `Call/CallerName` |

### Server — `CFT_*` environment variables

| Variable | Default | Description |
|---|---|---|
| `CFT_PORT` | `8765` | port |
| `CFT_PIPER_MODEL` | `models/piper/en_US-ryan-high.onnx` | Piper model |
| `CFT_PIPER_SENTENCE_SILENCE` | `0.4` | pause between sentences, sec (`0` — back to back) |
| `CFT_PIPER_COMMA_PAUSE` | `0.09` | maximum pause WITHIN a sentence, sec: keeps a comma from sounding like a full stop (`0` — Piper's timing as is) |
| `CFT_RVC_DEVICE` | `cuda:0` | `cpu` if you have no NVIDIA card |
| `CFT_RVC_ENABLED` | `true` | `false` — Piper only, no voice conversion |
| `CFT_RVC_F0METHOD` | `rmvpe` | `rmvpe` / `harvest` / `crepe` / `pm` |
| `CFT_RVC_F0UP_KEY` | `0` | pitch shift in semitones |
| `CFT_RVC_INDEX_RATE` | `0.5` | how strongly to pull toward the model's timbre |
| `CFT_PHONE_ENABLED` | `true` | `false` — dry voice, no phone filter |
| `CFT_PHONE_PRESET` | `gta` | `gta` / `radio` / `soft` (see below) |
| `CFT_QUIET` | `1` via `cft start` | terse console output; `cft start --verbose` turns it off |
| `CFT_VOICE_PROMPT` | `true` | ask which voice to use at startup when there are several models |
| `CFT_NO_COLOR` | — | disable color and the logo for older consoles |

Points and donation settings don't go here but into `voice_server\events.env`
(template — `events.env.example`), because they contain tokens:

| Variable | Default | Description |
|---|---|---|
| `CFT_EVENTS` | `0` | master switch for points and donations |
| `CFT_TWITCH_CHANNEL` | — | the channel whose rewards to listen to |
| `CFT_TWITCH_CLIENT_ID` | — | Client ID of an app from dev.twitch.tv |
| `CFT_TWITCH_TOKEN` | — | the **streamer's** token, scope `channel:read:redemptions` |
| `CFT_POINTS_REWARD` | — | reward name; empty — **any** reward qualifies |
| `CFT_DA_TOKEN` | — | DonationAlerts token, scope `oauth-donation-subscribe` |
| `CFT_EVENTS_QUEUE` | `50` | how many events wait for the game to pick them up |
| `CFT_EVENTS_TEST` | `1` | allow `POST /events/test` — the donation emulator |

#### The phone effect

Presets (`CFT_PHONE_PRESET`):

| Preset | Sound |
|---|---|
| `gta` | like calls in GTA 5: you hear the receiver, but every word stays clear |
| `radio` | police radio: narrower band, harsher distortion and artifacts |
| `soft` | coloration only, no noise or artifacts |

Any preset parameter can be overridden individually — whatever you don't set is
taken from the preset:

| Variable | `gta` | Description |
|---|---|---|
| `CFT_PHONE_LOW_HZ` | `300` | lower band edge, Hz |
| `CFT_PHONE_HIGH_HZ` | `3400` | upper band edge, Hz |
| `CFT_PHONE_ORDER` | `6` | slope steepness (filter order per edge) |
| `CFT_PHONE_DRIVE` | `1.8` | saturation; `1.0` — off |
| `CFT_PHONE_COMPRESS_RATIO` | `3.0` | compression ratio; `1.0` — off |
| `CFT_PHONE_COMPRESS_THRESHOLD` | `0.25` | compression threshold |
| `CFT_PHONE_CODEC_RATE` | `8000` | "codec" rate, Hz; `0` — no artifacts |
| `CFT_PHONE_CODEC_BITS` | `0` | quantization at that rate; `0` — off |
| `CFT_PHONE_NOISE_DB` | `-48` | line noise, dBFS; `0` — no noise |
| `CFT_PHONE_OUTPUT_PEAK` | `0.97` | peak after normalization |

For example, "a radio, but a bit softer with quieter noise":
`set CFT_PHONE_PRESET=radio` + `set CFT_PHONE_DRIVE=2.5` + `set CFT_PHONE_NOISE_DB=-55`.
