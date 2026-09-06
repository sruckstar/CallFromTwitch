using System;
using System.Collections.Generic;
using System.Text;

namespace CallFromTwitch
{
    /// <summary>
    /// Turns "!call hey, what's going on" in chat into a queued voice line.
    ///
    /// Two threads meet here and nowhere else in the mod. The IRC reader hands
    /// messages in from its own thread; the game thread drains them one per
    /// call in <see cref="TryTake"/>. Everything shared sits behind
    /// <see cref="_gate"/>, and no game API is touched from the reader side -
    /// SHVDN's natives are only valid on the tick thread, and calling one from
    /// a socket thread takes the process down with it.
    ///
    /// Messages are queued rather than dropped while a call is up, so a viewer
    /// who typed a command always gets one eventually.
    /// </summary>
    internal sealed class TwitchSource : IVoiceSource
    {
        private readonly TwitchConfig _config;
        private readonly TwitchIrcClient _client;

        private readonly object _gate = new object();
        private readonly Queue<VoiceRequest> _queue = new Queue<VoiceRequest>();

        // Wall-clock, not Game.GameTime: cooldowns are read and written from
        // the IRC thread, which has no game clock, and a viewer's idea of "one
        // message a minute" does not pause with the game.
        private readonly Dictionary<string, DateTime> _lastUseUtc =
            new Dictionary<string, DateTime>(StringComparer.OrdinalIgnoreCase);
        private DateTime _lastGlobalUseUtc = DateTime.MinValue;

        // Status is produced on the IRC thread and shown on the game thread.
        private string _pendingStatus;
        private bool _pendingStatusIsError;
        private bool _everConnected;

        // Lines thrown away for age or for a full queue, waiting to be reported.
        private int _dropped;

        public TwitchSource(TwitchConfig config)
        {
            _config = config;
            _client = new TwitchIrcClient(config.Channel, config.Username, config.Token);
            _client.MessageReceived += OnMessageReceived;
            _client.StatusChanged += OnStatusChanged;
            _client.Start();
        }

        public string Description
        {
            get { return "Twitch #" + _client.Channel; }
        }

        /// <summary>A status line to show once, or null. Cleared by reading it.</summary>
        public string TakeStatus(out bool isError)
        {
            lock (_gate)
            {
                string status = _pendingStatus;
                isError = _pendingStatusIsError;
                _pendingStatus = null;
                return status;
            }
        }

        public void Update(int gameTime)
        {
            // The client reconnects on its own thread; nothing to drive here.
        }

        public VoiceRequest TryTake(int gameTime)
        {
            lock (_gate)
            {
                // A pause menu, a cutscene or a mission holding the phone can
                // leave a line here long after its chat has moved on.
                while (_queue.Count > 0)
                {
                    VoiceRequest request = _queue.Dequeue();
                    if (!IsStale(request))
                    {
                        ModLog.Write("taking queued line ({0} left)", _queue.Count);
                        return request;
                    }

                    ModLog.Write("drop: line aged out ({0}s > MaxAgeSeconds {1})",
                        (int)request.AgeSeconds, _config.MaxAgeSeconds);
                    _dropped++;
                }

                return null;
            }
        }

        private bool IsStale(VoiceRequest request)
        {
            return _config.MaxAgeSeconds > 0 && request.AgeSeconds > _config.MaxAgeSeconds;
        }

        /// <summary>
        /// How many lines have been dropped for age or a full queue since this
        /// was last read, and zero after reading. A viewer whose line was
        /// dropped just sees silence, so the streamer has to be told.
        /// </summary>
        public int TakeDroppedCount()
        {
            lock (_gate)
            {
                int dropped = _dropped;
                _dropped = 0;
                return dropped;
            }
        }

        /// <summary>How many lines are waiting. Shown to the player on request.</summary>
        public int QueueLength
        {
            get
            {
                lock (_gate)
                {
                    return _queue.Count;
                }
            }
        }

        /// <summary>Drops everything waiting - the stop key clears the backlog too.</summary>
        public void ClearQueue()
        {
            lock (_gate)
            {
                _queue.Clear();
            }
        }

        // ---- IRC thread from here down ----

        private void OnStatusChanged(string message, bool connected)
        {
            ModLog.Write("irc status: {0}", message);

            lock (_gate)
            {
                // A reconnect after a working session is routine; only the
                // first failure to ever connect is worth a banner.
                if (!connected && _everConnected)
                    return;

                if (connected)
                    _everConnected = true;

                _pendingStatus = message;
                _pendingStatusIsError = !connected;
            }
        }

        private void OnMessageReceived(TwitchMessage message)
        {
            bool paid;
            bool exempt;
            if (!IsAllowed(message, out paid, out exempt))
            {
                ModLog.Write("drop {0}: not allowed (AllowChat={1} AllowSubs={2} AllowBits={3} sub={4} bits={5})",
                    message.Login, _config.AllowChat, _config.AllowSubs, _config.AllowBits,
                    message.IsSubscriber, message.Bits);
                return;
            }

            // A cheer is its own request: requiring the command word too would
            // mean taking a viewer's bits and saying nothing.
            string spoken = paid ? StripCommand(message.Text) : ExtractCommand(message.Text);
            if (spoken == null)
            {
                ModLog.Write("drop {0}: not a \"{1}\" command", message.Login, _config.Command);
                return;
            }

            spoken = Sanitize(spoken);

            if (paid)
                spoken = StripCheerTokens(spoken);

            // A bare "Cheer100" leaves nothing to say, but the viewer paid for
            // a call, so it falls back to the configured text.
            if (paid && spoken.Length < _config.MinLength)
                spoken = _config.DefaultEventText;

            if (spoken.Length < _config.MinLength)
            {
                ModLog.Write("drop {0}: {1} chars, MinLength is {2}",
                    message.Login, spoken.Length, _config.MinLength);
                return;
            }

            if (spoken.Length > _config.MaxLength)
                spoken = TrimToLength(spoken, _config.MaxLength);

            lock (_gate)
            {
                // Cheers buy past the cooldown, and so do the broadcaster and
                // their mods: IsAllowed already waved them through.
                if (!paid && !exempt && !PassesCooldownLocked(message.Login))
                {
                    ModLog.Write("drop {0}: cooldown (user {1}s, global {2}s)",
                        message.Login, _config.UserCooldownSeconds, _config.GlobalCooldownSeconds);
                    return;
                }

                // The newest rather than the oldest: evicting a line that has
                // been waiting takes a turn away from a viewer who had one.
                if (_queue.Count >= _config.MaxQueue)
                {
                    ModLog.Write("drop {0}: queue full ({1})", message.Login, _config.MaxQueue);
                    _dropped++;
                    return;
                }

                // A call that never paid the cooldown must not start one
                // either, or a mod testing the mod holds up all of chat.
                if (!exempt)
                    MarkUsedLocked(message.Login);

                _queue.Enqueue(new VoiceRequest(spoken, message.DisplayName));
                ModLog.Write("queued from {0} (paid={1} exempt={2}, queue={3}): {4}",
                    message.DisplayName, paid, exempt, _queue.Count, spoken);
            }
        }

        /// <summary>
        /// The text after the command word, or null when this message is not a
        /// command for us. Matching is case-insensitive and the command must
        /// be followed by whitespace, so "!called it" is not a call.
        /// </summary>
        private string ExtractCommand(string text)
        {
            if (string.IsNullOrEmpty(text))
                return null;

            string trimmed = text.Trim();
            string command = _config.Command;

            if (command.Length == 0)
                return trimmed;      // No command configured: every message counts.

            if (trimmed.Length <= command.Length ||
                !trimmed.StartsWith(command, StringComparison.OrdinalIgnoreCase))
                return null;

            char separator = trimmed[command.Length];
            if (separator != ' ' && separator != '\t')
                return null;

            return trimmed.Substring(command.Length + 1).Trim();
        }

        /// <summary>
        /// The text of a message that has already earned its call, with the
        /// command word removed if it is there. Unlike ExtractCommand this
        /// never rejects: the right to speak was settled before it was called.
        /// </summary>
        private string StripCommand(string text)
        {
            if (string.IsNullOrEmpty(text))
                return string.Empty;

            string trimmed = text.Trim();
            string command = _config.Command;

            if (command.Length == 0 || !trimmed.StartsWith(command, StringComparison.OrdinalIgnoreCase))
                return trimmed;

            // Only when the command stands as a word of its own.
            if (trimmed.Length == command.Length)
                return string.Empty;

            char separator = trimmed[command.Length];
            if (separator != ' ' && separator != '	')
                return trimmed;

            return trimmed.Substring(command.Length + 1).Trim();
        }

        /// <summary>
        /// Removes the cheermote words Twitch leaves in the message text.
        ///
        /// A cheer arrives as "Cheer100 good luck out there", with the amount
        /// in a tag and the word still in the sentence, where read aloud it
        /// becomes "cheer one hundred good luck out there".
        /// </summary>
        private static string StripCheerTokens(string text)
        {
            if (string.IsNullOrEmpty(text))
                return string.Empty;

            string[] words = text.Split(' ');
            var kept = new List<string>(words.Length);

            foreach (string word in words)
            {
                if (word.Length == 0)
                    continue;

                if (!IsCheerToken(word))
                    kept.Add(word);
            }

            return string.Join(" ", kept.ToArray());
        }

        /// <summary>A cheermote is a name followed by digits and nothing else.</summary>
        private static bool IsCheerToken(string word)
        {
            int digits = 0;
            while (digits < word.Length && char.IsDigit(word[word.Length - 1 - digits]))
                digits++;

            // No trailing number, or nothing but one: an ordinary word, or a
            // figure the viewer meant to say.
            if (digits == 0 || digits == word.Length)
                return false;

            string name = word.Substring(0, word.Length - digits);
            foreach (string prefix in CheerPrefixes)
            {
                if (string.Equals(name, prefix, StringComparison.OrdinalIgnoreCase))
                    return true;
            }

            return false;
        }

        // Twitch's global cheermotes. Custom ones need an API call to know,
        // and a stray one spoken beats eating a word the viewer meant.
        private static readonly string[] CheerPrefixes =
        {
            "Cheer", "BibleThump", "cheerwhal", "Corgo", "uni", "ShowLove",
            "Party", "SeemsGood", "Pride", "Kappa", "FrankerZ", "HeyGuys",
            "DansGame", "EleGiggle", "TriHard", "Kreygasm", "4Head", "SwiftRage",
            "NotLikeThis", "VoHiYo", "PJSalt", "MrDestructoid", "bday", "RIPCheer",
            "Shamrock", "Streamlabs", "Muxy", "HolidayCheer", "Goal", "Anon"
        };

        /// <param name="paid">The viewer bought this call, so no cooldown applies.</param>
        /// <param name="exempt">The broadcaster or a moderator.</param>
        /// <remarks>
        /// The four ways in are additive - a message qualifies if any enabled
        /// one accepts it - so a channel can let subscribers call for free
        /// while everyone else cheers. Bits are read off the IRC tag, so
        /// cheering needs neither a token nor the server.
        /// </remarks>
        private bool IsAllowed(TwitchMessage message, out bool paid, out bool exempt)
        {
            paid = false;
            exempt = false;

            // Never gated: they are the ones testing the thing, and a streamer
            // cannot cheer at their own channel.
            if (message.IsBroadcaster || message.IsModerator)
            {
                exempt = true;
                return true;
            }

            if (_config.AllowBits && message.Bits >= _config.MinBits)
            {
                // Cheering is a payment, so it buys past the cooldown.
                paid = true;
                return true;
            }

            if (_config.AllowChat)
                return true;

            if (_config.AllowSubs && message.IsSubscriber)
                return true;

            return false;
        }

        private bool PassesCooldownLocked(string login)
        {
            DateTime now = DateTime.UtcNow;

            if (_config.GlobalCooldownSeconds > 0 &&
                (now - _lastGlobalUseUtc).TotalSeconds < _config.GlobalCooldownSeconds)
                return false;

            if (_config.UserCooldownSeconds > 0)
            {
                DateTime last;
                if (_lastUseUtc.TryGetValue(login, out last) &&
                    (now - last).TotalSeconds < _config.UserCooldownSeconds)
                    return false;
            }

            return true;
        }

        private void MarkUsedLocked(string login)
        {
            DateTime now = DateTime.UtcNow;
            _lastGlobalUseUtc = now;

            if (_config.UserCooldownSeconds <= 0)
                return;

            _lastUseUtc[login] = now;

            // The map would otherwise hold every viewer who ever called. An
            // entry past its cooldown can no longer block anyone.
            if (_lastUseUtc.Count > 512)
                PruneExpiredLocked(now);
        }

        private void PruneExpiredLocked(DateTime now)
        {
            var stale = new List<string>();
            foreach (var entry in _lastUseUtc)
            {
                if ((now - entry.Value).TotalSeconds >= _config.UserCooldownSeconds)
                    stale.Add(entry.Key);
            }

            foreach (string key in stale)
                _lastUseUtc.Remove(key);
        }

        /// <summary>
        /// Folds a chat message into something a TTS engine can read aloud:
        /// plain text on a single line, without the control characters or the
        /// invisible duplicate-message suffix Twitch appends.
        /// </summary>
        private static string Sanitize(string text)
        {
            var sb = new StringBuilder(text.Length);
            bool lastWasSpace = true;

            foreach (char c in text)
            {
                // U+E0000, as a surrogate pair: the tag Twitch adds to bypass
                // its own duplicate-message filter.
                if (c == (char)0xDB40 || c == (char)0xDC00 || c == (char)0xFEFF)
                    continue;

                bool isSpace = c == ' ' || c == '\t' || c == '\r' || c == '\n';
                if (isSpace)
                {
                    if (!lastWasSpace)
                        sb.Append(' ');
                    lastWasSpace = true;
                    continue;
                }

                // Control characters, including the \x01ACTION wrapper around
                // a /me message.
                if (c < 0x20 || c == 0x7F)
                    continue;

                sb.Append(c);
                lastWasSpace = false;
            }

            if (sb.Length > 0 && sb[sb.Length - 1] == ' ')
                sb.Length = sb.Length - 1;

            return sb.ToString();
        }

        /// <summary>
        /// Cuts an over-long line at a word boundary, so it ends on a whole
        /// word rather than mid-syllable - the difference is audible.
        /// </summary>
        private static string TrimToLength(string text, int max)
        {
            if (text.Length <= max)
                return text;

            string cut = text.Substring(0, max);
            int lastSpace = cut.LastIndexOf(' ');

            // Unless the boundary throws away most of the allowance: a single
            // very long word should still be spoken.
            if (lastSpace > max / 2)
                cut = cut.Substring(0, lastSpace);

            return cut.TrimEnd();
        }

        public void Dispose()
        {
            _client.MessageReceived -= OnMessageReceived;
            _client.StatusChanged -= OnStatusChanged;
            _client.Dispose();
        }
    }
}
