using System;
using System.IO;
using System.Reflection;
using System.Text;

namespace CallFromTwitch
{
    /// <summary>
    /// A line-per-event text log next to the script, in GTA V\scripts\.
    ///
    /// The only other way the mod can say anything is Notification.Show, which
    /// lasts a few seconds, cannot be read afterwards, and is drawn by the game
    /// - so anything failing outside a tick is invisible. The chat client in
    /// particular lives on its own thread and can fail with nothing on screen.
    ///
    /// Every method swallows its own errors: logging must never be the reason
    /// the mod stops working.
    /// </summary>
    internal static class ModLog
    {
        private const string FileName = "CallFromTwitch.log";

        // Rewritten each launch and capped, so a session left running
        // overnight cannot grow a file without bound.
        private const long MaxBytes = 2 * 1024 * 1024;

        private static readonly object Gate = new object();
        private static string _path;
        private static bool _failed;

        /// <summary>Starts a fresh file for this session.</summary>
        public static void Begin(string header)
        {
            lock (Gate)
            {
                _path = null;
                _failed = false;

                try
                {
                    string path = ResolvePath();
                    if (path == null)
                    {
                        _failed = true;
                        return;
                    }

                    File.WriteAllText(path, string.Empty, new UTF8Encoding(false));
                    _path = path;
                }
                catch
                {
                    _failed = true;
                    return;
                }
            }

            Write("=== " + header + " ===");
        }

        /// <summary>Appends one line. Safe from any thread, including the IRC reader.</summary>
        public static void Write(string message)
        {
            lock (Gate)
            {
                if (_failed || _path == null)
                    return;

                try
                {
                    // A log past the cap starts over rather than being trimmed:
                    // what matters in a runaway log is what is happening now.
                    var info = new FileInfo(_path);
                    if (info.Exists && info.Length > MaxBytes)
                        File.WriteAllText(_path, "=== log restarted (size cap) ===" + Environment.NewLine,
                            new UTF8Encoding(false));

                    File.AppendAllText(_path,
                        DateTime.Now.ToString("HH:mm:ss.fff") + "  " + message + Environment.NewLine,
                        new UTF8Encoding(false));
                }
                catch
                {
                    // Locked or read-only: stop trying rather than paying for a
                    // failed write on every tick.
                    _failed = true;
                }
            }
        }

        public static void Write(string format, params object[] args)
        {
            Write(args == null || args.Length == 0 ? format : string.Format(format, args));
        }

        /// <summary>Logs an exception with its type, message and inner chain.</summary>
        public static void Error(string context, Exception ex)
        {
            if (ex == null)
            {
                Write("ERROR " + context);
                return;
            }

            var sb = new StringBuilder();
            sb.Append("ERROR ").Append(context).Append(": ");

            for (Exception e = ex; e != null; e = e.InnerException)
            {
                sb.Append(e.GetType().Name).Append(": ").Append(e.Message);
                if (e.InnerException != null)
                    sb.Append(" <- ");
            }

            Write(sb.ToString());
        }

        /// <summary>Where the log ended up, for the notification that points at it.</summary>
        public static string Path
        {
            get
            {
                lock (Gate)
                {
                    return _path;
                }
            }
        }

        /// <summary>
        /// scripts\ under the game's working directory, falling back to the
        /// folder the assembly was loaded from. Shadow copying makes
        /// Assembly.Location unreliable, so CodeBase is preferred.
        /// </summary>
        private static string ResolvePath()
        {
            foreach (string dir in Candidates())
            {
                if (string.IsNullOrEmpty(dir))
                    continue;

                try
                {
                    if (Directory.Exists(dir))
                        return System.IO.Path.Combine(dir, FileName);
                }
                catch
                {
                }
            }

            return null;
        }

        private static System.Collections.Generic.IEnumerable<string> Candidates()
        {
            string working = null;
            try { working = System.IO.Path.Combine(Directory.GetCurrentDirectory(), "scripts"); }
            catch { }
            yield return working;

            string codeBase = null;
            try
            {
                string raw = Assembly.GetExecutingAssembly().CodeBase;
                if (!string.IsNullOrEmpty(raw))
                    codeBase = System.IO.Path.GetDirectoryName(new Uri(raw).LocalPath);
            }
            catch { }
            yield return codeBase;

            string location = null;
            try
            {
                string raw = Assembly.GetExecutingAssembly().Location;
                if (!string.IsNullOrEmpty(raw))
                    location = System.IO.Path.GetDirectoryName(raw);
            }
            catch { }
            yield return location;
        }
    }
}
