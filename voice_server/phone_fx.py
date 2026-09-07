"""
Telephone colouring for the finished voice line.

GTA's own radio/phone submix lives inside RAGE's mixer and only touches sounds
the game itself plays; our WAV goes out through NAudio straight to the Windows
device, past the engine. So the effect is rebuilt here, in the order applied:

  1. band-pass 300-3400 Hz - the telephone passband, and most of the effect.
  2. soft saturation      - the mild overdrive of a cheap handset.
  3. compression          - phone lines squash dynamics flat.
  4. codec grit           - the aliasing burr of a low-bitrate speech codec,
                            off unless a preset asks for it.
  5. line noise           - a whisper of hiss, so the gaps are not dead silent.

The result is normalised back up, because the band-pass throws away most of
the signal's energy.
"""
from __future__ import annotations

import io
import logging
import wave
from dataclasses import dataclass, replace

import numpy as np
from scipy.signal import butter, sosfilt

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PhoneFX:
    """One complete setting of the effect. Every field is a knob."""

    # Band-pass. `order` is per edge; a steep slope is what sells it.
    low_hz: float = 300.0
    high_hz: float = 3400.0
    order: int = 6

    # Saturation. 1.0 is a straight wire; past ~4 the vowels start to buzz.
    drive: float = 1.8

    # Compression, on an RMS envelope rather than per sample.
    compress_ratio: float = 3.0
    compress_threshold: float = 0.25

    # Decimate to this rate and interpolate back, at this depth. 0 disables.
    # Off by default: the aliasing burr smears the formants RVC just built, and
    # the character stops being recognisable. `radio` still opts in.
    codec_rate: int = 0
    codec_bits: int = 0

    # Line hiss, in dBFS relative to the normalised peak.
    noise_db: float = -48.0

    # Peak the finished clip is normalised to, under 1.0 so the make-up gain
    # cannot clip on a stray sample.
    output_peak: float = 0.97


PRESETS: dict[str, PhoneFX] = {
    # Modelled on GTA's dialogue calls: a handset, every word intelligible.
    "gta": PhoneFX(),
    # Police-radio territory. Narrower, dirtier, harder to follow.
    "radio": PhoneFX(
        low_hz=400.0,
        high_hz=3000.0,
        order=8,
        drive=4.0,
        compress_ratio=6.0,
        compress_threshold=0.15,
        codec_rate=6000,
        codec_bits=8,
        noise_db=-38.0,
    ),
    # Just the colouring, none of the damage.
    "soft": PhoneFX(
        low_hz=250.0,
        high_hz=4000.0,
        order=4,
        drive=1.0,
        compress_ratio=2.0,
        compress_threshold=0.35,
        codec_rate=0,
        noise_db=0.0,
    ),
}


def preset(name: str) -> PhoneFX:
    """Look up a preset by name, falling back to 'gta' with a warning."""
    key = (name or "").strip().lower()
    if key in PRESETS:
        return PRESETS[key]
    log.warning("Unknown phone preset %r; using 'gta'. Known: %s",
                name, ", ".join(sorted(PRESETS)))
    return PRESETS["gta"]


def with_overrides(base: PhoneFX, overrides: dict) -> PhoneFX:
    """Apply the non-None entries of `overrides` on top of a preset."""
    fields = {key: value for key, value in overrides.items() if value is not None}
    return replace(base, **fields) if fields else base


def apply(wav_bytes: bytes, fx: PhoneFX) -> bytes:
    """
    Run the effect over a PCM WAV and return a WAV of the same format.

    Sample rate and channel count are preserved, so neither the mod's header
    read nor NAudio has to care that this step ran at all.
    """
    audio, sample_rate, channels, sample_width = _read_wav(wav_bytes)

    if audio.size == 0:
        return wav_bytes

    audio = _bandpass(audio, sample_rate, fx)
    audio = _saturate(audio, fx.drive)
    audio = _compress(audio, sample_rate, fx)
    audio = _codec_grit(audio, sample_rate, fx)
    audio = _normalise(audio, fx.output_peak)
    audio = _add_noise(audio, sample_rate, fx)

    return _write_wav(np.clip(audio, -1.0, 1.0), sample_rate, channels, sample_width)


def _bandpass(audio: np.ndarray, sample_rate: int, fx: PhoneFX) -> np.ndarray:
    """
    The passband, as second-order sections.

    sos rather than a single b/a pair: at order 6+ the transfer-function form
    is numerically fragile enough to ring or blow up outright.
    """
    nyquist = sample_rate / 2.0
    low = max(fx.low_hz, 1.0) / nyquist
    # A band edge at or above Nyquist is not representable; back it off rather
    # than letting butter raise.
    high = min(fx.high_hz / nyquist, 0.99)

    if low >= high:
        log.warning("Phone band %g-%g Hz is empty at %d Hz; skipping band-pass.",
                    fx.low_hz, fx.high_hz, sample_rate)
        return audio

    sos = butter(fx.order, [low, high], btype="band", output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _saturate(audio: np.ndarray, drive: float) -> np.ndarray:
    """
    Soft clipping through tanh.

    Divided by tanh(drive) so the curve still maps 1.0 to 1.0; without that,
    raising the drive would raise the level and every later stage would shift.
    """
    if drive <= 1.0:
        return audio
    return (np.tanh(audio * drive) / np.tanh(drive)).astype(np.float32)


def _compress(audio: np.ndarray, sample_rate: int, fx: PhoneFX) -> np.ndarray:
    """
    Downward compression driven by a smoothed RMS envelope.

    Fast attack, slow release, so the gain dives onto a syllable and eases
    back between them instead of pumping on every plosive.
    """
    if fx.compress_ratio <= 1.0:
        return audio

    envelope = _envelope(np.abs(audio), sample_rate, attack_ms=5.0, release_ms=80.0)

    threshold = max(fx.compress_threshold, 1e-4)
    over = envelope > threshold
    gain = np.ones_like(envelope)
    # Above the knee, keep `threshold` and pass only 1/ratio of the excess.
    gain[over] = (
        threshold + (envelope[over] - threshold) / fx.compress_ratio
    ) / envelope[over]

    return (audio * gain).astype(np.float32)


def _envelope(rectified: np.ndarray, sample_rate: int, attack_ms: float,
              release_ms: float) -> np.ndarray:
    """One-pole envelope follower with separate attack and release times."""
    attack = _coefficient(attack_ms, sample_rate)
    release = _coefficient(release_ms, sample_rate)

    out = np.empty_like(rectified)
    level = 0.0
    for index, sample in enumerate(rectified):
        coefficient = attack if sample > level else release
        level = coefficient * level + (1.0 - coefficient) * sample
        out[index] = level
    return out


def _coefficient(time_ms: float, sample_rate: int) -> float:
    """Per-sample decay for a given time constant."""
    if time_ms <= 0.0:
        return 0.0
    return float(np.exp(-1.0 / (sample_rate * time_ms / 1000.0)))


def _codec_grit(audio: np.ndarray, sample_rate: int, fx: PhoneFX) -> np.ndarray:
    """
    Decimate to `codec_rate` and interpolate straight back.

    No anti-alias filter on the way down, because the aliasing is the effect.
    The band-pass has already removed anything that would fold back badly.
    """
    if fx.codec_rate <= 0 or fx.codec_rate >= sample_rate:
        return audio

    ratio = sample_rate / float(fx.codec_rate)
    decimated_length = max(int(audio.size / ratio), 1)

    source = np.arange(decimated_length, dtype=np.float64) * ratio
    decimated = np.interp(source, np.arange(audio.size, dtype=np.float64), audio)

    if fx.codec_bits > 0:
        levels = float(2 ** (fx.codec_bits - 1))
        decimated = np.round(decimated * levels) / levels

    # Back up to the original length, so the caller's header stays true.
    target = np.arange(audio.size, dtype=np.float64) / ratio
    restored = np.interp(target, np.arange(decimated_length, dtype=np.float64), decimated)
    return restored.astype(np.float32)


def _normalise(audio: np.ndarray, peak: float) -> np.ndarray:
    """Make-up gain: the band-pass discards most of the signal's energy."""
    current = float(np.max(np.abs(audio))) if audio.size else 0.0
    if current <= 1e-8:
        return audio
    return (audio * (peak / current)).astype(np.float32)


def _add_noise(audio: np.ndarray, sample_rate: int, fx: PhoneFX) -> np.ndarray:
    """
    A floor of hiss, band-limited to the same passband as the voice and added
    after normalisation so its level is absolute.

    Last, because run through the compressor it would breathe with the speech.
    """
    if fx.noise_db >= 0.0:
        return audio
    amplitude = 10.0 ** (fx.noise_db / 20.0)
    noise = np.random.default_rng().standard_normal(audio.size).astype(np.float32)

    nyquist = sample_rate / 2.0
    low = max(fx.low_hz, 1.0) / nyquist
    high = min(fx.high_hz / nyquist, 0.99)
    if low < high:
        sos = butter(fx.order, [low, high], btype="band", output="sos")
        noise = sosfilt(sos, noise).astype(np.float32)
        peak = float(np.max(np.abs(noise)))
        if peak > 1e-8:
            noise /= peak

    return (audio + noise * amplitude).astype(np.float32)


def _read_wav(wav_bytes: bytes) -> tuple[np.ndarray, int, int, int]:
    """Decode a PCM WAV to mono float32 in [-1, 1], mixing down if needed."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM, got {sample_width * 8}-bit")

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    return audio, sample_rate, channels, sample_width


def _write_wav(audio: np.ndarray, sample_rate: int, channels: int,
               sample_width: int) -> bytes:
    """Re-encode, restoring the original channel count from the mono result."""
    if channels > 1:
        audio = np.repeat(audio[:, None], channels, axis=1).reshape(-1)

    samples = np.clip(audio * 32767.0, -32768, 32767).astype("<i2")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())
    return buffer.getvalue()
