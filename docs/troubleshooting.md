# Troubleshooting

[← README](../README.md)

| Symptom | Cause and fix |
|---|---|
| `voice server not reachable` in game | the server isn't running — `run_server.bat` |
| The mod doesn't load | check `ScriptHookVDotNet.log` in the GTA folder; .NET Framework 4.8 is required |
| No sound, but no errors in the log | `Audio/Volume` = 0, or the sound went to the wrong Windows device |
| `No RVC models in ...` | put a `.pth` into `models/rvc/` — otherwise you get the plain Piper voice |
| RVC crashes with CUDA OOM | close other apps or set `CFT_RVC_DEVICE=cpu` |
| The first line takes forever | that's normal: models are loaded lazily |
| The phone never rings at all | there's no `iFruit Jailbreak.dll` in `scripts\` |
| The call never arrives even though the voice line is ready | the phone is busy or the player is in a cutscene — the mod waits and calls later |
| The line gets cut off on the last word | raise `Call/HangUpDelayMs` |
| No `connected to #channel` message | `[Twitch] Enabled = false`, or `Channel` is empty |
| `Twitch disconnected: ...` on startup | check the channel name and your internet; on a typo the mod keeps retrying silently |
| People type in chat but no calls happen | wrong `Command`, the cooldown hasn't expired (`UserCooldownSeconds`), or `ModsOnly`/`SubsOnly` is on |
| Chat lines arrive with a delay | that's by design: a queue, one line at a time |
| The call screen doesn't show the viewer's name | `UseViewerName = false` in `[Twitch]` |
| Points/donations don't trigger anything | `events` is empty in `/health` — `events.env` isn't filled in, or `CFT_EVENTS=0` |
| `the voice server has no /call route` | the server is an old build; update the `voice_server\` folder |
| Nothing happens while the game is paused | it should: synthesis runs on the server, and the call is handed over on the first frame after you unpause. If it doesn't, the server is an old build |
| The reward fired but no call came | `AllowPoints` is off, or the reward name doesn't match `CFT_POINTS_REWARD` |
| Every reward triggers a call | `CFT_POINTS_REWARD` is empty — write in the exact name of your reward |
| `EventSub: subscription revoked` | the streamer token expired (lives ~60 days) — get a new one |
| A donation went through with nothing happening | the amount is below `MinDonation`, or `AllowDonations = false` |

## Why the install is this involved

`rvc-python` pulls in `fairseq` (from 2022), which won't install on modern
Windows without workarounds. `setup.bat` does them for you, but if you're
installing by hand, here's the full list of pitfalls:

| Problem | Workaround |
|---|---|
| `omegaconf==2.0.6` — metadata that pip ≥24.1 rejects | `pip==23.3.2` inside the venv |
| pip doesn't see MSVC | `vswhere` → `vcvars64.bat` |
| torch: "VC environment activated but DISTUTILS_USE_SDK is not set" | `set DISTUTILS_USE_SDK=1` |
| fairseq's `setup.py` imports torch | `--no-build-isolation` |
| **The fairseq 0.12.2 sdist is missing `balanced_assignment.cpp`**, even though its own `setup.py` compiles that file | install from the `v0.12.2` git tag |
| `setup.py` calls `os.symlink` — which needs admin rights on Windows | copy `examples` → `fairseq/examples` beforehand |
| pip clones into a temp folder that git considers "dubious ownership" | clone it yourself, install from the local folder |
| A ~5 GB venv in OneDrive / on a full C: drive | the venv is placed on another drive |
