using System;
using System.IO;
using System.Net.Security;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Text;
using System.Threading;

namespace CallFromTwitch
{
    /// <summary>
    /// A minimal Twitch chat (TMI) client: connects, joins one channel, and
    /// raises <see cref="MessageReceived"/> for every PRIVMSG.
    ///
    /// Raw IRC over TLS rather than the Helix/EventSub WebSocket, because chat
    /// is the one Twitch surface that can be read <i>anonymously</i>: logging
    /// in as justinfan&lt;digits&gt; with no password grants read access to any
    /// public channel, so there is no app registration and no token to paste
    /// into an INI. A token is still accepted.
    ///
    /// Everything here runs on a background thread, and the socket is blocking
    /// on purpose: a reader parked in Read is cheaper to reason about than an
    /// async state machine surviving a game paused mid-frame. Nothing on this
    /// class touches the game.
    /// </summary>
    internal sealed class TwitchIrcClient : IDisposable
    {
        private const string Host = "irc.chat.twitch.tv";
        private const int SslPort = 6697;

        // Twitch PINGs on its own schedule and expects a prompt PONG; we also
        // PING from our side, so a half-open socket is noticed.
        private static readonly TimeSpan IdleBeforePing = TimeSpan.FromMinutes(4);
        private static readonly TimeSpan IdleBeforeDead = TimeSpan.FromMinutes(6);

        // Bounds how long the thread parks between liveness checks; on its
        // own a read timeout is not a connection failure.
        private const int ReadTimeoutMs = 30000;

        private readonly string _channel;
        private readonly string _nick;
        private readonly string _token;

        private Thread _thread;
        private volatile bool _stopping;

        private TcpClient _tcp;
        private SslStream _ssl;
        private StreamReader _reader;
        private StreamWriter _writer;
        private readonly object _writeGate = new object();

        private DateTime _lastTrafficUtc = DateTime.UtcNow;
        private DateTime _lastPingSentUtc = DateTime.MinValue;

        /// <summary>Raised on the reader thread for each chat message.</summary>
        public event Action<TwitchMessage> MessageReceived;

        /// <summary>Raised on the reader thread when the connection state changes.</summary>
        public event Action<string, bool> StatusChanged;

        /// <param name="channel">Channel name without the leading '#'.</param>
        /// <param name="token">OAuth token, or null/empty for anonymous read-only access.</param>
        public TwitchIrcClient(string channel, string username, string token)
        {
            _channel = (channel ?? string.Empty).Trim().TrimStart('#').ToLowerInvariant();
            _token = (token ?? string.Empty).Trim();

            string user = (username ?? string.Empty).Trim().ToLowerInvariant();
            // An anonymous login must be justinfan followed by digits; Twitch
            // rejects any other nick that arrives without a password.
            _nick = _token.Length == 0 || user.Length == 0
                ? "justinfan" + new Random().Next(10000, 99999).ToString()
                : user;
        }

        public string Channel
        {
            get { return _channel; }
        }

        public bool IsAnonymous
        {
            get { return _token.Length == 0; }
        }

        public void Start()
        {
            if (_thread != null)
                return;

            ModLog.Write("irc reader thread starting");

            _thread = new Thread(RunLoop)
            {
                // A stuck reader must not keep the game's process alive after
                // SHVDN has torn the script down.
                IsBackground = true,
                Name = "CallFromTwitch.IRC"
            };
            _thread.Start();
        }

        /// <summary>
        /// Connect, read until the link dies, wait, connect again. Twitch
        /// restarts its edges routinely, so a disconnect is expected rather
        /// than an error worth surfacing.
        /// </summary>
        private void RunLoop()
        {
            int attempt = 0;

            while (!_stopping)
            {
                try
                {
                    Connect();
                    attempt = 0;
                    ReadUntilClosed();
                }
                catch (Exception ex)
                {
                    if (!_stopping)
                    {
                        ModLog.Error("irc loop", ex);
                        RaiseStatus("disconnected: " + ex.Message, false);
                    }
                }
                finally
                {
                    CloseSocket();
                }

                if (_stopping)
                    break;

                // So a channel that will never work - a typo, a banned
                // account - does not hammer Twitch once a second forever.
                attempt++;
                int delayMs = Math.Min(30000, 1000 * (1 << Math.Min(attempt, 5)));
                ModLog.Write("irc reconnecting in {0} ms (attempt {1})", delayMs, attempt);
                SleepInterruptibly(delayMs);
            }
        }

        private void Connect()
        {
            ModLog.Write("irc connecting to {0}:{1} as {2} (channel #{3})", Host, SslPort, _nick, _channel);

            _tcp = new TcpClient();
            _tcp.Connect(Host, SslPort);
            _tcp.NoDelay = true;

            _ssl = new SslStream(_tcp.GetStream(), leaveInnerStreamOpen: false);
            // Named explicitly: .NET Framework 4.8 honours the process-wide
            // ServicePointManager default, which can be SSL3/TLS1.0 on an
            // untouched machine, and Twitch refuses those.
            _ssl.AuthenticateAsClient(Host, null, SslProtocols.Tls12, checkCertificateRevocation: false);
            _ssl.ReadTimeout = ReadTimeoutMs;

            var reader = new StreamReader(_ssl, new UTF8Encoding(false));
            var writer = new StreamWriter(_ssl, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\r\n" };
            _reader = reader;
            lock (_writeGate)
            {
                _writer = writer;
            }

            // tags carries display-name and the mod/subscriber badges;
            // commands carries RECONNECT. membership is join/part spam.
            Send("CAP REQ :twitch.tv/tags twitch.tv/commands");
            Send("PASS " + (_token.Length == 0 ? "SCHMOOPIIE" : NormalizeToken(_token)));
            Send("NICK " + _nick);
            Send("JOIN #" + _channel);

            _lastTrafficUtc = DateTime.UtcNow;
            _lastPingSentUtc = DateTime.MinValue;

            RaiseStatus(string.Format("connected to #{0}{1}", _channel,
                IsAnonymous ? " (anonymous)" : " as " + _nick), true);
        }

        private void ReadUntilClosed()
        {
            while (!_stopping)
            {
                string line;
                try
                {
                    line = _reader.ReadLine();
                }
                catch (IOException)
                {
                    // Read timeout, not fatal on its own: fall through to the
                    // liveness check.
                    if (IsLinkDead())
                        throw new IOException("no traffic from Twitch, link is dead");

                    KeepAlive();
                    continue;
                }

                if (line == null)
                    throw new IOException("connection closed by Twitch");

                _lastTrafficUtc = DateTime.UtcNow;
                ModLog.Write("irc <- " + line);
                HandleLine(line);
            }
        }

        private bool IsLinkDead()
        {
            return DateTime.UtcNow - _lastTrafficUtc > IdleBeforeDead;
        }

        private void KeepAlive()
        {
            DateTime now = DateTime.UtcNow;
            if (now - _lastTrafficUtc < IdleBeforePing)
                return;

            // One outstanding ping at a time; the reply refreshes _lastTrafficUtc.
            if (now - _lastPingSentUtc < IdleBeforePing)
                return;

            _lastPingSentUtc = now;
            Send("PING :" + Host);
        }

        private void HandleLine(string line)
        {
            if (line.Length == 0)
                return;

            // An unanswered PING is a disconnect within seconds, so it is
            // handled before anything else.
            if (line.StartsWith("PING", StringComparison.Ordinal))
            {
                Send("PONG" + line.Substring(4));
                return;
            }

            IrcLine parsed = IrcLine.Parse(line);
            if (parsed == null)
                return;

            switch (parsed.Command)
            {
                case "PRIVMSG":
                    RaiseMessage(parsed);
                    break;

                case "RECONNECT":
                    // Twitch is about to drop this edge; tearing the socket
                    // down ourselves reconnects on our own schedule.
                    throw new IOException("Twitch asked us to reconnect");

                case "NOTICE":
                    // A bad token never becomes a JOIN: it says so and closes.
                    if (parsed.Trailing != null &&
                        parsed.Trailing.IndexOf("authentication failed", StringComparison.OrdinalIgnoreCase) >= 0)
                        throw new IOException("Twitch rejected the token: " + parsed.Trailing);
                    break;
            }
        }

        private void RaiseMessage(IrcLine line)
        {
            if (line.Trailing == null)
                return;

            string login = line.Nick;
            if (string.IsNullOrEmpty(login))
                return;

            // display-name carries the capitalisation and any non-Latin
            // spelling the viewer chose; the login is the ASCII fallback.
            string display = line.Tag("display-name");
            if (string.IsNullOrEmpty(display))
                display = login;

            bool broadcaster = HasBadge(line, "broadcaster");

            var message = new TwitchMessage(
                login: login,
                displayName: display,
                text: line.Trailing,
                isModerator: line.Tag("mod") == "1" || broadcaster,
                isSubscriber: line.Tag("subscriber") == "1",
                isBroadcaster: broadcaster,
                bits: ParseInt(line.Tag("bits")));

            ModLog.Write("chat {0}{1}{2}: {3}",
                message.DisplayName,
                message.IsBroadcaster ? " [broadcaster]" : (message.IsModerator ? " [mod]" : string.Empty),
                message.Bits > 0 ? " [" + message.Bits + " bits]" : string.Empty,
                message.Text);

            Action<TwitchMessage> handler = MessageReceived;
            if (handler != null)
                handler(message);
        }

        private static bool HasBadge(IrcLine line, string badge)
        {
            string badges = line.Tag("badges");
            if (string.IsNullOrEmpty(badges))
                return false;

            // badges is "broadcaster/1,subscriber/12" - match the name only,
            // so a substring test cannot confuse one badge for another.
            foreach (string entry in badges.Split(','))
            {
                int slash = entry.IndexOf('/');
                string name = slash < 0 ? entry : entry.Substring(0, slash);
                if (string.Equals(name, badge, StringComparison.OrdinalIgnoreCase))
                    return true;
            }
            return false;
        }

        private static int ParseInt(string value)
        {
            int parsed;
            return int.TryParse(value, out parsed) ? parsed : 0;
        }

        /// <summary>
        /// Accepts a token with or without the "oauth:" prefix the IRC gateway
        /// requires: the docs and most token generators disagree about whether
        /// it is part of the token, and pasting either should work.
        /// </summary>
        private static string NormalizeToken(string token)
        {
            return token.StartsWith("oauth:", StringComparison.OrdinalIgnoreCase)
                ? token
                : "oauth:" + token;
        }

        private void Send(string line)
        {
            lock (_writeGate)
            {
                if (_writer == null)
                    return;

                try
                {
                    ModLog.Write("irc -> " + (line.StartsWith("PASS ", StringComparison.Ordinal) ? "PASS ****" : line));
                    _writer.WriteLine(line);
                }
                catch (IOException)
                {
                    // The socket died under us; the reader thread is about to
                    // notice and reconnect.
                }
                catch (ObjectDisposedException)
                {
                }
            }
        }

        private void RaiseStatus(string message, bool connected)
        {
            Action<string, bool> handler = StatusChanged;
            if (handler != null)
                handler(message, connected);
        }

        private void SleepInterruptibly(int ms)
        {
            // In 250 ms slices, so Dispose during a 30 s backoff does not
            // hold up the game's shutdown.
            const int slice = 250;
            for (int waited = 0; waited < ms && !_stopping; waited += slice)
                Thread.Sleep(Math.Min(slice, ms - waited));
        }

        private void CloseSocket()
        {
            lock (_writeGate)
            {
                _writer = null;
            }

            try { if (_reader != null) _reader.Dispose(); } catch { }
            try { if (_ssl != null) _ssl.Dispose(); } catch { }
            try { if (_tcp != null) _tcp.Close(); } catch { }

            _reader = null;
            _ssl = null;
            _tcp = null;
        }

        public void Dispose()
        {
            _stopping = true;

            // Killing the socket is what unblocks the reader thread: it is
            // parked inside Read, which no flag can interrupt.
            CloseSocket();

            Thread thread = _thread;
            _thread = null;
            if (thread != null)
            {
                // Bounded: SHVDN is waiting, and the thread is IsBackground
                // anyway, so a straggler dies with the process.
                try { thread.Join(1000); } catch { }
            }
        }
    }

    /// <summary>One chat message, with the badges needed to decide who may use the command.</summary>
    internal sealed class TwitchMessage
    {
        public TwitchMessage(string login, string displayName, string text,
                             bool isModerator, bool isSubscriber, bool isBroadcaster, int bits)
        {
            Login = login;
            DisplayName = displayName;
            Text = text;
            IsModerator = isModerator;
            IsSubscriber = isSubscriber;
            IsBroadcaster = isBroadcaster;
            Bits = bits;
        }

        public string Login { get; private set; }
        public string DisplayName { get; private set; }
        public string Text { get; private set; }
        public bool IsModerator { get; private set; }
        public bool IsSubscriber { get; private set; }
        public bool IsBroadcaster { get; private set; }
        public int Bits { get; private set; }
    }
}
