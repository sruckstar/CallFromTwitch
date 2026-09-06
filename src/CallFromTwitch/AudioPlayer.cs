using System;
using System.IO;
using NAudio.Wave;

namespace CallFromTwitch
{
    /// <summary>
    /// Plays a WAV held in memory. One clip at a time: starting a new one
    /// cuts off whatever is currently talking.
    /// </summary>
    internal sealed class AudioPlayer : IDisposable
    {
        private WaveOutEvent _output;
        private WaveStream _reader;
        private MemoryStream _source;
        private readonly object _gate = new object();

        public bool IsPlaying
        {
            get
            {
                lock (_gate)
                {
                    return _output != null && _output.PlaybackState == PlaybackState.Playing;
                }
            }
        }

        public void Play(byte[] wavBytes, float volume)
        {
            lock (_gate)
            {
                StopLocked();

                _source = new MemoryStream(wavBytes, writable: false);
                try
                {
                    _reader = new WaveFileReader(_source);
                    // WaveOutEvent uses its own thread, so playback does not
                    // block the game tick that started it.
                    _output = new WaveOutEvent { Volume = Clamp01(volume) };
                    _output.Init(_reader);
                    _output.Play();
                }
                catch
                {
                    // Never leave half-built state behind on a malformed WAV.
                    StopLocked();
                    throw;
                }
            }
        }

        public void Stop()
        {
            lock (_gate)
            {
                StopLocked();
            }
        }

        private void StopLocked()
        {
            if (_output != null)
            {
                try { _output.Stop(); } catch { }
                try { _output.Dispose(); } catch { }
                _output = null;
            }

            if (_reader != null)
            {
                try { _reader.Dispose(); } catch { }
                _reader = null;
            }

            if (_source != null)
            {
                try { _source.Dispose(); } catch { }
                _source = null;
            }
        }

        private static float Clamp01(float value)
        {
            if (value < 0f) return 0f;
            if (value > 1f) return 1f;
            return value;
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
