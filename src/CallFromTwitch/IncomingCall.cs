using System;
using GTA;
using iFruitJailbreak;

namespace CallFromTwitch
{
    /// <summary>
    /// Delivers a finished voice line as an incoming phone call.
    ///
    /// The caller is registered once as an <i>unlisted</i> contact: it takes a
    /// character-sheet slot so the call screen has a name and a picture, but no
    /// phonebook lists it, so it only shows up while ringing. The clip is held
    /// here until the player picks up, rather than talking into an empty street.
    /// </summary>
    internal sealed class IncomingCall
    {
        private enum State
        {
            Idle,
            /// <summary>Clip is ready, waiting out the delay before ringing.</summary>
            Waiting,
            /// <summary>Ringing, or connected and speaking.</summary>
            Calling
        }

        private readonly ModConfig _config;
        private readonly AudioPlayer _player;

        private int _slot = -1;
        private State _state = State.Idle;

        private byte[] _clip;
        private string _text;
        private string _callerName;

        // What is left of the delay before the phone rings, counted down per
        // tick rather than against a deadline. A game-time deadline is never
        // noticed, because the tick that would check it stops with the clock;
        // a wall-clock one burns down inside the pause menu and rings the
        // instant the player returns. Counting only frames that happen spends
        // the delay in the game, which is where the player agreed to wait.
        private int _ringDelayRemainingMs;
        private int _lastTickGameTime;

        // Set when the clip starts, so the hang-up trails the last word.
        // Wall-clock, unlike the ring delay: this paces NAudio, which plays
        // straight through a pause because it never sees the game clock.
        private DateTime _hangUpAtUtc = DateTime.MinValue;
        private bool _hangUpArmed;

        // When the clip was handed over, in real time. Waiting is the one
        // state that can be entered and never left on its own: if the game
        // never frees the phone, IsBusy stays true and blocks every later
        // line, which reads as the mod having died after one call.
        private DateTime _waitingSinceUtc = DateTime.MinValue;

        /// <summary>
        /// How long to hold a clip whose call the game will not let start.
        /// Generous, because the usual reasons are temporary: a cutscene, a
        /// mission call, a player who is dead.
        /// </summary>
        private static readonly TimeSpan WaitingTimeout = TimeSpan.FromMinutes(2);

        public IncomingCall(ModConfig config, AudioPlayer player)
        {
            _config = config;
            _player = player;
            _callerName = config.CallerName;
        }

        /// <summary>True while a clip is queued, ringing or being spoken.</summary>
        public bool IsBusy
        {
            get { return _state != State.Idle; }
        }

        /// <summary>
        /// Queues <paramref name="clip"/> for delivery by a call from
        /// <paramref name="callerName"/> after the configured delay.
        /// </summary>
        public void Enqueue(byte[] clip, string text, string callerName)
        {
            _clip = clip;
            _text = text;
            if (!string.IsNullOrEmpty(callerName))
                _callerName = callerName;
            _ringDelayRemainingMs = _config.CallDelaySeconds * 1000;
            _lastTickGameTime = Game.GameTime;
            _waitingSinceUtc = DateTime.UtcNow;
            _hangUpArmed = false;
            _state = State.Waiting;
        }

        /// <summary><b>Call every frame.</b> Runs the delay -&gt; ring -&gt; speak sequence.</summary>
        public void Update()
        {
            // appIncomingCall keeps its own state machine, which has to run
            // while we are idle so a call that ended otherwise winds down.
            appIncomingCall.Update();

            switch (_state)
            {
                case State.Waiting:
                    UpdateWaiting();
                    break;

                case State.Calling:
                    UpdateCalling();
                    break;
            }
        }

        private void UpdateWaiting()
        {
            // Before the delay, not after: the delay is spent in game time,
            // so a clip queued and then paused on burns none of it and this
            // timeout would never be reached.
            if (GiveUpIfWaitedTooLong())
                return;

            if (!DelayElapsed())
                return;

            // In a cutscene, dead, or already on a call: hold the clip and
            // retry next frame, but not forever.
            if (!appIncomingCall.CanStartCall())
            {
                GiveUpIfWaitedTooLong();
                return;
            }

            if (!EnsureCaller())
            {
                Fail("could not register the caller.");
                return;
            }

            appIncomingCall.RingTimeMs = _config.RingSeconds * 1000;

            if (!appIncomingCall.StartCall(_slot))
            {
                // Something took the phone between the two checks. The slot may
                // also have gone stale, so let the next attempt register one.
                _slot = -1;
                GiveUpIfWaitedTooLong();
                return;
            }

            _state = State.Calling;
        }

        /// <summary>
        /// Drops a clip the game keeps refusing to ring for, so the mod goes
        /// back to accepting lines. Returns true when it gave up, so the caller
        /// stops touching a state Fail has already reset to Idle.
        /// </summary>
        private bool GiveUpIfWaitedTooLong()
        {
            if (_waitingSinceUtc == DateTime.MinValue ||
                DateTime.UtcNow - _waitingSinceUtc < WaitingTimeout)
                return false;

            // From the outside this looks exactly like a line that was never
            // sent, so the player has to be told the phone was busy.
            Fail("the phone stayed busy - dropped one call.");
            return true;
        }

        /// <summary>
        /// Spends one frame's worth of the ring delay and reports whether it is
        /// used up. The step is clamped because Game.GameTime jumps across a
        /// pause, a load screen or a cutscene, and an unclamped delta would
        /// swallow the whole delay in the first frame back.
        /// </summary>
        private bool DelayElapsed()
        {
            const int maxStepMs = 250;

            int now = Game.GameTime;
            int elapsed = now - _lastTickGameTime;
            _lastTickGameTime = now;

            // Negative deltas are possible: GameTime is a 32-bit millisecond
            // counter and a new session restarts it.
            if (elapsed < 0)
                elapsed = 0;
            else if (elapsed > maxStepMs)
                elapsed = maxStepMs;

            _ringDelayRemainingMs -= elapsed;
            return _ringDelayRemainingMs <= 0;
        }

        private void UpdateCalling()
        {
            if (appIncomingCall.JustAnswered(_slot))
            {
                Speak();
                return;
            }

            // Not announced: the player either declined or watched it ring
            // out, and the phone showed both.
            if (appIncomingCall.JustDeclined(_slot) || appIncomingCall.JustMissed(_slot))
            {
                Reset();
                return;
            }

            if (appIncomingCall.IsRinging(_slot))
                return;

            if (!appIncomingCall.IsInCall(_slot))
            {
                // Neither ringing nor connected, and no event fired: the call
                // is gone. Stop the clip so it does not outlive it.
                _player.Stop();
                Reset();
                return;
            }

            // Connected: hang up once the line has been spoken. JustAnswered
            // is a one-shot flag that clears on the read, so a frame lost
            // between the answer and here would otherwise leave a connected
            // call nobody hangs up, with IsBusy stuck on it.
            if (!_hangUpArmed)
            {
                appIncomingCall.EndCall();
                Reset();
                return;
            }

            if (DateTime.UtcNow >= _hangUpAtUtc)
            {
                appIncomingCall.EndCall();
                Reset();
            }
        }

        private void Speak()
        {
            byte[] clip = _clip;
            _clip = null;

            if (clip == null)
            {
                appIncomingCall.EndCall();
                Reset();
                return;
            }

            int durationMs = WavDurationMs(clip);

            try
            {
                _player.Play(clip, _config.Volume);
            }
            catch (Exception ex)
            {
                Notify("~r~CallFromTwitch:~w~ playback failed: " + ex.Message);
                appIncomingCall.EndCall();
                Reset();
                return;
            }

            if (_config.ShowSubtitle)
            {
                GTA.UI.Screen.ShowSubtitle(
                    string.Format("~b~{0}:~w~ {1}", _callerName, _text),
                    Math.Max(durationMs, 1000));
            }

            // Hold the line open for the clip plus a beat.
            _hangUpAtUtc = DateTime.UtcNow
                + TimeSpan.FromMilliseconds(durationMs + _config.HangUpDelayMs);
            _hangUpArmed = true;
        }

        /// <summary>Hangs up and drops whatever is queued. Safe to call when idle.</summary>
        public void Cancel()
        {
            if (_state == State.Calling)
                appIncomingCall.EndCall();

            Reset();
        }

        /// <summary>Frees the caller slot. Call on script abort.</summary>
        public void Dispose()
        {
            Cancel();

            if (_slot != -1)
            {
                try { appIncomingCall.RemoveCaller(_slot); } catch { }
                _slot = -1;
            }
        }

        // Registered lazily: a char-sheet slot is a finite game resource, and
        // taking one at construction would hold it for a session that may
        // never place a call.
        private bool EnsureCaller()
        {
            if (_slot != -1)
                return true;

            try
            {
                // Debris from a session that ended without Dispose - a crash,
                // a script reload - would otherwise sit in the pool forever.
                appContacts.SweepStalePoolSlots();
                _slot = appIncomingCall.RegisterCaller(_callerName, _config.CallerIcon);
                return _slot != -1;
            }
            catch
            {
                return false;
            }
        }

        private void Reset()
        {
            _state = State.Idle;
            _clip = null;
            _text = null;
            _hangUpArmed = false;
            _ringDelayRemainingMs = 0;
            _waitingSinceUtc = DateTime.MinValue;
        }

        private void Fail(string message)
        {
            Notify("~r~CallFromTwitch:~w~ " + message);
            Reset();
        }

        private static void Notify(string message)
        {
            GTA.UI.Notification.Show(message);
        }

        /// <summary>
        /// Length of a PCM WAV in milliseconds, read from its header. NAudio
        /// knows this too, but only by re-parsing the stream it is playing.
        /// </summary>
        private static int WavDurationMs(byte[] wav)
        {
            const int fallbackMs = 5000;

            if (wav == null || wav.Length < 44)
                return fallbackMs;

            try
            {
                int byteRate = BitConverter.ToInt32(wav, 28);   // fmt chunk: bytes per second
                if (byteRate <= 0)
                    return fallbackMs;

                // Walk the chunk list rather than assuming data starts at 44:
                // a LIST/INFO chunk in front of it is legal.
                int offset = 12;
                while (offset + 8 <= wav.Length)
                {
                    int chunkSize = BitConverter.ToInt32(wav, offset + 4);
                    bool isData = wav[offset] == (byte)'d' && wav[offset + 1] == (byte)'a' &&
                                  wav[offset + 2] == (byte)'t' && wav[offset + 3] == (byte)'a';

                    if (isData)
                    {
                        if (chunkSize <= 0 || chunkSize > wav.Length - offset - 8)
                            chunkSize = wav.Length - offset - 8;
                        return (int)((long)chunkSize * 1000 / byteRate);
                    }

                    if (chunkSize <= 0)
                        break;

                    offset += 8 + chunkSize + (chunkSize & 1); // chunks are word-aligned
                }
            }
            catch
            {
                // Malformed header: fall through to the default.
            }

            return fallbackMs;
        }
    }
}
