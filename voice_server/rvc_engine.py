"""RVC: re-timbre Piper's neutral speech into a specific character's voice."""
from __future__ import annotations

import contextlib
import io
import logging
import os
import struct
import tempfile
import warnings
import wave
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)

# rvc-python and torch report progress with bare print() calls and warnings,
# none of it through logging, so a log level cannot reach it.
_QUIET = os.environ.get("CFT_QUIET", "").strip().lower() in {"1", "true", "yes", "on"}


@contextlib.contextmanager
def _hushed():
    """Swallow a library's stdout chatter and warnings during a noisy call."""
    if not _QUIET:
        yield
        return
    sink = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with contextlib.redirect_stdout(sink):
            yield
    captured = sink.getvalue().strip()
    if captured:
        log.debug("rvc output: %s", captured)


class RVCEngine:
    """
    Holds one RVCInference instance and swaps the active model per character.

    Model discovery covers both layouts a download can arrive in:
      * <models_dir>/<name>.pth              -> voice "<name>"
      * <models_dir>/<Character>/<any>.pth   -> voice "<Character>"
    Lookup is case-insensitive. A folder's voice is its folder name, but the
    .pth stem still resolves as an alias, so an ini written for the flat
    layout keeps working after the models are tidied into folders.
    """

    def __init__(self, models_dir: Path, device: str = "cuda:0", **params):
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.params = params
        self._rvc = None
        self._loaded_voice: str | None = None
        self._lock = Lock()

    def _discover(self) -> dict[str, Path]:
        """Map voice name -> .pth path, folder voices taking the folder's name."""
        found: dict[str, Path] = {}
        for pth in sorted(self.models_dir.glob("*.pth")):
            found[pth.stem] = pth
        for sub in sorted(p for p in self.models_dir.iterdir() if p.is_dir()):
            models = sorted(sub.glob("*.pth"))
            if not models:
                continue
            if len(models) > 1:
                log.warning(
                    "%d .pth files in %s -- using %s.", len(models), sub, models[0].name
                )
            found[sub.name] = models[0]
        return found

    def _aliases(self) -> dict[str, str]:
        """
        Extra spellings that map onto a discovered voice, lowercased.

        The .pth stems inside a character folder. A folder name always wins,
        so an alias can never shadow a real voice.
        """
        found = self._discover()
        taken = {voice.lower() for voice in found}
        alias: dict[str, str] = {}
        for voice, path in found.items():
            stem = path.stem.lower()
            if stem in taken or stem in alias:
                continue
            alias[stem] = voice
        return alias

    def available_voices(self) -> list[str]:
        return sorted(self._discover(), key=str.lower)

    def resolve(self, name: str) -> Path | None:
        """The model file for a voice name, matched case-insensitively."""
        voice = self.canonical(name)
        return self._discover()[voice] if voice else None

    def canonical(self, name: str) -> str | None:
        """The voice name as discovery spells it, or None when unknown."""
        found = self._discover()
        lowered = (name or "").lower()
        if not lowered:
            return None
        for voice in found:
            if voice.lower() == lowered:
                return voice
        return self._aliases().get(lowered)

    def has_voice(self, name: str) -> bool:
        return self.resolve(name) is not None

    def _resolve_device(self) -> str:
        """Fall back to CPU rather than dying when CUDA is unavailable."""
        if not self.device.startswith("cuda"):
            return self.device
        try:
            import torch

            if torch.cuda.is_available():
                return self.device
            log.warning("CUDA requested but not available; falling back to CPU (slower).")
        except Exception as exc:
            log.warning("Could not probe CUDA (%s); falling back to CPU.", exc)
        return "cpu"

    def _ensure_engine(self):
        if self._rvc is not None:
            return
        # Imported lazily: torch costs seconds to import and pulls in CUDA.
        from rvc_python.infer import RVCInference

        device = self._resolve_device()
        log.debug("Initialising RVC on %s", device)
        with _hushed():
            self._rvc = RVCInference(device=device)
        if self.params:
            self._apply_params()

    def _apply_params(self):
        """set_params has taken both kwargs and a dict across versions."""
        try:
            self._rvc.set_params(**self.params)
        except TypeError:
            self._rvc.set_params(self.params)

    def _find_index(self, model_path: Path) -> str:
        """
        Locate the .index that belongs to a model.

        A same-stem sibling first, then a lone .index in the same folder -
        downloads usually ship one named after the training run. Without it
        rvc-python silently converts at reduced fidelity.
        """
        same_stem = model_path.with_suffix(".index")
        if same_stem.is_file():
            return str(same_stem)

        candidates = sorted(model_path.parent.glob("*.index"))
        if len(candidates) == 1:
            log.debug("Using index %s for %s", candidates[0].name, model_path.name)
            return str(candidates[0])
        if len(candidates) > 1:
            log.warning(
                "%d .index files in %s and none named %s -- skipping index. "
                "Rename the right one to %s to use it.",
                len(candidates), model_path.parent, same_stem.name, same_stem.name,
            )
        return ""

    def _ensure_voice(self, voice: str):
        # The canonical name, so an alias does not look like a different voice
        # and reload the model that is already resident.
        name = self.canonical(voice)
        if name is None:
            raise FileNotFoundError(
                f"RVC model '{voice}' not found under {self.models_dir}. "
                f"Available: {', '.join(self.available_voices()) or 'none'}"
            )
        if self._loaded_voice == name:
            return
        model_path = self._discover()[name]
        log.debug("Loading RVC model %s (%s)", model_path.name, name)
        with _hushed():
            self._rvc.load_model(str(model_path), index_path=self._find_index(model_path))
        self._loaded_voice = name

    def warmup(self, voice: str) -> None:
        """
        Pay the one-off cost up front.

        rvc-python defers hubert and rmvpe until the first infer_file, which
        would put ~60s inside the player's first line and blow past the mod's
        HTTP timeout.
        """
        if not self.has_voice(voice):
            log.warning("Cannot warm up: no RVC model '%s'", voice)
            return
        try:
            self.convert(self._silence(), voice)
            log.debug("RVC warmed up on '%s'", voice)
        except Exception:
            # Not fatal: the first real call retries anyway.
            log.exception("RVC warmup failed; the first call will be slow")

    @staticmethod
    def _silence(seconds: float = 0.6, rate: int = 16000) -> bytes:
        """A short near-silent WAV: enough signal for the full pipeline to run."""
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(struct.pack("<h", 0) * int(rate * seconds))
        return buffer.getvalue()

    def convert(self, wav_bytes: bytes, voice: str) -> bytes:
        """Run voice conversion over a WAV and return the converted WAV."""
        with self._lock:
            self._ensure_engine()
            self._ensure_voice(voice)

            # rvc-python works on file paths, so bounce through a temp dir.
            with tempfile.TemporaryDirectory(prefix="cft_") as tmp:
                src = Path(tmp) / "in.wav"
                dst = Path(tmp) / "out.wav"
                src.write_bytes(wav_bytes)
                with _hushed():
                    self._rvc.infer_file(str(src), str(dst))
                if not dst.is_file():
                    raise RuntimeError("RVC produced no output file")
                return dst.read_bytes()
