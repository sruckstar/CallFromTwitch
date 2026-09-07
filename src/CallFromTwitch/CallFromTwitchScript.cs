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
        // Calls the server has already spoken, waiting to be collected. Every
        // call arrives this way now - chat, points and donations alike.
        private readonly CallFeed _feed;

        // Lines handed to the server, not yet acknowledged by it. Only the
        // hand-off is tracked; the audio comes back through _feed.
        private Task<bool> _submit;
        private CancellationTokenSource _submitCancel;
        private VoiceRequest _submitRequest;

        // Polled until it answers, not checked once: the game is usually
        // launched before the server, and a single startup check would call it
        // dead for the whole session.
        private Task<bool> _healthCheck;
        private bool _offlineReported;
        // Whether the server we are currently talking to has our access rules.
        private bool _rulesSent;

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
            ModLog.Write("server = {0}, timeout {1}s (voice is the server's own choice)",
                _config.ServerUrl, _config.TimeoutSeconds);
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
            // Always on: it is how every call comes back, not just paid ones.
            _feed = new CallFeed(_twitchConfig, _client);

            Tick += OnTick;
            Aborted += OnAborted;

            ModLog.Write("started; chat={0}, calls collected from the server",
                _twitch != null ? _twitch.Description : "off");
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

            CollectFinishedSubmit();
            PollSources();
            CollectReadyCall();
        }

        /// <summary>
        /// Hands the next chat line to the server, paid lines needing nothing
        /// from us - the server hears those on its own sockets and starts
        /// speaking them without being asked.
        ///
        /// Unlike the call itself, this is not held back while a call is up:
        /// getting the text to the server early is the whole point, so that
        /// synthesis has already happened by the time the phone is free.
        /// </summary>
        private void PollSources()
        {
            if (_twitch == null)
                return;

            _twitch.Update(Game.GameTime);

            if (_submit != null)
            {
                LogBlocked("a line is still being handed over");
                return;
            }

            // Nothing is taken out of a queue we cannot serve. A request aimed
            // at a port nobody is listening on does not fail fast - it sits in
            // HttpClient for the whole TimeoutSeconds and blocks every later
            // line for that long. The queue keeps its contents, and the health
            // poll gets them moving again within five seconds.
            if (_offlineReported)
            {
                LogBlocked("the voice server is not reachable");
                return;
            }

            LogBlocked(null);

            VoiceRequest request = _twitch.TryTake(Game.GameTime);
            if (request != null)
                Submit(request);
        }

        /// <summary>
        /// Takes a spoken call from the feed and rings it.
        ///
        /// This is the only place the game is allowed to be busy: the audio is
        /// already made, so waiting costs the viewer nothing, and the server
        /// keeps the call until we say we have it.
        /// </summary>
        private void CollectReadyCall()
        {
            _feed.Update();

            if (_call.IsBusy)
                return;

            byte[] audio;
            string text;
            string user;
            if (!_feed.TryTake(out audio, out text, out user))
                return;

            ModLog.Write("ringing: {0} bytes", audio.Length);
            _call.Enqueue(audio, text, CallerNameFor(user));
        }

        private void CollectFinishedSubmit()
        {
            if (_submit == null || !_submit.IsCompleted)
                return;

            Task<bool> finished = _submit;
            VoiceRequest request = _submitRequest;
            bool cancelledByUser = _submitCancel != null && _submitCancel.IsCancellationRequested;
            _submit = null;
            _submitRequest = null;
            DisposeSubmitCancel();

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
                    ModLog.Error("handing a line to the server", finished.Exception);
                    ShowError(Flatten(finished.Exception));
                }
                return;
            }

            ModLog.Write("server accepted: {0}", request.Text);
        }

        /// <summary>
        /// Who the call screen says is calling. A viewer's name only wins when
        /// the streamer asked for it, so calls otherwise share one contact.
        /// </summary>
        private string CallerNameFor(string author)
        {
            if (_twitchConfig.UseViewerName && !string.IsNullOrEmpty(author))
                return author;

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

        private void Submit(VoiceRequest request)
        {
            _submitRequest = request;
            _submitCancel = new CancellationTokenSource();
            // Unawaited: OnTick polls the task, so the game thread is never
            // blocked on the network round-trip. The reply says only that the
            // line was accepted - the audio comes back through the feed, which
            // is what lets synthesis finish while the game is paused.
            ModLog.Write("handing to server: {0}", request.Text);
            _submit = _client.SubmitAsync(request.Text, request.Author, "chat", _submitCancel.Token);
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

                // On every first sighting, not just the first of the session:
                // a server restarted mid-stream comes back with its own
                // defaults and no memory of the streamer's ini.
                if (!_rulesSent)
                {
                    _feed.SendRules();
                    _rulesSent = true;
                }

                _offlineReported = false;
                return;
            }

            // The next server we reach has to be told the rules again; it may
            // not be the same process we were talking to.
            _rulesSent = false;

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

            string feedStatus = _feed.TakeStatus();
            if (feedStatus != null)
                ShowError(feedStatus);

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

            if (chatDropped > 0)
                Notification.Show(string.Format(
                    "~p~CallFromTwitch:~w~ skipped ~y~{0}~w~ stale chat line(s).", chatDropped));
        }

        private void CancelSubmit()
        {
            if (_submitCancel != null)
            {
                try { _submitCancel.Cancel(); } catch { }
            }
        }

        private void DisposeSubmitCancel()
        {
            if (_submitCancel != null)
            {
                try { _submitCancel.Dispose(); } catch { }
                _submitCancel = null;
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

            CancelSubmit();
            DisposeSubmitCancel();

            // The socket thread first: it is the only piece that outlives the
            // script, and must not read into a half-torn-down object graph.
            if (_twitch != null)
                _twitch.Dispose();
            _feed.Dispose();

            // Frees the caller's char-sheet slot, which would otherwise leak
            // into the phonebook pool for the rest of the session.
            _call.Dispose();
            _player.Dispose();
            _client.Dispose();
        }
    }
}
