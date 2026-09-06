using System;
using System.Collections.Generic;
using System.Text;

namespace CallFromTwitch
{
    /// <summary>
    /// One parsed IRC line, in the IRCv3 shape Twitch sends:
    ///
    /// <code>@tag=v;tag=v :nick!user@host PRIVMSG #channel :the message</code>
    ///
    /// Written out rather than pulled from an IRC library because this is the
    /// only line format the mod ever sees, and a library would be another DLL
    /// to ship into GTA V\scripts\ next to the script.
    /// </summary>
    internal sealed class IrcLine
    {
        private readonly Dictionary<string, string> _tags;

        private IrcLine(Dictionary<string, string> tags, string prefix, string command,
                        string[] parameters, string trailing)
        {
            _tags = tags;
            Prefix = prefix;
            Command = command;
            Parameters = parameters;
            Trailing = trailing;
        }

        /// <summary>The "nick!user@host" ahead of the command, or null.</summary>
        public string Prefix { get; private set; }

        /// <summary>PRIVMSG, PING, NOTICE, RECONNECT, a numeric ...</summary>
        public string Command { get; private set; }

        /// <summary>Middle parameters - for PRIVMSG, the channel.</summary>
        public string[] Parameters { get; private set; }

        /// <summary>The text after " :" - for PRIVMSG, what the viewer typed.</summary>
        public string Trailing { get; private set; }

        /// <summary>The nick out of the prefix, or null when there is none.</summary>
        public string Nick
        {
            get
            {
                if (string.IsNullOrEmpty(Prefix))
                    return null;

                int bang = Prefix.IndexOf('!');
                return bang > 0 ? Prefix.Substring(0, bang) : Prefix;
            }
        }

        /// <summary>An IRCv3 tag value, or null when the tag was not sent.</summary>
        public string Tag(string name)
        {
            if (_tags == null)
                return null;

            string value;
            return _tags.TryGetValue(name, out value) ? value : null;
        }

        /// <summary>Parses one line, or returns null when it is not usable.</summary>
        public static IrcLine Parse(string line)
        {
            if (string.IsNullOrEmpty(line))
                return null;

            int pos = 0;
            Dictionary<string, string> tags = null;

            if (line[pos] == '@')
            {
                int space = line.IndexOf(' ', pos);
                if (space < 0)
                    return null;

                tags = ParseTags(line.Substring(pos + 1, space - pos - 1));
                pos = SkipSpaces(line, space);
            }

            string prefix = null;
            if (pos < line.Length && line[pos] == ':')
            {
                int space = line.IndexOf(' ', pos);
                if (space < 0)
                    return null;

                prefix = line.Substring(pos + 1, space - pos - 1);
                pos = SkipSpaces(line, space);
            }

            if (pos >= line.Length)
                return null;

            int cmdEnd = line.IndexOf(' ', pos);
            if (cmdEnd < 0)
            {
                // A bare command with no parameters, e.g. "RECONNECT".
                return new IrcLine(tags, prefix, line.Substring(pos), new string[0], null);
            }

            string command = line.Substring(pos, cmdEnd - pos);
            pos = SkipSpaces(line, cmdEnd);

            var parameters = new List<string>();
            string trailing = null;

            while (pos < line.Length)
            {
                if (line[pos] == ':')
                {
                    // Everything after " :" is one parameter, spaces and all.
                    trailing = line.Substring(pos + 1);
                    break;
                }

                int space = line.IndexOf(' ', pos);
                if (space < 0)
                {
                    parameters.Add(line.Substring(pos));
                    break;
                }

                parameters.Add(line.Substring(pos, space - pos));
                pos = SkipSpaces(line, space);
            }

            return new IrcLine(tags, prefix, command, parameters.ToArray(), trailing);
        }

        private static int SkipSpaces(string line, int from)
        {
            int pos = from;
            while (pos < line.Length && line[pos] == ' ')
                pos++;
            return pos;
        }

        private static Dictionary<string, string> ParseTags(string raw)
        {
            var tags = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            foreach (string pair in raw.Split(';'))
            {
                if (pair.Length == 0)
                    continue;

                int eq = pair.IndexOf('=');
                if (eq < 0)
                    tags[pair] = string.Empty;      // A valueless tag is still "present".
                else
                    tags[pair.Substring(0, eq)] = UnescapeTagValue(pair.Substring(eq + 1));
            }

            return tags;
        }

        /// <summary>
        /// IRCv3 escaping. It matters here for one reason: a display-name may
        /// contain a space, which arrives as "\s" and would otherwise be shown
        /// on the call screen with a stray backslash in it.
        /// </summary>
        private static string UnescapeTagValue(string value)
        {
            if (string.IsNullOrEmpty(value) || value.IndexOf('\\') < 0)
                return value;

            var sb = new StringBuilder(value.Length);
            for (int i = 0; i < value.Length; i++)
            {
                if (value[i] != '\\')
                {
                    sb.Append(value[i]);
                    continue;
                }

                // A trailing lone backslash is dropped, per the spec.
                if (i + 1 >= value.Length)
                    break;

                char next = value[++i];
                switch (next)
                {
                    case ':': sb.Append(';'); break;
                    case 's': sb.Append(' '); break;
                    case 'r': sb.Append('\r'); break;
                    case 'n': sb.Append('\n'); break;
                    case '\\': sb.Append('\\'); break;
                    default: sb.Append(next); break;   // Unknown escape: the char stands alone.
                }
            }

            return sb.ToString();
        }
    }
}
