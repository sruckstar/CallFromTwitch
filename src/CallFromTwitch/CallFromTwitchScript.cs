using System;
using System.Threading;
using System.Threading.Tasks;
using GTA;
using GTA.UI;

namespace CallFromTwitch
{
    /// <summary>
    /// A line of text becomes an incoming phone call in a character's voice.
    ///
    /// The text comes from Twitch chat when [Twitch] is enabled, and from the
    /// paid feed - channel points and donations - relayed by the voice server.
    /// Only failures are announced on screen; a ringing phone is evidence
    /// enough of success.
    /// </summary>
    public sealed class CallFromTwitchScript : Script
    {
        private readonly VoiceClient _client;
        private readonly AudioPlayer _player = new AudioPlayer();
        private readonly ModConfig _config;
        private readonly TwitchConfig _twitchConfig;
        private readonly IncomingCall _call;

        private readonly TwitchSource _twitch;   // null when [Twitch] Enabled = false
        // Channel points and donations, relayed by the voice server. Null
        // unless one of those paths is switched on.
        private readonly EventSource _events;

        private Task<byte[]> _pending;
        private CancellationTokenSource _pendingCancel;
        private VoiceRequest _pendingRequest;

        // Polled until it answers, not checked once: the game is usually
        // launched before the server, and a single startup check would call it
        // dead for the whole session.
        private Task<bool> _healthCheck;
        private bool _offlineReported;

        // Wall-clock: the retry has to keep running while the game sits paused
        // in a menu, which is when someone alt-tabs out to start the server.
        private DateTime _lastHealthCheckUtc = DateTime.MinValue;
        private static readonly TimeSpan HealthRetryInterval = TimeSpan.FromSeconds(5);

        public CallFromTwitchScript()
        {
            ModLog.Begin("CallFromTwitch starting " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));

            _config = ModConfig.Load(Settings);
            _twitchConfig = TwitchConfig.Load(Settings);

            // The settings as the mod actually read them: the INI beside the
            // DLL is not the one in the repository, and only this says which won.
            ModLog.Write("server = {0}, timeout {1}s, voice = {2}",
                _config.ServerUrl, _config.TimeoutSeconds, _config.Voice);
            ModLog.Write("twitch: Enabled={0} Channel=\"{1}\" Command=\"{2}\" anonymous={3}",
                _twitchConfig.Enabled, _twitchConfig.Channel, _twitchConfig.Command,
                _twitchConfig.Token.Length == 0);
            ModLog.Write("access: AllowChat={0} AllowSubs={1} AllowBits={2} AllowPoints={3} AllowDonations={4} ModsOnly={5} SubsOnly={6}",
                _twitchConfig.AllowChat, _twitchConfig.AllowSubs, _twitchConfig.AllowBits,
                _twitchConfig.AllowPoints, _twitchConfig.AllowDonations,
                _twitchConfig.ModsOnly, _twitchConfig.SubsOnly);
            ModLog.Write("limits: cooldown user {0}s / global {1}s, length {2}-{3}, queue {4}, maxAge {5}s",
                _twitchConfig.UserCooldownSeconds, _twitchConfig.GlobalCooldownSeconds,
                _twitchConfig.MinLength, _twitchConfig.MaxLength,
                _twitchConfig.MaxQueue, _twitchConfig.MaxAgeSeconds);
            _client = new VoiceClient(_config.ServerUrl, _config.TimeoutSeconds);
            _call = new IncomingCall(_config, _player);

            _twitch = CreateTwitchSource();
            _events = _twitchConfig.Enabled && _twitchConfig.NeedsEventFeed
                ? new EventSource(_twitchConfig, _client)
                : null;

            Tick += OnTick;
            Aborted += OnAborted;

            ModLog.Write("started; sources: twitch={0}, events={1}",
                _twitch != null ? _twitch.Description : "off",
                _events != null ? "on" : "off");
        }

        /// <summary>
        /// Starts the chat client, or explains why it did not start. A blank
        /// channel is the likeliest reason the feature looks dead.
        /// </summary>
        private TwitchSource CreateTwitchSource()
        {
            if (!_twitchConfig.Enabled)
            {
                ModLog.Write("twitch disabled: [Twitch] Enabled = false");
                return null;
            }

            if (_twitchConfig.Channel.Length == 0)
            {
                ModLog.Write("twitch disabled: Channel is empty");
                // A pasted URL lands here too: the INI parser cuts the value at
                // the "https:" colon, so a filled-in line arrives empty.
                ShowError("[Twitch] Channel is empty. Write just the channel name "
                    + "(~y~Channel = kreyg~w~) - a full https:// link does not survive the INI parser.");
                return null;
            }

            try
            {
                return new TwitchSource(_twitchConfig);
            }
            catch (Exception ex)
            {
                // The file source still works, so this must not take the
                // whole script down with it.
                ModLog.Error("starting the Twitch client", ex);
                ShowError("could not start the Twitch client: " + ex.Message);
                return null;
            }
        }

        private void OnTick(object sender, EventArgs e)
        {
            PollServerHealth();
            ReportTwitchStatus();

            // The call runs its own delay -> ring -> speak sequence and needs
            // a frame even when nothing is queued.
            _call.Update();

            PollSources();
            CollectFinishedRequest();
        }

        /// <summary>
        /// Picks up the next line, paid calls first. Skipped while a request or
        /// a call is in flight; the queues keep their contents meanwhile.
        /// </summary>
        private void PollSources()
        {
            if (_twitch != null)
                _twitch.Update(Game.GameTime);

            // Outside the CanAcceptLine guard: this fetch is what drains the
            // server's queue, which would otherwise age out while a call is up.
            if (_events != null)
                _events.Update(Game.GameTime);

            if (!CanAcceptLine())
            {
                LogBlocked(_pending != null
                    ? "a voice request is still in flight"
                    : "a call is in progress");
                return;
            }

            // Nothing is taken out of a queue we cannot serve. A request aimed
            // at a port nobody is listening on does not fail fast - it sits in
            // HttpClient for the whole TimeoutSeconds and blocks every later
            // line for that long. The queues keep their contents, and the
            // health poll gets them moving again within five seconds.
            if (_offlineReported)
            {
                LogBlocked("the voice server is not reachable");
                return;
            }

            LogBlocked(null);

            // Paid calls first, then chat.
            VoiceRequest request = _events != null ? _events.TryTake(Game.GameTime) : null;
            if (request == null && _twitch != null)
                request = _twitch.TryTake(Game.GameTime);

            if (request != null)
                Send(request);
        }

        private void CollectFinishedRequest()
        {
            if (_pending == null || !_pending.IsCompleted)
                return;

            Task<byte[]> finished = _pending;
            VoiceRequest request = _pendingRequest;
            bool cancelledByUser = _pendingCancel != null && _pendingCancel.IsCancellationRequested;
            _pending = null;
            _pendingRequest = null;
            DisposePendingCancel();

            // A cancelled task is the HttpClient timeout: nothing else trips
            // our own token any more. The timeout lands in IsCanceled or
            // IsFaulted depending on the runtime, so both are checked.
            if (finished.IsCanceled)
            {
                if (!cancelledByUser)
                    ShowTimeout();
                return;
            }

            if (finished.IsFaulted)
            {
                if (cancelledByUser)
                    return;

                if (IsTimeout(finished.Exception))
                    ShowTimeout();
                else
                {
                    ModLog.Error("voice request", finished.Exception);
                    ShowError(Flatten(finished.Exception));
                }
                return;
            }

            ModLog.Write("server returned {0} bytes; ringing", finished.Result.Length);
            _call.Enqueue(finished.Result, request.Text, CallerNameFor(request));
        }

        /// <summary>
        /// Who the call screen says is calling. A viewer's name only wins when
        /// the streamer asked for it, so calls otherwise share one contact.
        /// </summary>
        private string CallerNameFor(VoiceRequest request)
        {
            if (_twitchConfig.UseViewerName && !string.IsNullOrEmpty(request.Author))
                return request.Author;

            return _config.CallerName;
        }

        // The last reason a waiting line was not picked up, so the log records
        // each stall once rather than once per frame.
        private string _blockedReason;

        /// <summary>
        /// Records why a queued line is being left where it is. Null clears the
        /// stall. Silent unless something is actually waiting.
        /// </summary>
        private void LogBlocked(string reason)
        {
            if (reason == _blockedReason)
                return;

            if (reason != null && _twitch != null && _twitch.QueueLength == 0)
                return;

            _blockedReason = reason;

            if (reason != null)
                ModLog.Write("holding {0} queued line(s): {1}",
                    _twitch != null ? _twitch.QueueLength : 0, reason);
            else
                ModLog.Write("no longer blocked; taking queued lines again");
        }

        private bool CanAcceptLine()
        {
            return _pending == null && !_call.IsBusy;
        }

        private void Send(VoiceRequest request)
        {
            _pendingRequest = request;
            _pendingCancel = new CancellationTokenSource();
            // Unawaited: OnTick polls the task, so the game thread is never
            // blocked on the network round-trip.
            ModLog.Write("sending to server (voice {0}): {1}", _config.Voice, request.Text);
            _pending = _client.SynthesizeAsync(request.Text, _config.Voice, _pendingCancel.Token);
        }

        /// <summary>
        /// Asks the server whether it is there every five seconds, forever.
        ///
        /// The two halves start in either order, and the game usually goes
        /// first; a single startup probe never recovered from that. The result
        /// also holds lines back - see PollSources - and it runs before
        /// PollSources in the same tick, so a server that came up in between
        /// is noticed on the same frame the line is sent.
        /// </summary>
        private void PollServerHealth()
        {
            CollectHealthCheck();

            if (_healthCheck != null)
                return;

            DateTime now = DateTime.UtcNow;
            if (now - _lastHealthCheckUtc < HealthRetryInterval)
                return;

            _lastHealthCheckUtc = now;

            try
            {
                _healthCheck = _client.IsAliveAsync(CancellationToken.None);
            }
            catch
            {
                // A malformed base address; already reported by the constructor.
                _healthCheck = null;
            }
        }

        private void CollectHealthCheck()
        {
            if (_healthCheck == null || !_healthCheck.IsCompleted)
                return;

            bool alive = _healthCheck.Status == TaskStatus.RanToCompletion && _healthCheck.Result;
            if (_healthCheck.IsFaulted)
                ModLog.Error("health check", _healthCheck.Exception);
            _healthCheck = null;

            if (alive)
            {
                // Only worth announcing when the player was told it was down.
                if (_offlineReported)
                {
                    ModLog.Write("voice server reachable again");
                    Notification.Show("~p~CallFromTwitch:~w~ voice server connected.");
                }

                _offlineReported = false;
                return;
            }

            // Once per outage, not once every five seconds: an unstarted
            // server would otherwise bury the screen in banners.
            if (_offlineReported)
                return;

            _offlineReported = true;
            ModLog.Write("voice server not reachable at {0}", _config.ServerUrl);
            ShowError(string.Format(
                "voice server not reachable at ~y~{0}~w~. Start run_server.bat - "
                + "the mod keeps retrying, no need to reload it.",
                _config.ServerUrl));
        }

        /// <summary>
        /// Shows the chat client's first connect, and any failure to get there.
        /// The connect is the one success worth announcing: until a viewer
        /// types something there is no other sign the mod is listening.
        /// </summary>
        private void ReportTwitchStatus()
        {
            bool isError;

            if (_twitch != null)
            {
                string status = _twitch.TakeStatus(out isError);
                if (status != null)
                {
                    if (isError)
                        ShowError("Twitch " + status);
                    else
                        Notification.Show("~p~CallFromTwitch:~w~ " + status);
                }
            }

            if (_events != null)
            {
                string status = _events.TakeStatus(out isError);
                if (status != null)
                    ShowError(status);
            }

            ReportDroppedLines();
        }

        /// <summary>
        /// Says how many lines were thrown away for age or a full queue.
        ///
        /// A dropped line looks exactly like one that was never sent, so
        /// without this the streamer's only evidence is a viewer insisting
        /// they typed something. Paid drops are called out separately.
        /// </summary>
        private void ReportDroppedLines()
        {
            int chatDropped = _twitch != null ? _twitch.TakeDroppedCount() : 0;
            int paidDropped = _events != null ? _events.TakeDroppedCount() : 0;

            if (paidDropped > 0)
                ShowError(string.Format(
                    "~y~{0}~w~ paid call(s) waited too long and were skipped.", paidDropped));

            if (chatDropped > 0)
                Notification.Show(string.Format(
                    "~p~CallFromTwitch:~w~ skipped ~y~{0}~w~ stale chat line(s).", chatDropped));
        }

        private void CancelPending()
        {
            if (_pendingCancel != null)
            {
                try { _pendingCancel.Cancel(); } catch { }
            }
        }

        private void DisposePendingCancel()
        {
            if (_pendingCancel != null)
            {
                try { _pendingCancel.Dispose(); } catch { }
                _pendingCancel = null;
            }
        }

        private void ShowTimeout()
        {
            ShowError(string.Format(
                "server took longer than ~y~{0}s~w~. Wait for 'Warmup done' in the server window, "
                + "or raise [Server] TimeoutSeconds in CallFromTwitch.ini.",
                _config.TimeoutSeconds));
        }

        private static bool IsTimeout(AggregateException ex)
        {
            if (ex == null) return false;
            foreach (Exception inner in ex.Flatten().InnerExceptions)
            {
                if (inner is TaskCanceledException || inner is OperationCanceledException)
                    return true;
            }
            return false;
        }

        private static void ShowError(string message)
        {
            Notification.Show("~r~CallFromTwitch:~w~ " + message);
        }

        private static string Flatten(AggregateException ex)
        {
            if (ex == null) return "unknown error";
            Exception inner = ex.Flatten().InnerException ?? ex;
            return inner.Message;
        }

        private void OnAborted(object sender, EventArgs e)
        {
            // SHVDN aborts a script that threw during a tick without telling
            // the player, so the log has to record that the mod stopped.
            ModLog.Write("script aborted; shutting down");

            CancelPending();
            DisposePendingCancel();

            // The socket thread first: it is the only piece that outlives the
            // script, and must not read into a half-torn-down object graph.
            if (_twitch != null)
                _twitch.Dispose();
            if (_events != null)
                _events.Dispose();

            // Frees the caller's char-sheet slot, which would otherwise leak
            // into the phonebook pool for the rest of the session.
            _call.Dispose();
            _player.Dispose();
            _client.Dispose();
        }
    }
}
