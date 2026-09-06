using System;
using GTA;
using iFruitJailbreak;

namespace CallFromTwitch
{
    /// <summary>Values from CallFromTwitch.ini, with defaults that work out of the box.</summary>
    internal sealed class ModConfig
    {
        public string ServerUrl { get; private set; }
        public string Voice { get; private set; }
        public float Volume { get; private set; }
        public int TimeoutSeconds { get; private set; }
        public bool ShowSubtitle { get; private set; }
        public string CallerName { get; private set; }
        public string CallerIcon { get; private set; }
        public int CallDelaySeconds { get; private set; }
        public int RingSeconds { get; private set; }
        public int HangUpDelayMs { get; private set; }

        public static ModConfig Load(ScriptSettings settings)
        {
            var config = new ModConfig
            {
                ServerUrl = LoadServerUrl(settings),
                Voice = settings.GetValue("Voice", "Character", "trevor"),
                Volume = Clamp(settings.GetValue("Audio", "Volume", 1.0f), 0f, 1f),
                TimeoutSeconds = Clamp(settings.GetValue("Server", "TimeoutSeconds", 180), 5, 600),
                ShowSubtitle = settings.GetValue("Audio", "ShowSubtitle", true),
                CallerName = settings.GetValue("Call", "CallerName", "Michael"),
                // Any CHAR_* texture the game ships; an unknown label just
                // draws no picture.
                CallerIcon = settings.GetValue("Call", "CallerIcon", ContactIcon.Michael),
                CallDelaySeconds = Clamp(settings.GetValue("Call", "DelaySeconds", 5), 0, 120),
                RingSeconds = Clamp(settings.GetValue("Call", "RingSeconds", 20), 3, 120),
                HangUpDelayMs = Clamp(settings.GetValue("Call", "HangUpDelayMs", 1200), 0, 10000)
            };
            return config;
        }

        /// <summary>
        /// ScriptSettings treats ':' as a key/value separator alongside '=', so
        /// "Url = http://host:port" comes back cut down to "http". Host/Port
        /// carry no colon and survive; a Url line is still honoured when it
        /// happens to be colon-free.
        /// </summary>
        private static string LoadServerUrl(ScriptSettings settings)
        {
            string url = settings.GetValue("Server", "Url", string.Empty);
            Uri parsed;
            if (Uri.TryCreate(url, UriKind.Absolute, out parsed))
                return url;

            string host = settings.GetValue("Server", "Host", "127.0.0.1");
            int port = Clamp(settings.GetValue("Server", "Port", 8765), 1, 65535);
            return string.Format("http://{0}:{1}", host, port);
        }

        private static float Clamp(float value, float min, float max)
        {
            return value < min ? min : (value > max ? max : value);
        }

        private static int Clamp(int value, int min, int max)
        {
            return value < min ? min : (value > max ? max : value);
        }
    }
}
