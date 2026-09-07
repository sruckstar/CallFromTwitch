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
    /// Nothing here decides which lines those are. Chat, channel points and
    /// donations all arrive on the voice server's own sockets, are filtered
    /// against CallFromTwitch.ini there, and are spoken there - all of it
    /// while this script may not be running at all. What is left for the mod
    /// is the one job that needs the game: take a finished call and ring it.
    ///
    /// That division exists because a paused game runs no script code. A mod
    /// that held the chat socket stopped hearing chat the moment the player
    /// alt-tabbed, and a mod that waited for synthesis waited forever in a
    /// menu. Neither is true of a server that never stops.
    ///
    /// Only failures are announced on screen; a ringing phone is evidence
    /// enough of success.
    /// </summary>
    public sealed class CallFromTwitchScript : Script
    {
        private readonly VoiceClient _client;
        private readonly AudioPlayer _player = new AudioPlayer();
        private readonly ModConfig _config;
        private readonly IncomingCall _call;

        // Calls the server has already spoken, waiting to be collected. Every
        // call arrives this way - chat, points and donations alike.
        private readonly CallFeed _feed;

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

            // The settings as the mod actually read them: the INI beside the
            // DLL is not the one in the repository, and only this says which won.
            ModLog.Write("server = {0}, timeout {1}s (voice is the server's own choice)",
                _config.ServerUrl, _config.TimeoutSeconds);
            ModLog.Write("poll every {0}ms; caller \"{1}\", viewer name {2}",
                _config.CallPollMilliseconds, _config.CallerName,
                _config.UseViewerName ? "on" : "off");
            // Said plainly because it is the thing streamers ask about: the
            // channel, the command and every threshold are the server's now.
            ModLog.Write("chat, points and donations are read by the voice server "
                + "from its own copy of CallFromTwitch.ini");

            _client = new VoiceClient(_config.ServerUrl, _config.TimeoutSeconds);
            _call = new IncomingCall(_config, _player);
            _feed = new CallFeed(_config, _client);

            Tick += OnTick;
            Aborted += OnAborted;

            ModLog.Write("started; collecting calls from {0}", _config.ServerUrl);
        }

        private void OnTick(object sender, EventArgs e)
        {
            PollServerHealth();
            ReportFeedStatus();

            // The call runs its own delay -> ring -> speak sequence and needs
            // a frame even when nothing is queued.
            _call.Update();

            CollectReadyCall();
        }

        /// <summary>
        /// Takes a spoken call from the feed and rings it.
        ///
        /// Waiting costs the viewer nothing here: the audio is already made,
        /// and the server keeps the call until we say we have it.
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

        /// <summary>
        /// Who the call screen says is calling. A viewer's name only wins when
        /// the streamer asked for it, so calls otherwise share one contact.
        /// </summary>
        private string CallerNameFor(string author)
        {
            if (_config.UseViewerName && !string.IsNullOrEmpty(author))
                return author;

            return _config.CallerName;
        }

        /// <summary>
        /// Asks the server whether it is there every five seconds, forever.
        ///
        /// The two halves start in either order, and the game usually goes
        /// first; a single startup probe never recovered from that. It is also
        /// the only way the player learns the server is down, since a server
        /// that is not running is indistinguishable from a quiet chat.
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
                + "the mod keeps retrying, no need to reload it. Chat is not being "
                + "read while it is down.",
                _config.ServerUrl));
        }

        private void ReportFeedStatus()
        {
            string feedStatus = _feed.TakeStatus();
            if (feedStatus != null)
                ShowError(feedStatus);
        }

        private static void ShowError(string message)
        {
            Notification.Show("~r~CallFromTwitch:~w~ " + message);
        }

        private void OnAborted(object sender, EventArgs e)
        {
            // SHVDN aborts a script that threw during a tick without telling
            // the player, so the log has to record that the mod stopped.
            ModLog.Write("script aborted; shutting down");

            _feed.Dispose();

            // Frees the caller's char-sheet slot, which would otherwise leak
            // into the phonebook pool for the rest of the session.
            _call.Dispose();
            _player.Dispose();
            _client.Dispose();
        }
    }
}
