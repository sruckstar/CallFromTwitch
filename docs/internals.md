# How it works

[← README](../README.md)

```
!call in chat  ->  queue  ->  text
 (Twitch IRC, points,
  donations)
                                 |
           Piper TTS  ->  neutral speech (WAV)  ->  RVC  ->  character voice
           (Python, local)                        (Python, GPU)
                                 |
                           phone filter
                           (phone_fx.py)
                                 |
                           incoming call
                           (iFruit Jailbreak)
                                 |
            player picked up  ->  NAudio (C#, in game)
```

The finished WAV isn't played right away: it **waits**. After
`Call/DelaySeconds` seconds the mod raises the incoming call screen (that's
done by [iFruit Jailbreak](https://github.com/sruckstar/iFruitJailbreak) — it
takes over a character slot in the character sheet and draws the call exactly
the way the game itself does), and the voice line only starts the moment you
answer. Decline it or miss it, and the line disappears along with the call.

Why two models instead of one: RVC changes the **timbre**, but it can't read
text — it needs a ready audio stream. Piper provides that stream quickly and
locally.

Before it's handed over, the voice runs through a **phone filter** — a
300–3400 Hz band, light saturation, compression and codec artifacts — so the
line sounds "down the receiver", like the phone dialogue in GTA itself. The
game's submixes are no good for this: they only affect sounds played by the
game's audio engine, while our WAV goes through NAudio straight to the output
device, bypassing the engine. So the effect is built by hand — see
`voice_server/phone_fx.py`.

Both halves are free and work offline.

## Layout

```
CallFromTwitch/
├── src/CallFromTwitch/          # C# mod (SHVDN3)
│   ├── CallFromTwitchScript.cs  # the tick: submit text, collect ready calls
│   ├── TwitchIrcClient.cs       # chat connection (IRC/TLS), reconnect
│   ├── IrcLine.cs               # parsing an IRC line with IRCv3 tags
│   ├── TwitchSource.cs          # !call, permissions, bits, cooldowns, queue
│   ├── CallFeed.cs              # collecting spoken calls: /call -> audio -> ack
│   ├── MiniJson.cs              # JSON parsing without external DLLs
│   ├── TwitchConfig.cs          # reading the [Twitch] section
│   ├── IVoiceSource.cs          # shared interface for text sources
│   ├── VoiceRequest.cs          # a line + its author
│   ├── IncomingCall.cs          # delay -> call -> answer -> voice line
│   ├── VoiceClient.cs           # HTTP client for the server
│   ├── AudioPlayer.cs           # playback through NAudio
│   └── ModConfig.cs             # reading the INI
├── voice_server/                # Python server
│   ├── server.py                # FastAPI: /submit, /call, /call/ack, /health
│   ├── calls.py                 # synthesis queue; holds calls until acked
│   ├── events.py                # EventSub (points) and DonationAlerts (donations)
│   ├── test_events.py           # donation and points emulator
│   ├── test_events.bat          # same thing, one-click launch
│   ├── events.env.example       # token template (copied to events.env)
│   ├── tts_engine.py            # Piper
│   ├── rvc_engine.py            # RVC
│   ├── phone_fx.py              # the "down the receiver" phone filter
│   ├── config.py                # settings from CFT_*
│   ├── setup.bat                # install (asks where to)
│   ├── run_server.bat           # start the server
│   ├── _banner.ps1              # ASCII logo for the console
│   ├── _pick_dir.ps1            # drive selection during install
│   ├── _gta_path.ps1            # finding GTA V (registry, Steam, asking)
│   ├── _ini.ps1                 # reading/writing INI without losing comments
│   ├── _config.ps1              # cft config
│   ├── _where.ps1               # cft where
│   ├── _status.ps1              # cft status
│   ├── _done.ps1                # the "all set" screen after install
│   └── _uninstall.ps1           # cft uninstall
├── lib/                         # iFruit Jailbreak.dll for building (not in git)
├── cft.cmd                      # one command for everything: install/deploy/config
├── install.ps1                  # put cft on the PATH
├── bootstrap.ps1                # clone + install.ps1 in one command
├── deploy.ps1                   # build + copy into GTA
└── CallFromTwitch.ini           # mod config
```

## Dependency licenses

- **Piper** — GPL-3.0. This matters if you plan to distribute the mod in closed
  form: the server talks to the mod over HTTP and runs as a separate process,
  which settles the linking question, but Piper itself still has to be
  distributed under the GPL.
- **RVC** — MIT.
- **NAudio** — MIT.
- **iFruit Jailbreak** — a separate mod, referenced only at compile time;
  this repository does not distribute its DLL.
