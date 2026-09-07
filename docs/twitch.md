# Twitch

[← README](../README.md)

### Connecting

1. The easiest way is the command (it finds the file itself):

```bat
cft config Enabled true
cft config Channel YOUR_CHANNEL
```

   Or by hand: open `GTA V\scripts\CallFromTwitch.ini`, section `[Twitch]`:

```ini
[Twitch]
Enabled = true
Channel = YOUR_CHANNEL
```

   `Channel` is **the channel name, not a link**: SHVDN's INI parser treats `:`
   as a separator just like `=`, so out of `https://twitch.tv/kreyg` only
   `https` would ever reach the mod. For the same reason `[Server]` splits
   `Host` and `Port` apart. If you do write a link, the mod will say so on screen.

2. Start the server and the game. On startup a corner notification appears:
   `CallFromTwitch: connected to #channel (anonymous)` — that means chat is
   being read.
3. Type `!call hey, what's going on out there` in your own chat.

**No token needed.** The mod joins chat anonymously (`justinfan`) — which is
enough for chat, subscribers and bits: Twitch puts both the subscriber badge
and the bits amount right into the message tags.

You'll only need a token for channel points and donations — and it goes not
here but into `voice_server\events.env` (see "Setting up points and donations"
below). `Username`/`Token` in the INI only matter the day the mod needs to
**write** to chat. If you do fill in a token, it's stored in the INI in plain
text, so don't show that file on stream.

### How it behaves

While a call is in progress, further lines **wait in the queue** (`MaxQueue`)
rather than disappearing: the viewer who typed the command will hear their call
sooner or later. The queue is strictly first come, first served; once it's
full, new messages are dropped — the lines already in it have waited longer,
and losing them to a fresher message would be wrong.

The viewer's name goes on the call screen (`UseViewerName = true`). The contact
picture still comes from `Call/CallerIcon` — there's nowhere to get the
viewer's own photo from.

The mod handles disconnects on its own: Twitch regularly moves clients between
servers, so reconnecting with a growing backoff is built in and isn't shown on
screen. An error message only appears if the connection never succeeded once —
for example, when the channel name has a typo.

Chat lines go through the same cleanup as text from the file: line breaks are
joined, control characters and Twitch's invisible marker for repeated messages
are stripped.

As for language, the same limitation applies as with the file: Piper's
reference voice is American, and RVC preserves the accent. Cyrillic will reach
the server intact, but it will be read as English.

### Who can request a call

There are four ways, and they **add up** rather than excluding each other.
Everything is configured in `[Twitch]`:

| Way | Key | Token needed | Who reads it |
|---|---|---|---|
| 1. Anyone in chat via `!call` | `AllowChat = true` | no | the mod, from chat |
| 2. Subscribers only via `!call` | `AllowSubs = true` | no | the mod, from chat |
| 3. For bits (Cheer) | `AllowBits = true` + `MinBits` | no | the mod, from chat |
| 4. For channel points | `AllowPoints = true` | **yes** | voice_server |
| 5. For a DonationAlerts donation | `AllowDonations = true` + `MinDonation` | **yes** | voice_server |

Typical combinations:

```ini
; Subscribers only, free
AllowChat = false
AllowSubs = true

; Subs free, everyone else pays 100 bits
AllowChat = false
AllowSubs = true
AllowBits = true
MinBits   = 100

; Channel points only
AllowChat   = false
AllowPoints = true
```

You can't turn all five off: in that case the mod falls back to
`AllowChat = true`, since otherwise it would connect to chat and silently
answer no one.

**The streamer and moderators are never restricted** — otherwise the only way
to test your own mod would be to donate to yourself.

The old `ModsOnly` / `SubsOnly` still work and **narrow** what's allowed above,
so there's no need to change an INI you already have.

### Why points and donations go through the server

The mod reads the first three ways itself: Twitch puts both the subscriber
badge and the bits amount right into the message tags, and chat can be read
anonymously. The other two can't be obtained that way:

- **Channel points** live only in EventSub, which won't open without an OAuth
  token from **the streamer themself**, with the `channel:read:redemptions`
  scope. A bot token won't do: only the channel's owner can read its rewards.
- **Money donations** Twitch doesn't see at all. Only the service that took the
  payment knows about them — here, DonationAlerts.

Holding two websockets and a JSON parser inside a .NET 4.8 game script means
extra DLLs in `scripts\` and one more way to crash the game mid-frame. So the
sockets are held by the Python server, which is running anyway.

The server does the whole pipeline itself, and this is not just tidiness: a
script gets **no tick at all** while the player sits in the pause menu, so a
mod that waited for its own synthesis did nothing for as long as the menu was
up. The server has no such problem. It speaks the line on its own thread and
then keeps offering the finished call — once a second, forever — until the mod
answers "got it", which it can only do once the player is back in the game.

```
chat / subs / bits ──IRC──► mod ──POST /submit──┐
points ──EventSub──────────────────────────────►├─► voice_server (speaks it)
donations ──DonationAlerts─────────────────────►┘          │
                                                           ▼
                     mod ◄──GET /call──── "ready" (repeated until acked)
                      └──POST /call/ack──► call is released
```

### Setting up points and donations

The tokens live next to the server, not in the INI:

```bat
cd voice_server
copy events.env.example events.env
notepad events.env
```

The file documents where to get each value. In short:

1. `CFT_EVENTS=1` — the master switch.
2. **Client ID**: https://dev.twitch.tv/console/apps → Register Your Application,
   Redirect URL `http://localhost:3000`.
3. **Streamer token**: https://twitchtokengenerator.com, scope
   `channel:read:redemptions`, authorize with **your own channel**. Lives ~60 days.
4. **Reward**: create it in the Creator Dashboard → Viewer Rewards → Channel
   Points, making sure to enable "Require Viewer to Enter Text", and write its
   exact name into `CFT_POINTS_REWARD`. If you leave it empty, **any** reward
   will trigger a call, including "highlight my message".
5. **DonationAlerts** (if you want money): https://www.donationalerts.com/application/clients,
   scope `oauth-donation-subscribe` → `CFT_DA_TOKEN`.

Then enable receiving in `CallFromTwitch.ini`: `AllowPoints = true` and/or
`AllowDonations = true`.

`events.env` holds tokens in plain text and is listed in `.gitignore`.
Don't show it on stream.

You can confirm the server picked them up via `/health`:

```bat
curl http://127.0.0.1:8765/health
```

The response should contain `"events":["twitch-points"]` (and/or
`"donationalerts"`). An empty list means the tokens aren't filled in, or
`CFT_EVENTS=0`.

### Testing without spending money

You **can't** donate to yourself or buy your own channel points. That's why the
server has an emulator: it puts a fake event into the same queue, by the same
path as a real one. The only fake part is where it came from — the filters,
thresholds, queue, synthesis and call are exactly the ones a live viewer gets.

```bat
cd voice_server
test_events.bat
```

The menu offers: points, a donation, bits, a reward with no text (to check
`DefaultEventText`) and three events in a row (to check the queue). There's a
direct invocation too:

```bat
test_events.bat points "Hey Michael, where are you"
test_events.bat donation 500 "Thanks for the stream"
test_events.bat bits 300 "Good luck out there"
```

The order to check things in:

1. Run `run_server.bat`, wait for `Warmup done`.
2. Start the game, wait for the Twitch connection notification.
3. Enable the flag you need in the INI (`AllowPoints` / `AllowDonations`).
4. Run `test_events.bat` and send an event.
5. After `[Call] DelaySeconds` seconds the phone should ring.

If the event went out but no call came, the emulator prints a list of things
worth checking. Most often it's `Enabled = false`, a flag left off, an amount
below `MinDonation`/`MinBits`, or a previous call that hasn't finished — the
mod takes one line at a time.

**Bits can also be tested for real, for free:** set `MinBits = 1` and
temporarily `AllowChat = false`. The streamer's own message will go through
anyway (moderators aren't restricted), so for an honest threshold test ask one
of your viewers — or trust the emulator, which hits the same code path.

To turn off test events entirely — `CFT_EVENTS_TEST=0` in `events.env`. The
server only listens on `127.0.0.1` anyway, so that route can't be reached from
outside.

### What happens to the text of a paid event

- The line is taken from the reward's input field or from the donation message.
- If there's no text (a reward with no input field, a donation with no message),
  `[Twitch] DefaultEventText` is spoken. The viewer paid for a call, so the call
  happens either way.
- The word `Cheer100` is stripped from a bits message — otherwise it would be
  read out loud. Ordinary words with digits (`gate12`) and plain numbers are
  left alone.
- Paid lines are **not subject to the cooldown**: a viewer who spent bits and
  got silence because of someone else's message a minute ago paid for nothing.
- Paid lines go **ahead of** free ones from chat.
