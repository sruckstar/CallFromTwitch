r"""
CallFromTwitch.ini, read from the server side.

The streamer edits one file, and it is the one that sits in GTA V\scripts\
next to the DLL - not a copy in this folder. Everything the chat reader needs
to decide who may ask for a call lives in its [Twitch] section, so the server
reads it directly instead of waiting for the mod to send the settings up. That
is the whole point: chat has to work with the game closed, and a game that is
closed pushes nothing.

The file is watched rather than read once. A streamer who raises a cooldown
mid-stream alt-tabs, saves, and expects the next message to obey it; making
them restart the server to apply an ini edit would be worse than the old
behaviour, where restarting the game did it.

Parsed here rather than with configparser: SHVDN's ScriptSettings accepts ':'
as a key/value separator alongside '=', writes no quoting rules, and tolerates
duplicate sections. Matching its quirks matters more than strictness - a value
this reads differently from the way the game reads it is a bug the streamer
cannot see.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("cft.ini")
log.setLevel(logging.INFO)

BASE_DIR = Path(__file__).resolve().parent

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}


def parse_ini(text: str) -> dict:
    """
    An INI as ScriptSettings reads it: sections in brackets, keys split at the
    first '=' or ':', ';' and '#' starting a comment.

    The colon is the quirk worth keeping. It is why [Server] carries Host and
    Port rather than a Url, and why a pasted "https://twitch.tv/name" arrives
    as the bare word "https" - the game truncates it that way, so we must too,
    or the two halves disagree about what the streamer configured.
    """
    sections: dict = {}
    current = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#":
            continue

        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, {})
            continue

        cuts = [pos for pos in (line.find("="), line.find(":")) if pos >= 0]
        if not cuts:
            continue
        cut = min(cuts)

        key = line[:cut].strip().lower()
        value = line[cut + 1:].strip()
        if key:
            sections.setdefault(current, {})[key] = value

    return sections


def clean_channel(value: str) -> str:
    """
    What the streamer is likely to have pasted, reduced to a channel name.

    Mirrors TwitchConfig.CleanChannel in the mod, including its reading of a
    lone "https": that is all the game's parser leaves of a pasted URL, and
    connecting to a channel literally named "https" is the confusing failure
    this avoids.
    """
    channel = (value or "").strip()
    if not channel or channel.lower() in {"http", "https"}:
        return ""

    marker = "twitch.tv/"
    at = channel.lower().find(marker)
    if at >= 0:
        channel = channel[at + len(marker):]

    start = 1 if channel.startswith("#") else 0
    for index in range(start, len(channel)):
        if channel[index] in "/?#":
            channel = channel[:index]
            break

    return channel.strip().lstrip("#").lower()


@dataclass
class ChatSettings:
    """
    The [Twitch] section, as the chat reader needs it.

    Field for field what TwitchConfig used to hold in the mod, because these
    are the streamer's settings and the file they live in did not change -
    only which process reads it.
    """

    enabled: bool = False
    channel: str = ""
    username: str = ""
    token: str = ""
    command: str = "!call"

    mods_only: bool = False
    subs_only: bool = False

    allow_chat: bool = True
    allow_subs: bool = False
    allow_bits: bool = False
    min_bits: int = 100
    allow_points: bool = False
    allow_donations: bool = False
    min_donation: float = 0.0

    default_event_text: str = "Hey, thanks for the support!"

    user_cooldown_seconds: int = 60
    global_cooldown_seconds: int = 0

    min_length: int = 2
    max_length: int = 300
    max_queue: int = 10
    max_age_seconds: int = 300

    use_viewer_name: bool = True

    def normalize(self) -> "ChatSettings":
        """
        Reconciles ModsOnly/SubsOnly with the Allow* flags, and refuses to end
        up with no way in.

        The old keys narrow rather than add: an ini written before the flags
        existed still restricts the channel it restricted, and every flag off
        would otherwise leave a reader that connects, joins, reads every
        message and answers none of them.
        """
        if self.mods_only:
            self.allow_chat = False
            self.allow_subs = False
        elif self.subs_only and self.allow_chat:
            self.allow_chat = False
            self.allow_subs = True

        open_flags = (self.allow_chat, self.allow_subs, self.allow_bits,
                      self.allow_points, self.allow_donations)
        if not any(open_flags) and not self.mods_only:
            self.allow_chat = True

        return self


def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def _get_bool(section: dict, key: str, default: bool) -> bool:
    raw = (section.get(key) or "").strip().lower()
    if raw in TRUE_WORDS:
        return True
    if raw in FALSE_WORDS:
        return False
    return default


def _get_int(section: dict, key: str, default: int, low: int, high: int) -> int:
    raw = (section.get(key) or "").strip()
    try:
        return int(_clamp(int(float(raw)), low, high))
    except ValueError:
        return default


def _get_float(section: dict, key: str, default: float, low: float, high: float) -> float:
    raw = (section.get(key) or "").strip()
    try:
        return float(_clamp(float(raw), low, high))
    except ValueError:
        return default


def settings_from(sections: dict) -> ChatSettings:
    """Builds settings from a parsed ini, with the mod's own defaults."""
    twitch = sections.get("twitch", {})

    return ChatSettings(
        enabled=_get_bool(twitch, "enabled", False),
        channel=clean_channel(twitch.get("channel", "")),
        username=(twitch.get("username") or "").strip(),
        token=(twitch.get("token") or "").strip(),
        command=(twitch.get("command") or "!call").strip(),
        mods_only=_get_bool(twitch, "modsonly", False),
        subs_only=_get_bool(twitch, "subsonly", False),
        allow_chat=_get_bool(twitch, "allowchat", True),
        allow_subs=_get_bool(twitch, "allowsubs", False),
        allow_bits=_get_bool(twitch, "allowbits", False),
        min_bits=_get_int(twitch, "minbits", 100, 1, 1000000),
        allow_points=_get_bool(twitch, "allowpoints", False),
        allow_donations=_get_bool(twitch, "allowdonations", False),
        min_donation=_get_float(twitch, "mindonation", 0.0, 0.0, 1e9),
        default_event_text=(twitch.get("defaulteventtext")
                            or "Hey, thanks for the support!").strip(),
        user_cooldown_seconds=_get_int(twitch, "usercooldownseconds", 60, 0, 3600),
        global_cooldown_seconds=_get_int(twitch, "globalcooldownseconds", 0, 0, 3600),
        min_length=_get_int(twitch, "minlength", 2, 1, 100),
        max_length=_get_int(twitch, "maxlength", 300, 10, 2000),
        max_queue=_get_int(twitch, "maxqueue", 10, 1, 200),
        max_age_seconds=_get_int(twitch, "maxageseconds", 300, 0, 86400),
        use_viewer_name=_get_bool(twitch, "useviewername", True),
    ).normalize()


def locate() -> Optional[Path]:
    r"""
    Finds the ini the game actually reads.

    CFT_INI names it outright. Otherwise it is scripts\CallFromTwitch.ini under
    the GTA install, whose path the installer left in gta_path.txt. The copy in
    this repository is the last resort and only useful when running from a
    checkout, because it is not the file the game loads.
    """
    override = os.environ.get("CFT_INI", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None

    pointer = BASE_DIR / "gta_path.txt"
    try:
        if pointer.is_file():
            root = Path(pointer.read_text(encoding="utf-8-sig").strip())
            candidate = root / "scripts" / "CallFromTwitch.ini"
            if candidate.is_file():
                return candidate
    except OSError:
        pass

    fallback = BASE_DIR.parent / "CallFromTwitch.ini"
    return fallback if fallback.is_file() else None


class SettingsFile:
    """
    The ini, reread whenever it changes on disk.

    Polling its mtime rather than watching the directory: one stat call every
    few seconds is cheaper than a filesystem watcher, and nothing here needs to
    react within a frame. A save the streamer makes mid-stream reaches the chat
    reader before they have finished alt-tabbing back.
    """

    def __init__(self, path: Optional[Path], poll_seconds: float = 5.0) -> None:
        self.path = path
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self._settings = ChatSettings().normalize()
        self._mtime = 0.0
        self._checked_at = 0.0
        # Called with the new settings after a reload changed them.
        self.on_change: Optional[Callable[[ChatSettings], None]] = None
        self.reload(force=True)

    @property
    def settings(self) -> ChatSettings:
        with self._lock:
            return self._settings

    def poll(self) -> None:
        """Rereads if the file changed, at most once every poll_seconds."""
        now = time.time()
        if now - self._checked_at < self._poll:
            return
        self._checked_at = now
        self.reload()

    def reload(self, force: bool = False) -> bool:
        """Rereads the file. True when the settings actually changed."""
        if self.path is None:
            return False

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False

        if not force and mtime == self._mtime:
            return False

        try:
            text = self.path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            log.warning("could not read %s: %s", self.path, exc)
            return False

        parsed = settings_from(parse_ini(text))
        self._mtime = mtime

        with self._lock:
            changed = parsed != self._settings
            self._settings = parsed

        if changed and not force:
            log.info("CallFromTwitch.ini changed; chat settings reloaded")
            listener = self.on_change
            if listener is not None:
                try:
                    listener(parsed)
                except Exception:
                    log.exception("settings listener failed")

        return changed
