"""Server settings. Env vars (CFT_*) override every default."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """Read events.env into the environment. Existing variables win."""
    try:
        if not path.is_file():
            return

        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass


_load_env_file(BASE_DIR / "events.env")


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_opt_float(key: str) -> float | None:
    """None when unset, so a preset's own value stands."""
    try:
        return float(os.environ[key])
    except (KeyError, ValueError):
        return None


def _env_opt_int(key: str) -> int | None:
    try:
        return int(os.environ[key])
    except (KeyError, ValueError):
        return None


@dataclass
class Config:
    host: str = field(default_factory=lambda: _env_str("CFT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("CFT_PORT", 8765))

    # Piper
    piper_model: Path = field(
        default_factory=lambda: Path(
            _env_str("CFT_PIPER_MODEL", str(BASE_DIR / "models" / "piper" / "en_US-ryan-high.onnx"))
        )
    )
    piper_length_scale: float = field(default_factory=lambda: _env_float("CFT_PIPER_LENGTH_SCALE", 1.0))
    piper_cuda: bool = field(default_factory=lambda: _env_bool("CFT_PIPER_CUDA", False))
    # Silence spliced between sentence chunks, in seconds.
    piper_sentence_silence: float = field(
        default_factory=lambda: _env_float("CFT_PIPER_SENTENCE_SILENCE", 0.4)
    )
    # Longest pause kept inside a sentence, in seconds. 0 keeps Piper's timing.
    piper_comma_pause: float = field(
        default_factory=lambda: _env_float("CFT_PIPER_COMMA_PAUSE", 0.09)
    )

    # RVC
    rvc_models_dir: Path = field(
        default_factory=lambda: Path(_env_str("CFT_RVC_MODELS", str(BASE_DIR / "models" / "rvc")))
    )
    rvc_device: str = field(default_factory=lambda: _env_str("CFT_RVC_DEVICE", "cuda:0"))
    rvc_enabled: bool = field(default_factory=lambda: _env_bool("CFT_RVC_ENABLED", True))
    rvc_f0method: str = field(default_factory=lambda: _env_str("CFT_RVC_F0METHOD", "rmvpe"))
    rvc_f0up_key: int = field(default_factory=lambda: _env_int("CFT_RVC_F0UP_KEY", 0))
    rvc_index_rate: float = field(default_factory=lambda: _env_float("CFT_RVC_INDEX_RATE", 0.5))
    rvc_protect: float = field(default_factory=lambda: _env_float("CFT_RVC_PROTECT", 0.33))

    # Telephone effect applied after RVC. See phone_fx.py.
    phone_enabled: bool = field(default_factory=lambda: _env_bool("CFT_PHONE_ENABLED", True))
    # One of phone_fx.PRESETS: gta, radio, soft.
    phone_preset: str = field(default_factory=lambda: _env_str("CFT_PHONE_PRESET", "gta"))
    # Optional: unset means the preset's own value stands.
    phone_low_hz: float | None = field(default_factory=lambda: _env_opt_float("CFT_PHONE_LOW_HZ"))
    phone_high_hz: float | None = field(default_factory=lambda: _env_opt_float("CFT_PHONE_HIGH_HZ"))
    phone_order: int | None = field(default_factory=lambda: _env_opt_int("CFT_PHONE_ORDER"))
    phone_drive: float | None = field(default_factory=lambda: _env_opt_float("CFT_PHONE_DRIVE"))
    phone_compress_ratio: float | None = field(
        default_factory=lambda: _env_opt_float("CFT_PHONE_COMPRESS_RATIO")
    )
    phone_compress_threshold: float | None = field(
        default_factory=lambda: _env_opt_float("CFT_PHONE_COMPRESS_THRESHOLD")
    )
    phone_codec_rate: int | None = field(default_factory=lambda: _env_opt_int("CFT_PHONE_CODEC_RATE"))
    phone_codec_bits: int | None = field(default_factory=lambda: _env_opt_int("CFT_PHONE_CODEC_BITS"))
    phone_noise_db: float | None = field(default_factory=lambda: _env_opt_float("CFT_PHONE_NOISE_DB"))
    phone_output_peak: float | None = field(
        default_factory=lambda: _env_opt_float("CFT_PHONE_OUTPUT_PEAK")
    )

    default_voice: str = field(default_factory=lambda: _env_str("CFT_DEFAULT_VOICE", "Michael"))
    # Ask at startup which voice to use when several are installed.
    voice_prompt: bool = field(default_factory=lambda: _env_bool("CFT_VOICE_PROMPT", True))
    # Load the lazy model weights at startup rather than inside the first call.
    warmup: bool = field(default_factory=lambda: _env_bool("CFT_WARMUP", True))
    # Trim startup chatter and per-request framework logs to a few status lines.
    quiet: bool = field(default_factory=lambda: _env_bool("CFT_QUIET", False))
    max_text_length: int = field(default_factory=lambda: _env_int("CFT_MAX_TEXT", 300))

    # ---- Paid Twitch events: channel points and money donations. See events.py.
    events_enabled: bool = field(default_factory=lambda: _env_bool("CFT_EVENTS", False))
    # The broadcaster's own token, scope channel:read:redemptions.
    twitch_channel: str = field(default_factory=lambda: _env_str("CFT_TWITCH_CHANNEL", ""))
    twitch_client_id: str = field(default_factory=lambda: _env_str("CFT_TWITCH_CLIENT_ID", ""))
    twitch_token: str = field(default_factory=lambda: _env_str("CFT_TWITCH_TOKEN", ""))
    # Empty means any reward triggers a call; a title here limits it to one.
    points_reward: str = field(default_factory=lambda: _env_str("CFT_POINTS_REWARD", ""))
    # DonationAlerts OAuth token, scope oauth-donation-subscribe.
    donationalerts_token: str = field(default_factory=lambda: _env_str("CFT_DA_TOKEN", ""))
    # Events waiting for the mod to fetch them.
    events_max_queue: int = field(default_factory=lambda: _env_int("CFT_EVENTS_QUEUE", 50))
    # Accept POST /events/test.
    events_test: bool = field(default_factory=lambda: _env_bool("CFT_EVENTS_TEST", True))

    # ---- Calls spoken here and held for the game. See calls.py.
    # Lines queued for synthesis plus lines spoken and not yet collected.
    # Small on purpose: a backlog the player has to sit through is worse than
    # a line that never arrives.
    call_max_pending: int = field(default_factory=lambda: _env_int("CFT_CALL_QUEUE", 8))
    # Seconds a call may wait for the game before it is thrown away. Long,
    # because a pause menu is a legitimate reason to hold one, but finite:
    # a server left running overnight must not open with yesterday's calls.
    call_max_age: float = field(default_factory=lambda: _env_float("CFT_CALL_MAX_AGE", 900.0))

    def phone_overrides(self) -> dict:
        """The knobs the user actually set, to lay over the chosen preset."""
        return {
            "low_hz": self.phone_low_hz,
            "high_hz": self.phone_high_hz,
            "order": self.phone_order,
            "drive": self.phone_drive,
            "compress_ratio": self.phone_compress_ratio,
            "compress_threshold": self.phone_compress_threshold,
            "codec_rate": self.phone_codec_rate,
            "codec_bits": self.phone_codec_bits,
            "noise_db": self.phone_noise_db,
            "output_peak": self.phone_output_peak,
        }

    def rvc_params(self) -> dict:
        return {
            "f0method": self.rvc_f0method,
            "f0up_key": self.rvc_f0up_key,
            "index_rate": self.rvc_index_rate,
            "protect": self.rvc_protect,
        }
