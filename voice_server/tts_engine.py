"""Piper TTS: text -> neutral speech, the carrier signal RVC will re-timbre."""
from __future__ import annotations

import io
import logging
import wave
from pathlib import Path
from threading import Lock

import numpy as np
from piper import PiperVoice, SynthesisConfig

log = logging.getLogger(__name__)


class TTSEngine:
    """Wraps a single Piper voice. Kept warm for the lifetime of the server."""

    # Resolution of the pause scan, and the level below which - relative to
    # the clip's own peak - a frame counts as silence.
    _FRAME_SECONDS = 0.01
    _SILENCE_DB = -45.0

    def __init__(
        self,
        model_path: Path,
        length_scale: float = 1.0,
        use_cuda: bool = False,
        sentence_silence: float = 0.4,
        comma_pause: float = 0.09,
    ):
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"Piper model not found: {self.model_path}\n"
                f"Download one with: python -m piper.download_voices en_US-ryan-high"
            )

        log.debug("Loading Piper voice %s (cuda=%s)", self.model_path.name, use_cuda)
        self.voice = PiperVoice.load(str(self.model_path), use_cuda=use_cuda)
        # normalize_audio off: Piper applies it per sentence, so the loudness
        # jumps between chunks. _join normalises once over the whole utterance.
        self.config = SynthesisConfig(length_scale=length_scale, normalize_audio=False)
        self.sentence_silence = max(0.0, sentence_silence)
        # Longest pause left standing inside a sentence; 0 turns the pass off.
        self.comma_pause = comma_pause
        # onnxruntime sessions are not guaranteed reentrant; serialize calls.
        self._lock = Lock()

    def synthesize(self, text: str) -> bytes:
        """Return a complete WAV file (header included) as bytes."""
        with self._lock:
            chunks = list(self.voice.synthesize(text, syn_config=self.config))

        if not chunks:
            raise RuntimeError(f"Piper produced no audio for {text!r}")

        sample_rate = chunks[0].sample_rate
        audio = self._join(chunks, sample_rate)
        return self._to_wav(audio, sample_rate, chunks[0].sample_channels)

    def _join(self, chunks, sample_rate: int) -> np.ndarray:
        """
        Concatenate sentence chunks with a real pause between them.

        Piper yields one chunk per sentence and writes them back to back, so
        the gap has to be inserted here.
        """
        gap = np.zeros(int(sample_rate * self.sentence_silence), dtype=np.float32)

        parts: list[np.ndarray] = []
        for index, chunk in enumerate(chunks):
            if index and gap.size:
                parts.append(gap)
            # Per sentence: run after concatenation, the pass would see the
            # gap spliced in above as just another silence and shorten it too.
            parts.append(
                self._shrink_pauses(
                    np.asarray(chunk.audio_float_array, dtype=np.float32), sample_rate
                )
            )

        audio = np.concatenate(parts) if len(parts) > 1 else parts[0]

        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1e-8:
            audio = audio / peak

        return np.clip(audio, -1.0, 1.0)

    def _shrink_pauses(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """
        Shorten the silences Piper leaves inside a sentence.

        Piper answers a comma with a 150-280 ms rest, long enough that "Good
        luck, man" lands as two utterances; trimming the gap binds them back
        into one line. Only interior silence is touched - the head and tail
        room are what the caller splices its sentence gaps against.
        """
        if self.comma_pause <= 0 or audio.size == 0:
            return audio

        frame = int(sample_rate * self._FRAME_SECONDS)
        if frame <= 0:
            return audio

        frames = audio.size // frame
        if frames == 0:
            return audio

        peak = float(np.max(np.abs(audio)))
        if peak <= 1e-8:
            return audio

        # Framewise RMS against the clip's own peak, so the threshold means
        # the same thing whether the take came out loud or quiet.
        windows = audio[: frames * frame].reshape(frames, frame) / peak
        rms = np.sqrt(np.mean(windows * windows, axis=1) + 1e-12)
        silent = 20.0 * np.log10(rms + 1e-12) < self._SILENCE_DB

        voiced = np.flatnonzero(~silent)
        if voiced.size == 0:
            return audio

        first, last = int(voiced[0]), int(voiced[-1])
        keep = max(1, int(round(self.comma_pause / self._FRAME_SECONDS)))

        pieces: list[np.ndarray] = [audio[: first * frame]]
        index = first
        while index <= last:
            if not silent[index]:
                pieces.append(audio[index * frame : (index + 1) * frame])
                index += 1
                continue

            run_end = index
            while run_end <= last and silent[run_end]:
                run_end += 1

            length = min(run_end - index, keep)
            pieces.append(audio[index * frame : (index + length) * frame])
            index = run_end

        pieces.append(audio[(last + 1) * frame :])
        return np.concatenate(pieces)

    @staticmethod
    def _to_wav(audio: np.ndarray, sample_rate: int, channels: int) -> bytes:
        samples = np.clip(audio * 32767.0, -32768, 32767).astype("<i2")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(samples.tobytes())
        return buffer.getvalue()
