using System;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace CallFromTwitch
{
    /// <summary>
    /// Talks to the local Python voice server (Piper TTS -> RVC).
    /// Everything here runs off the game thread; the caller polls the Task.
    /// </summary>
    internal sealed class VoiceClient : IDisposable
    {
        private readonly HttpClient _http;

        public VoiceClient(string baseUrl, int timeoutSeconds)
        {
            Uri baseAddress;
            string normalized = (baseUrl ?? string.Empty).TrimEnd('/') + "/";
            if (!Uri.TryCreate(normalized, UriKind.Absolute, out baseAddress))
                throw new VoiceServerException(string.Format(
                    "server address '{0}' is not a valid URL - check [Server] Host/Port in CallFromTwitch.ini",
                    baseUrl));

            _http = new HttpClient
            {
                BaseAddress = baseAddress,
                Timeout = TimeSpan.FromSeconds(timeoutSeconds)
            };
        }

        /// <summary>
        /// Hands a line to the server and returns without waiting for audio.
        ///
        /// The mod cannot wait: a player who pauses the game stops every
        /// script, so a reply that arrives during the menu is a reply nobody
        /// is left to collect. The server speaks the line on its own thread
        /// and holds the result until <see cref="AckCallAsync"/> confirms it.
        /// </summary>
        public async Task<bool> SubmitAsync(string text, string user, string kind, CancellationToken token)
        {
            // Hand-rolled: the payload is three short strings, and a
            // serializer would mean shipping another DLL alongside the script.
            string payload = "{\"text\":" + JsonString(text)
                + ",\"user\":" + JsonString(user ?? string.Empty)
                + ",\"kind\":" + JsonString(kind ?? "chat") + "}";

            using (var content = new StringContent(payload, Encoding.UTF8, "application/json"))
            using (var response = await _http.PostAsync("submit", content, token).ConfigureAwait(false))
            {
                if (!response.IsSuccessStatusCode)
                {
                    string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    throw new VoiceServerException(
                        string.Format("server returned {0}: {1}", (int)response.StatusCode, Trim(body, 200)));
                }

                return true;
            }
        }

        /// <summary>
        /// Sends the streamer's INI filters up, so the server can decide for
        /// itself which redemptions become calls. Failure is not fatal: the
        /// server has defaults, and this is retried whenever it comes back.
        /// </summary>
        public async Task<bool> SendRulesAsync(string payload, CancellationToken token)
        {
            using (var content = new StringContent(payload, Encoding.UTF8, "application/json"))
            using (var response = await _http.PostAsync("rules", content, token).ConfigureAwait(false))
            {
                return response.IsSuccessStatusCode;
            }
        }

        /// <summary>
        /// Asks whether a spoken call is waiting, as raw JSON - parsing
        /// belongs next to the type that knows the shape.
        ///
        /// The timeout is short on purpose: this repeats every second, and one
        /// request that hung for the synthesis timeout would stack up behind
        /// itself.
        /// </summary>
        public async Task<string> FetchCallAsync(CancellationToken token)
        {
            using (var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5)))
            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(token, timeout.Token))
            using (var response = await _http.GetAsync("call", linked.Token).ConfigureAwait(false))
            {
                if (!response.IsSuccessStatusCode)
                    return null;

                return await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            }
        }

        /// <summary>
        /// Downloads the audio for one ready call. The server still holds it
        /// afterwards - only the ack releases it.
        /// </summary>
        public async Task<byte[]> FetchCallAudioAsync(string callId, CancellationToken token)
        {
            using (var response = await _http.GetAsync("call/" + Uri.EscapeDataString(callId) + "/audio", token)
                .ConfigureAwait(false))
            {
                if (!response.IsSuccessStatusCode)
                {
                    string body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    throw new VoiceServerException(
                        string.Format("server returned {0}: {1}", (int)response.StatusCode, Trim(body, 200)));
                }

                return await response.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
            }
        }

        /// <summary>
        /// Tells the server the audio arrived, which is the only thing that
        /// makes it stop offering this call.
        /// </summary>
        public async Task<bool> AckCallAsync(string callId, CancellationToken token)
        {
            string payload = "{\"id\":" + JsonString(callId) + "}";

            using (var content = new StringContent(payload, Encoding.UTF8, "application/json"))
            using (var response = await _http.PostAsync("call/ack", content, token).ConfigureAwait(false))
            {
                return response.IsSuccessStatusCode;
            }
        }

        /// <summary>True if the server answers /health. Used to warn the player early.</summary>
        public async Task<bool> IsAliveAsync(CancellationToken token)
        {
            try
            {
                using (var response = await _http.GetAsync("health", token).ConfigureAwait(false))
                {
                    return response.IsSuccessStatusCode;
                }
            }
            catch
            {
                return false;
            }
        }

        private static string Trim(string value, int max)
        {
            if (string.IsNullOrEmpty(value)) return string.Empty;
            value = value.Replace('\n', ' ').Replace('\r', ' ');
            return value.Length <= max ? value : value.Substring(0, max) + "...";
        }

        /// <summary>A JSON string literal. Shared with the callers that build
        /// their own small payloads rather than dragging in a serializer.</summary>
        public static string Json(string value)
        {
            return JsonString(value ?? string.Empty);
        }

        private static string JsonString(string value)
        {
            var sb = new StringBuilder(value.Length + 2);
            sb.Append('"');
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        // Control chars and everything non-ASCII, so the body
                        // survives however the server decodes it.
                        if (c < 0x20 || c > 0x7E)
                            sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else
                            sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
            return sb.ToString();
        }

        public void Dispose()
        {
            _http.Dispose();
        }
    }

    internal sealed class VoiceServerException : Exception
    {
        public VoiceServerException(string message) : base(message) { }
    }
}
