using System;

namespace CallFromTwitch

{
    /// <summary>
    /// Where lines the mod itself collects come from - chat, today, and
    /// whatever else arrives on a socket the mod holds.
    ///
    /// Paid events are not one of these any more. They reach the voice server
    /// directly, because a line that only the mod knows about cannot be
    /// spoken while the game is paused and the mod is not running.
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
