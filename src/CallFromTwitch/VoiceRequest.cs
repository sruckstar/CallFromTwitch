using System;

namespace CallFromTwitch
{
    /// <summary>
    /// One line waiting to be spoken, and who asked for it.
    ///
    /// The file source has no author and leaves it null, meaning "use the
    /// caller from the INI". Twitch fills it with the viewer's display name,
    /// which has to travel with the text until IncomingCall draws it.
    /// </summary>
    internal sealed class VoiceRequest
    {
        public VoiceRequest(string text, string author)
        {
            Text = text;
            Author = author;
            QueuedUtc = DateTime.UtcNow;
        }

        public string Text { get; private set; }

        /// <summary>Viewer's display name, or null when the line has no author.</summary>
        public string Author { get; private set; }

        /// <summary>
        /// When this line was accepted. Wall-clock, because the stamp measures
        /// how long the viewer has been waiting, and they keep waiting while
        /// the game sits paused.
        /// </summary>
        public DateTime QueuedUtc { get; private set; }

        /// <summary>Seconds this line has been waiting to be spoken.</summary>
        public double AgeSeconds
        {
            get { return (DateTime.UtcNow - QueuedUtc).TotalSeconds; }
        }

        public override string ToString()
        {
            return Author == null ? Text : Author + ": " + Text;
        }
    }
}
