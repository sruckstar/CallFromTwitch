using System;
using System.Threading;
using System.Threading.Tasks;

namespace CallFromTwitch
{
    /// <summary>
    /// Collects calls the server has already spoken, and confirms each one.
    ///
    /// This is the whole answer to the pause menu. A script gets no tick while
    /// the player sits in one - no timers, no continuations, nothing - so the
    /// mod cannot be the thing that waits for a minute of synthesis. It asks
    /// the server for a finished call instead, which is a question that can be
    /// asked and answered inside a single frame.
    ///
    /// The server keeps offering the same call until it is acked, and the ack
    /// is only sent once the audio is in hand and the game is in a state that
    /// can ring a phone. So a redemption bought while the menu was up is
    /// spoken during the menu, offered on every poll the menu allows (none),
    /// and taken on the first frame after the player comes back.
    /// </summary>
    internal sealed class CallFeed : IDisposable
    {
        private readonly VoiceClient _client;
        private readonly TwitchConfig _config;

        // Wall-clock, not Game.GameTime: the game clock stops with the game,
        // and this schedule has to survive exactly that.
        private DateTime _nextPollUtc = DateTime.MinValue;
        private readonly TimeSpan _pollInterval;

        // One request in flight at a time, per stage. A server inside its
        // 60-second warmup would otherwise collect one of each per tick.
        private Task<string> _poll;
        private Task<byte[]> _download;
        private Task<bool> _ack;

        // The call currently being fetched, and the one already fetched and
        // waiting for the game to be free.
        private string _downloadingId;
        private ReadyCall _ready;
        // Acked and gone, but the poll that was already in flight may still
        // name it; without this the same call is taken twice.
        private string _lastAckedId;

        private string _pendingStatus;
        private int _consecutiveFailures;
        private bool _everAnswered;

        public CallFeed(TwitchConfig config, VoiceClient client)
        {
            _config = config;
            _client = client;
            _pollInterval = TimeSpan.FromMilliseconds(config.EventPollMilliseconds);
        }

        /// <summary>A call whose audio is in hand, waiting to be rung.</summary>
        private sealed class ReadyCall
        {
            public string Id;
            public byte[] Audio;
            public string Text;
            public string User;
        }

        /// <summary>A status line to show once, or null. Cleared by reading it.</summary>
        public string TakeStatus()
        {
            string status = _pendingStatus;
            _pendingStatus = null;
            return status;
        }

        /// <summary>
        /// Drives one frame of the fetch: collect what finished, start what is
        /// due. Nothing here blocks, so a slow server costs no frame time.
        /// </summary>
        public void Update()
        {
            CollectAck();
            CollectDownload();
            CollectPoll();

            // Nothing new is asked for while a call is already in hand: the
            // server holds the rest, and it holds them better than we would.
            if (_ready != null || _downloadingId != null || _poll != null)
                return;

            DateTime now = DateTime.UtcNow;
            if (now < _nextPollUtc)
                return;

            // Measured from now rather than from the due time, so an hour in a
            // menu costs one poll on the way out, not an hour of them.
            _nextPollUtc = now + _pollInterval;

            try
            {
                _poll = _client.FetchCallAsync(CancellationToken.None);
            }
            catch (Exception)
            {
                // A malformed base address, already reported elsewhere.
                _poll = null;
            }
        }

        private void CollectPoll()
        {
            if (_poll == null || !_poll.IsCompleted)
                return;

            Task<string> finished = _poll;
            _poll = null;

            if (finished.Status != TaskStatus.RanToCompletion)
            {
                NoteFailure();
                return;
            }

            string body = finished.Result;
            if (body == null)
            {
                // Answered, but not with a call: most likely an older build
                // with no /call route.
                NoteMissingRoute();
                return;
            }

            _consecutiveFailures = 0;
            _everAnswered = true;

            string id;
            try
            {
                object document = MiniJson.Parse(body);
                object call = MiniJson.Field(document, "call");
                if (call == null)
                    return;

                id = MiniJson.String(call, "id");
                if (id.Length == 0)
                    return;

                // The poll that was already in flight when we acked still
                // names the call we just took.
                if (id == _lastAckedId)
                    return;

                _pendingText = MiniJson.String(call, "text");
                _pendingUser = MiniJson.String(call, "user", "Viewer");
            }
            catch (FormatException)
            {
                // Something on that port that is not our server.
                NoteFailure();
                return;
            }

            StartDownload(id);
        }

        private string _pendingText;
        private string _pendingUser;

        private void StartDownload(string id)
        {
            _downloadingId = id;
            try
            {
                ModLog.Write("call {0} is ready on the server; downloading", id);
                _download = _client.FetchCallAudioAsync(id, CancellationToken.None);
            }
            catch (Exception)
            {
                _downloadingId = null;
                _download = null;
            }
        }

        private void CollectDownload()
        {
            if (_download == null || !_download.IsCompleted)
                return;

            Task<byte[]> finished = _download;
            string id = _downloadingId;
            _download = null;
            _downloadingId = null;

            if (finished.Status != TaskStatus.RanToCompletion)
            {
                // Not acked, so the server offers it again on the next poll.
                // That is the failure mode this design is built for.
                ModLog.Write("could not download call {0}; the server will offer it again", id);
                NoteFailure();
                return;
            }

            _ready = new ReadyCall
            {
                Id = id,
                Audio = finished.Result,
                Text = _pendingText,
                User = _pendingUser,
            };
            ModLog.Write("call {0}: {1} bytes in hand", id, finished.Result.Length);
        }

        /// <summary>
        /// The call to ring now, or null. Taking one is what triggers the ack:
        /// the audio is in hand and the game is in a state to play it, which
        /// is exactly what the server has been waiting to hear.
        /// </summary>
        public bool TryTake(out byte[] audio, out string text, out string user)
        {
            audio = null;
            text = null;
            user = null;

            if (_ready == null)
                return false;

            ReadyCall call = _ready;
            _ready = null;

            audio = call.Audio;
            text = call.Text;
            user = call.User;

            Acknowledge(call.Id);
            return true;
        }

        private void Acknowledge(string id)
        {
            _lastAckedId = id;
            try
            {
                ModLog.Write("acking call {0}", id);
                _ack = _client.AckCallAsync(id, CancellationToken.None);
            }
            catch (Exception)
            {
                _ack = null;
            }
        }

        private void CollectAck()
        {
            if (_ack == null || !_ack.IsCompleted)
                return;

            Task<bool> finished = _ack;
            _ack = null;

            if (finished.Status != TaskStatus.RanToCompletion || !finished.Result)
            {
                // The server did not hear us and will offer the call again.
                // Harmless: the audio was already played, and the repeat is
                // caught by the acked-id check in CollectPoll.
                ModLog.Write("ack was not confirmed; the server may offer the call again");
            }
        }

        /// <summary>
        /// Pushes the streamer's INI filters up. Called whenever the server
        /// turns out to be reachable, because a server restarted mid-session
        /// comes back with its own defaults and no memory of ours.
        /// </summary>
        public void SendRules()
        {
            string payload = "{"
                + "\"allow_points\":" + Bool(_config.AllowPoints)
                + ",\"allow_donations\":" + Bool(_config.AllowDonations)
                + ",\"allow_bits\":" + Bool(_config.AllowBits)
                + ",\"min_donation\":" + _config.MinDonation.ToString(Culture)
                + ",\"min_bits\":" + _config.MinBits.ToString(Culture)
                + ",\"min_length\":" + _config.MinLength.ToString(Culture)
                + ",\"max_length\":" + _config.MaxLength.ToString(Culture)
                + ",\"default_text\":" + VoiceClient.Json(_config.DefaultEventText)
                + "}";

            try
            {
                // Unawaited on purpose: nothing here needs the answer, and the
                // next reachable server gets the same push anyway.
                Task ignored = _client.SendRulesAsync(payload, CancellationToken.None);
                GC.KeepAlive(ignored);
                ModLog.Write("sent access rules to the server");
            }
            catch (Exception)
            {
            }
        }

        private static readonly System.Globalization.CultureInfo Culture =
            System.Globalization.CultureInfo.InvariantCulture;

        private static string Bool(bool value)
        {
            return value ? "true" : "false";
        }

        /// <summary>
        /// Says once that the feed is not answering, then stops. The threshold
        /// covers the server's minute of model warmup, during which the first
        /// polls of a session routinely miss.
        /// </summary>
        private void NoteFailure()
        {
            _consecutiveFailures++;
            if (_consecutiveFailures != 10 || _everAnswered)
                return;

            _pendingStatus = "cannot reach the call feed on the voice server. "
                + "Check that run_server.bat is up to date.";
        }

        private void NoteMissingRoute()
        {
            _consecutiveFailures++;
            if (_consecutiveFailures != 3 || _everAnswered)
                return;

            _pendingStatus = "the voice server has no ~y~/call~w~ route - it is an older build. "
                + "Update voice_server\\ so calls survive the pause menu.";
        }

        public void Dispose()
        {
            // These hold only HttpClient calls on a client this class does not
            // own; letting them finish is the whole of the cleanup.
            _poll = null;
            _download = null;
            _ack = null;
        }
    }
}
