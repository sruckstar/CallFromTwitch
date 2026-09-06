using System;
using GTA;

namespace CallFromTwitch
{
    /// <summary>The [Twitch] section of CallFromTwitch.ini.</summary>
    internal sealed class TwitchConfig
    {
        /// <summary>False leaves the mod on the file source it shipped with.</summary>
        public bool Enabled { get; private set; }

        public string Channel { get; private set; }

        /// <summary>Bot account name. Empty means anonymous, read-only.</summary>
        public string Username { get; private set; }

        /// <summary>OAuth token. Empty means anonymous, read-only.</summary>
        public string Token { get; private set; }

        /// <summary>Command word including its prefix, e.g. "!call". Empty means every message.</summary>
        public string Command { get; private set; }

        public bool ModsOnly { get; private set; }
        public bool SubsOnly { get; private set; }

        // ---- Who may ask for a call ----
        //
        // Four ways in, combining rather than excluding: a channel can let
        // subscribers call for free while everyone else pays. All four off
        // would leave a mod that is connected, listening and deaf, so Load()
        // falls back to chat in that case.

        /// <summary>Anyone in chat may use the command.</summary>
        public bool AllowChat { get; private set; }

        /// <summary>Subscribers (and mods, and the broadcaster) may use the command.</summary>
        public bool AllowSubs { get; private set; }

        /// <summary>A cheer of at least <see cref="MinBits"/> becomes a call.</summary>
        public bool AllowBits { get; private set; }

        /// <summary>Bits below this are ignored. Twitch's own minimum cheer is 1.</summary>
        public int MinBits { get; private set; }

        /// <summary>
        /// Channel-point redemptions become calls. Unlike the three above this
        /// is not read from chat: redemptions only exist on EventSub, which
        /// needs the broadcaster's token, so the voice server holds that socket
        /// and the mod polls it. Turning this on without configuring the server
        /// yields nothing.
        /// </summary>
        public bool AllowPoints { get; private set; }

        /// <summary>Money donations relayed by the voice server (DonationAlerts).</summary>
        public bool AllowDonations { get; private set; }

        /// <summary>Donations below this are ignored, in whatever currency the service reports.</summary>
        public double MinDonation { get; private set; }

        /// <summary>How often to ask the server for new paid events, in milliseconds.</summary>
        public int EventPollMilliseconds { get; private set; }

        /// <summary>
        /// What to say when a paid event carries no text of its own - a bare
        /// "Cheer100", or a reward with no prompt box.
        /// </summary>
        public string DefaultEventText { get; private set; }

        public int UserCooldownSeconds { get; private set; }
        public int GlobalCooldownSeconds { get; private set; }

        public int MinLength { get; private set; }
        public int MaxLength { get; private set; }
        public int MaxQueue { get; private set; }

        /// <summary>
        /// How long a queued line stays worth speaking, in seconds. 0 keeps
        /// everything, however old.
        ///
        /// The queue survives things the viewer cannot see - a long pause
        /// menu, a cutscene, a mission that would not let the phone ring - and
        /// answering half an hour later reads worse than not answering.
        /// </summary>
        public int MaxAgeSeconds { get; private set; }

        /// <summary>
        /// Use the viewer's name on the call screen instead of Call/CallerName.
        /// Off means every call comes from the same contact, which is the
        /// in-fiction look.
        /// </summary>
        public bool UseViewerName { get; private set; }

        public static TwitchConfig Load(ScriptSettings settings)
        {
            var config = new TwitchConfig
            {
                Enabled = settings.GetValue("Twitch", "Enabled", false),
                Channel = CleanChannel(settings.GetValue("Twitch", "Channel", string.Empty)),
                Username = (settings.GetValue("Twitch", "Username", string.Empty) ?? string.Empty).Trim(),
                Token = (settings.GetValue("Twitch", "Token", string.Empty) ?? string.Empty).Trim(),
                Command = (settings.GetValue("Twitch", "Command", "!call") ?? string.Empty).Trim(),
                ModsOnly = settings.GetValue("Twitch", "ModsOnly", false),
                SubsOnly = settings.GetValue("Twitch", "SubsOnly", false),
                AllowChat = settings.GetValue("Twitch", "AllowChat", true),
                AllowSubs = settings.GetValue("Twitch", "AllowSubs", false),
                AllowBits = settings.GetValue("Twitch", "AllowBits", false),
                MinBits = Clamp(settings.GetValue("Twitch", "MinBits", 100), 1, 1000000),
                AllowPoints = settings.GetValue("Twitch", "AllowPoints", false),
                AllowDonations = settings.GetValue("Twitch", "AllowDonations", false),
                MinDonation = Math.Max(0, settings.GetValue("Twitch", "MinDonation", 0.0f)),
                // A viewer who paid notices a delay measured in seconds, and
                // one request a second costs nothing next to the GPU work.
                EventPollMilliseconds = Clamp(settings.GetValue("Twitch", "EventPollMilliseconds", 1000), 250, 30000),
                DefaultEventText = (settings.GetValue("Twitch", "DefaultEventText",
                    "Hey, thanks for the support!") ?? string.Empty).Trim(),
                UserCooldownSeconds = Clamp(settings.GetValue("Twitch", "UserCooldownSeconds", 60), 0, 3600),
                GlobalCooldownSeconds = Clamp(settings.GetValue("Twitch", "GlobalCooldownSeconds", 0), 0, 3600),
                MinLength = Clamp(settings.GetValue("Twitch", "MinLength", 2), 1, 100),
                // Matches the server's CFT_MAX_TEXT default: a longer line is
                // cut there anyway, and cutting here keeps the subtitle honest.
                MaxLength = Clamp(settings.GetValue("Twitch", "MaxLength", 300), 10, 2000),
                MaxQueue = Clamp(settings.GetValue("Twitch", "MaxQueue", 10), 1, 200),
                // Long enough to outlast a cutscene or a call already in
                // progress, short enough to stay part of the same stream.
                MaxAgeSeconds = Clamp(settings.GetValue("Twitch", "MaxAgeSeconds", 300), 0, 86400),
                UseViewerName = settings.GetValue("Twitch", "UseViewerName", true)
            };

            config.Normalize();
            return config;
        }

        /// <summary>
        /// Reconciles the new Allow* flags with the ModsOnly/SubsOnly pair they
        /// replace, and refuses to end up with no way in at all.
        ///
        /// The old keys stay meaningful because an INI written before this
        /// change is still sitting in someone's scripts\ folder, and silently
        /// dropping SubsOnly would open a channel that had restricted itself.
        /// </summary>
        private void Normalize()
        {
            // The old keys are restrictions, so they narrow the new flags
            // rather than adding to them.
            if (ModsOnly)
            {
                AllowChat = false;
                AllowSubs = false;
            }
            else if (SubsOnly && AllowChat)
            {
                AllowChat = false;
                AllowSubs = true;
            }

            if (AllowChat || AllowSubs || AllowBits || AllowPoints || AllowDonations)
                return;

            // ModsOnly closes every flag by design and is complete on its own:
            // mods and the broadcaster are admitted ahead of the flags anyway.
            if (ModsOnly)
                return;

            // Otherwise everything off leaves a client that connects, joins,
            // reads every message and answers none of them.
            AllowChat = true;
        }

        /// <summary>True when any path needs the voice server's event feed.</summary>
        public bool NeedsEventFeed
        {
            get { return AllowPoints || AllowDonations; }
        }

        /// <summary>
        /// Accepts what the streamer is likely to paste: a bare name, "#name",
        /// or the channel URL straight out of the address bar.
        ///
        /// ScriptSettings treats ':' as a key/value separator alongside '=' -
        /// the same quirk that forced [Server] Host/Port apart - so
        /// "Channel = https://twitch.tv/x" reaches us as the bare word "https".
        /// That truncation is recognised here and reported as an empty channel
        /// rather than a doomed connection to a channel named "https".
        /// </summary>
        private static string CleanChannel(string value)
        {
            string channel = (value ?? string.Empty).Trim();
            if (channel.Length == 0)
                return string.Empty;

            // What is left of a URL after the INI parser cut it at the colon.
            if (string.Equals(channel, "http", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(channel, "https", StringComparison.OrdinalIgnoreCase))
                return string.Empty;

            const string marker = "twitch.tv/";
            int at = channel.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (at >= 0)
                channel = channel.Substring(at + marker.Length);

            // Anything after the name in a pasted URL: /about, ?tt_content=...
            int cut = channel.IndexOfAny(new[] { '/', '?', '#' }, channel.StartsWith("#") ? 1 : 0);
            if (cut > 0)
                channel = channel.Substring(0, cut);

            return channel.Trim().TrimStart('#').ToLowerInvariant();
        }

        private static int Clamp(int value, int min, int max)
        {
            return value < min ? min : (value > max ? max : value);
        }
    }
}
