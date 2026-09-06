using System;

namespace CallFromTwitch

{
    /// <summary>
    /// Where lines come from. The Twitch chat client and the paid event feed
    /// are both one of these, so the rest of the mod never learns whether a
    /// line was shouted in chat or paid for with points.
    ///
    /// Polled from the game thread once per tick and must never block it:
    /// anything that waits on the network does so on a thread of its own and
    /// leaves the result where <see cref="TryTake"/> can pick it up.
    /// </summary>
    internal interface IVoiceSource : IDisposable
    {
        /// <summary>Human-readable origin, for error messages.</summary>
        string Description { get; }

        /// <summary>
        /// Called once per tick, on the game thread. Starts whatever the
        /// source needs running, without blocking.
        /// </summary>
        void Update(int gameTime);

        /// <summary>
        /// The next line to speak, or null when there is nothing waiting.
        /// Only called while the mod is ready to accept one.
        /// </summary>
        VoiceRequest TryTake(int gameTime);
    }
}
