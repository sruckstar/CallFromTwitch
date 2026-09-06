using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace CallFromTwitch
{
    /// <summary>
    /// Calls bought with channel points or money, fetched from the voice server.
    ///
    /// Chat and bits arrive on the IRC socket the mod already holds, because
    /// Twitch puts both in the message itself. These two cannot: a redemption
    /// exists only on EventSub, which needs the broadcaster's OAuth token, and
    /// a donation never reaches Twitch at all. The Python server holds those
    /// sockets and this class asks it what came in.
    ///
    /// Polling, not pushing: a game script can rely on its tick and little
    /// else, and one small GET a second is cheaper than holding a socket open
    /// across a paused game.
    /// </summary>
    internal sealed class EventSource : IVoiceSource
    {
        private readonly TwitchConfig _config;
        private readonly VoiceClient _client;

        private readonly object _gate = new object();
        private readonly Queue<VoiceRequest> _queue = new Queue<VoiceRequest>();

        // One request in flight at a time: a server inside its 60-second
        // warmup would otherwise collect a request per tick.
        private Task<string> _poll;
        // Wall-clock, not Game.GameTime: a schedule on the game clock would
        // stop with it, and the server would sit on a paid redemption for as
        // long as the player stayed in a menu.
        private DateTime _nextPollUtc = DateTime.MinValue;
        private bool _everAnswered;

        private string _pendingStatus;
        private bool _pendingStatusIsError;
        private int _consecutiveFailures;

        // Paid lines thrown away for age, waiting to be reported.
        private int _dropped;

        public EventSource(TwitchConfig config, VoiceClient client)
        {
            _config = config;
            _client = client;
        }

        public string Description
        {
            get { return "Twitch events (points/donations)"; }
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

        /// <summary>
        /// Starts a fetch when one is due, and collects the previous one.
        ///
        /// The next poll is measured from now rather than from the due time,
        /// so an hour in a menu costs one poll on the way out, not a burst.
        /// </summary>
        public void Update(int gameTime)
        {
            CollectFinishedPoll();

            DateTime now = DateTime.UtcNow;
            if (_poll != null || now < _nextPollUtc)
                return;

            _nextPollUtc = now + TimeSpan.FromMilliseconds(_config.EventPollMilliseconds);

            try
            {
                _poll = _client.FetchEventsAsync(_config.MaxQueue, CancellationToken.None);
            }
            catch (Exception)
            {
                // A malformed base address, already reported elsewhere.
                _poll = null;
            }
        }

        private void CollectFinishedPoll()
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
                // Answered, but not with events: most likely an older build
                // with no /events route.
                NoteMissingRoute();
                return;
            }

            try
            {
                Ingest(body);
            }
            catch (FormatException)
            {
                // Something on that port that is not our server.
                NoteFailure();
                return;
            }

            _consecutiveFailures = 0;
            _everAnswered = true;
        }

        private void Ingest(string body)
        {
            object document = MiniJson.Parse(body);

            foreach (object node in MiniJson.Array(document, "events"))
            {
                VoiceRequest request = ToRequest(node);
                if (request == null)
                    continue;

                lock (_gate)
                {
                    // As in the chat queue: a full queue drops what just
                    // arrived rather than a line that has been waiting.
                    if (_queue.Count >= _config.MaxQueue)
                        return;

                    _queue.Enqueue(request);
                }
            }
        }

        /// <summary>
        /// One event, or null when it is not one this configuration wants.
        ///
        /// The thresholds live here so that changing them is an INI edit
        /// rather than a server restart, and the server stays a dumb relay.
        /// </summary>
        private VoiceRequest ToRequest(object node)
        {
            string kind = MiniJson.String(node, "kind");
            double amount = MiniJson.Number(node, "amount");

            if (kind == "points")
            {
                if (!_config.AllowPoints)
                    return null;
            }
            else if (kind == "donation")
            {
                if (!_config.AllowDonations || amount < _config.MinDonation)
                    return null;
            }
            else if (kind == "bits")
            {
                // Bits normally arrive on the IRC tag; the exception is a test
                // event, which has no chat message to attach itself to.
                if (!_config.AllowBits || amount < _config.MinBits)
                    return null;
            }
            else
            {
                return null;
            }

            string text = MiniJson.String(node, "text").Trim();
            if (text.Length < _config.MinLength)
            {
                // A reward with no prompt box, or a donation sent without a
                // message. The viewer paid for a call, so one happens.
                text = _config.DefaultEventText;
                if (text.Length == 0)
                    return null;
            }

            if (text.Length > _config.MaxLength)
                text = text.Substring(0, _config.MaxLength);

            string user = MiniJson.String(node, "user", "Viewer");
            return new VoiceRequest(text, user);
        }

        public VoiceRequest TryTake(int gameTime)
        {
            lock (_gate)
            {
                while (_queue.Count > 0)
                {
                    VoiceRequest request = _queue.Dequeue();

                    // A longer rope than chat, because it was paid for - but
                    // not unlimited: a redemption from an hour ago is a call
                    // the viewer has stopped waiting for.
                    if (_config.MaxAgeSeconds <= 0 ||
                        request.AgeSeconds <= _config.MaxAgeSeconds * PaidAgeFactor)
                        return request;

                    _dropped++;
                }

                return null;
            }
        }

        /// <summary>How much longer a paid line may wait than a chat line.</summary>
        private const int PaidAgeFactor = 4;

        /// <summary>
        /// Paid lines dropped for age since this was last read. Worth telling
        /// the streamer unprompted: money changed hands, and the viewer who
        /// paid and heard nothing may ask why.
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

        public void ClearQueue()
        {
            lock (_gate)
            {
                _queue.Clear();
            }
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

            SetStatus("cannot reach the event feed. Channel points and donations need "
                + "~y~CFT_EVENTS=1~w~ set for the voice server.", true);
        }

        private void NoteMissingRoute()
        {
            _consecutiveFailures++;
            if (_consecutiveFailures != 3 || _everAnswered)
                return;

            SetStatus("the voice server has no ~y~/events~w~ route - it is an older build. "
                + "Update voice_server\\ to use channel points or donations.", true);
        }

        private void SetStatus(string message, bool isError)
        {
            lock (_gate)
            {
                _pendingStatus = message;
                _pendingStatusIsError = isError;
            }
        }

        public void Dispose()
        {
            // The poll holds only an HttpClient call on a client this class
            // does not own; letting it finish is the whole of the cleanup.
            _poll = null;
        }
    }
}
