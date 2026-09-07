r"""
Twitch chat, read here rather than inside the game.

Chat is the one Twitch surface that can be read anonymously: logging in as
justinfan<digits> with no password grants read access to any public channel,
so there is no app registration and no token to paste anywhere. A token is
still accepted, and is what a channel needs if it ever wants to talk back.

This used to live in the C# mod, which held the socket inside GTA's process.
That made chat a feature of the game being open: close the game and nobody was
listening, and a viewer who typed !call got silence rather than a queue. Now
the socket lives here, beside the socket that already carried redemptions and
donations, and every route into a call is the same shape - an event arrives, a
rule accepts it, and a call is synthesised whether or not anything is playing.

The mod, when it eventually starts, finds the calls already spoken and waiting
for it. Nothing about a message depends on the game: not receiving it, not the
cooldown it pays, not the queue it waits in.
"""
from __future__ import annotations

import logging
import random
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("cft.chat")
log.setLevel(logging.INFO)

HOST = "irc.chat.twitch.tv"
SSL_PORT = 6697

# Twitch PINGs on its own schedule and expects a prompt PONG; we also PING from
# our side, so a half-open socket is noticed rather than silently kept.
IDLE_BEFORE_PING = 4 * 60
IDLE_BEFORE_DEAD = 6 * 60
READ_TIMEOUT = 30.0

# Twitch's global cheermotes. A custom one needs an API call to know about, and
# a stray token read aloud beats eating a word the viewer meant to say.
CHEER_PREFIXES = {
    "cheer", "biblethump", "cheerwhal", "corgo", "uni", "showlove",
    "party", "seemsgood", "pride", "kappa", "frankerz", "heyguys",
    "dansgame", "elegiggle", "trihard", "kreygasm", "4head", "swiftrage",
    "notlikethis", "vohiyo", "pjsalt", "mrdestructoid", "bday", "ripcheer",
    "shamrock", "streamlabs", "muxy", "holidaycheer", "goal", "anon",
}

# U+E0000, and the BOM: Twitch appends the first to a message that duplicates
# the sender's previous one, to slip past its own duplicate filter.
INVISIBLE = re.compile("[\U000e0000﻿]")
# Control characters, including the \x01ACTION wrapper around a /me message.
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class ChatMessage:
    """One PRIVMSG, with the badges needed to decide who may use the command."""

    login: str
    display_name: str
    text: str
    is_moderator: bool = False
    is_subscriber: bool = False
    is_broadcaster: bool = False
    bits: int = 0


def parse_line(line: str) -> Optional[dict]:
    """
    One IRC line, split into tags, prefix, command, args and trailing.

    Hand-written for the same reason the mod had its own: this is the only
    wire format involved, and its grammar fits in twenty lines.
    """
    if not line:
        return None

    tags: dict = {}
    rest = line

    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for pair in raw_tags.split(";"):
            key, _, value = pair.partition("=")
            if key:
                tags[key] = _unescape_tag(value)

    prefix = ""
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")

    trailing = None
    at = rest.find(" :")
    if at >= 0:
        trailing = rest[at + 2:]
        rest = rest[:at]

    parts = rest.split()
    if not parts:
        return None

    return {
        "tags": tags,
        "prefix": prefix,
        "command": parts[0].upper(),
        "args": parts[1:],
        "trailing": trailing,
    }


def _unescape_tag(value: str) -> str:
    """IRCv3 tag escapes: \\s is a space, \\: a semicolon, and so on."""
    if "\\" not in value:
        return value

    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            index += 1
            nxt = value[index]
            out.append({"s": " ", ":": ";", "r": "\r", "n": "\n", "\\": "\\"}.get(nxt, nxt))
        else:
            out.append(char)
        index += 1
    return "".join(out)


def has_badge(tags: dict, badge: str) -> bool:
    """
    Whether one badge is present.

    badges reads "broadcaster/1,subscriber/12", so the name is matched on its
    own - a substring test would confuse one badge for another.
    """
    for entry in (tags.get("badges") or "").split(","):
        name, _, _version = entry.partition("/")
        if name.strip().lower() == badge:
            return True
    return False


def sanitize(text: str) -> str:
    """
    Folds a chat message into something a TTS engine can read aloud: plain
    text on one line, without control characters or Twitch's invisible
    duplicate-message suffix.
    """
    text = INVISIBLE.sub("", text or "")
    text = CONTROL.sub(" ", text)
    return " ".join(text.split())


def is_cheer_token(word: str) -> bool:
    """A cheermote is a known name followed by digits and nothing else."""
    stripped = word.rstrip("0123456789")
    if not stripped or stripped == word:
        # No trailing number, or nothing but one: an ordinary word, or a
        # figure the viewer meant to say.
        return False
    return stripped.lower() in CHEER_PREFIXES


def strip_cheer_tokens(text: str) -> str:
    """
    Removes the cheermote words Twitch leaves in the message text.

    A cheer arrives as "Cheer100 good luck out there", with the amount in a tag
    and the word still in the sentence, where read aloud it becomes "cheer one
    hundred good luck out there".
    """
    return " ".join(word for word in text.split() if not is_cheer_token(word))


def trim_to_length(text: str, limit: int) -> str:
    """
    Cuts an over-long line at a word boundary, so it ends on a whole word
    rather than mid-syllable - the difference is audible.
    """
    if len(text) <= limit:
        return text

    cut = text[:limit]
    space = cut.rfind(" ")
    # Unless the boundary throws away most of the allowance: one very long word
    # should still be spoken.
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip()


class ChatRules:
    """
    Who may ask for a call, and what their line becomes.

    Lifted from TwitchSource in the mod, cooldown bookkeeping included. It
    keeps its own clock and its own map of who called when, so none of it
    depends on a game being open - which is the entire reason it moved.
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._last_use: dict = {}
        self._last_global = 0.0

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value) -> None:
        # Cooldowns already served stay served: an ini edit should not hand
        # everyone in chat a fresh turn.
        self._settings = value

    def allows(self, message: ChatMessage) -> "tuple[bool, bool, bool]":
        """
        Returns (allowed, paid, exempt).

        The ways in are additive - a message qualifies if any enabled one
        accepts it - so a channel can let subscribers call for free while
        everyone else cheers. Bits are read off the IRC tag, so cheering needs
        no token at all.
        """
        config = self._settings

        # Never gated: they are the ones testing the thing, and a streamer
        # cannot cheer at their own channel.
        if message.is_broadcaster or message.is_moderator:
            return True, False, True

        if config.allow_bits and message.bits >= config.min_bits:
            # Cheering is a payment, so it buys past the cooldown.
            return True, True, False

        if config.allow_chat:
            return True, False, False

        if config.allow_subs and message.is_subscriber:
            return True, False, False

        return False, False, False

    def extract_command(self, text: str) -> Optional[str]:
        """
        The text after the command word, or None when this is not a command
        for us. Matching is case-insensitive and the command must be followed
        by whitespace, so "!called it" is not a call.
        """
        trimmed = (text or "").strip()
        command = self._settings.command

        if not command:
            return trimmed  # No command configured: every message counts.

        if len(trimmed) <= len(command) or not trimmed.lower().startswith(command.lower()):
            return None

        if trimmed[len(command)] not in " \t":
            return None

        return trimmed[len(command) + 1:].strip()

    def strip_command(self, text: str) -> str:
        """
        The text of a message that has already earned its call, with the
        command word removed if present. Unlike extract_command this never
        rejects: the right to speak was settled before it was called.
        """
        trimmed = (text or "").strip()
        command = self._settings.command

        if not command or not trimmed.lower().startswith(command.lower()):
            return trimmed

        if len(trimmed) == len(command):
            return ""

        if trimmed[len(command)] not in " \t":
            return trimmed

        return trimmed[len(command) + 1:].strip()

    def spoken_text(self, message: ChatMessage, paid: bool) -> Optional[str]:
        """The line to speak, or None when this message does not become one."""
        config = self._settings

        raw = self.strip_command(message.text) if paid else self.extract_command(message.text)
        if raw is None:
            log.debug("drop %s: not a %r command", message.login, config.command)
            return None

        spoken = sanitize(raw)
        if paid:
            spoken = strip_cheer_tokens(spoken)
            # A bare "Cheer100" leaves nothing to say, but the viewer paid for
            # a call, so it falls back to the configured text.
            if len(spoken) < config.min_length:
                spoken = config.default_event_text

        if len(spoken) < config.min_length:
            log.info("drop %s: %d chars, MinLength is %d",
                     message.login, len(spoken), config.min_length)
            return None

        return trim_to_length(spoken, config.max_length)

    def passes_cooldown(self, login: str) -> bool:
        config = self._settings
        now = time.time()

        with self._lock:
            if config.global_cooldown_seconds > 0 and \
                    now - self._last_global < config.global_cooldown_seconds:
                return False

            if config.user_cooldown_seconds > 0:
                last = self._last_use.get(login.lower())
                if last is not None and now - last < config.user_cooldown_seconds:
                    return False

        return True

    def mark_used(self, login: str) -> None:
        config = self._settings
        now = time.time()

        with self._lock:
            self._last_global = now
            if config.user_cooldown_seconds <= 0:
                return

            self._last_use[login.lower()] = now

            # The map would otherwise hold every viewer who ever called. An
            # entry past its cooldown can no longer block anyone.
            if len(self._last_use) > 512:
                cutoff = now - config.user_cooldown_seconds
                self._last_use = {name: stamp for name, stamp in self._last_use.items()
                                  if stamp >= cutoff}


@dataclass
class ChatStats:
    """What the streamer can ask about a chat reader that looks idle."""

    connected: bool = False
    channel: str = ""
    anonymous: bool = True
    messages_seen: int = 0
    calls_queued: int = 0
    dropped: int = 0
    last_error: str = ""
    connected_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "channel": self.channel,
            "anonymous": self.anonymous,
            "messages_seen": self.messages_seen,
            "calls_queued": self.calls_queued,
            "chat_dropped": self.dropped,
            "last_error": self.last_error,
            "uptime": round(time.time() - self.connected_at, 1) if self.connected_at else 0.0,
        }


class TwitchChat:
    """
    A minimal TMI client: connect, join one channel, hand every PRIVMSG on.

    A blocking socket on a thread of its own, on purpose. The reader is parked
    in recv almost all of the time, which is cheaper to reason about than an
    async state machine, and it costs one thread in a process that is already
    holding a GPU model.

    Reconnects are expected rather than exceptional - Twitch restarts its edges
    routinely - so the loop treats a dropped link as normal and backs off only
    to keep a doomed channel name from hammering the service.
    """

    def __init__(self, rules: ChatRules,
                 on_call: Callable[[str, str, str], bool]) -> None:
        self._rules = rules
        # Called with (text, user, kind); returns False when the call store
        # refused the line, which is what a full queue looks like from here.
        self._on_call = on_call

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._socket: Optional[socket.socket] = None
        self._recv_buffer = b""
        self._write_lock = threading.Lock()

        self._last_traffic = 0.0
        self._last_ping_sent = 0.0

        self._stats_lock = threading.Lock()
        self._stats = ChatStats()
        # The channel and nick this connection actually used, so a settings
        # change can be noticed and reconnected for.
        self._live_channel = ""

    @property
    def configured(self) -> bool:
        settings = self._rules.settings
        return bool(settings.enabled and settings.channel)

    def stats(self) -> dict:
        with self._stats_lock:
            return self._stats.as_dict()

    def start(self) -> None:
        if self._thread is not None:
            return

        self._thread = threading.Thread(target=self._run, name="cft.chat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def settings_changed(self) -> None:
        """
        Reconnects when the ini names a different channel or login.

        Everything else - cooldowns, lengths, who may call - is read per
        message and needs nothing here. Only the connection itself is bound to
        what was configured when it opened.
        """
        settings = self._rules.settings
        if settings.enabled and settings.channel and settings.channel != self._live_channel:
            log.info("chat: channel changed to #%s; reconnecting", settings.channel)
            self._close()
        elif not settings.enabled and self._live_channel:
            log.info("chat: disabled in the ini; disconnecting")
            self._close()

    # ---- reader thread from here down ----

    def _run(self) -> None:
        attempt = 0

        while not self._stop.is_set():
            if not self.configured:
                # Not an error: a streamer may run the server for donations
                # alone, and the ini can turn chat on later without a restart.
                self._sleep(5.0)
                continue

            try:
                self._connect()
                attempt = 0
                self._read_until_closed()
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("chat disconnected: %s", exc)
                    self._note_disconnect(str(exc))
            finally:
                self._close()

            if self._stop.is_set():
                break

            # So a channel that will never work - a typo, a banned account -
            # does not hammer Twitch once a second forever.
            attempt += 1
            self._sleep(min(30.0, 2.0 ** min(attempt, 5)))

    def _connect(self) -> None:
        settings = self._rules.settings
        channel = settings.channel
        token = settings.token.strip()
        username = settings.username.strip().lower()

        # An anonymous login must be justinfan followed by digits; Twitch
        # rejects any other nick that arrives without a password.
        nick = username if (token and username) else "justinfan%d" % random.randint(10000, 99999)

        log.info("chat: connecting to #%s as %s", channel, nick)

        raw = socket.create_connection((HOST, SSL_PORT), timeout=15)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw, server_hostname=HOST)
        sock.settimeout(READ_TIMEOUT)

        self._socket = sock
        self._recv_buffer = b""
        self._live_channel = channel

        # tags carries display-name and the mod/subscriber badges; commands
        # carries RECONNECT. membership is join/part spam.
        self._send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send("PASS " + (_normalize_token(token) if token else "SCHMOOPIIE"))
        self._send("NICK " + nick)
        self._send("JOIN #" + channel)

        now = time.time()
        self._last_traffic = now
        self._last_ping_sent = 0.0

        with self._stats_lock:
            self._stats.connected = True
            self._stats.channel = channel
            self._stats.anonymous = not token
            self._stats.connected_at = now
            self._stats.last_error = ""

        log.info("chat: listening on #%s%s", channel, " (anonymous)" if not token else "")

    def _read_until_closed(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._socket.recv(8192)
            except socket.timeout:
                # A read timeout is not a dead link on its own.
                if time.time() - self._last_traffic > IDLE_BEFORE_DEAD:
                    raise IOError("no traffic from Twitch, link is dead")
                self._keep_alive()
                continue

            if not chunk:
                raise IOError("connection closed by Twitch")

            self._last_traffic = time.time()
            self._recv_buffer += chunk

            while b"\r\n" in self._recv_buffer:
                raw, _, self._recv_buffer = self._recv_buffer.partition(b"\r\n")
                self._handle_line(raw.decode("utf-8", errors="replace"))

            # A peer that sends megabytes without a newline is not Twitch.
            if len(self._recv_buffer) > 1 << 20:
                raise IOError("oversized line from Twitch")

            # Settings may have changed the channel out from under us.
            if self._rules.settings.channel != self._live_channel:
                raise IOError("channel changed in the ini")

    def _keep_alive(self) -> None:
        now = time.time()
        if now - self._last_traffic < IDLE_BEFORE_PING:
            return
        # One outstanding ping at a time; the reply refreshes _last_traffic.
        if now - self._last_ping_sent < IDLE_BEFORE_PING:
            return

        self._last_ping_sent = now
        self._send("PING :" + HOST)

    def _handle_line(self, line: str) -> None:
        if not line:
            return

        # An unanswered PING is a disconnect within seconds, so it comes first.
        if line.startswith("PING"):
            self._send("PONG" + line[4:])
            return

        parsed = parse_line(line)
        if parsed is None:
            return

        command = parsed["command"]

        if command == "PRIVMSG":
            self._handle_privmsg(parsed)
            return

        if command == "RECONNECT":
            # Twitch is about to drop this edge; tearing the socket down
            # ourselves reconnects on our own schedule.
            raise IOError("Twitch asked us to reconnect")

        if command == "NOTICE":
            trailing = parsed["trailing"] or ""
            # A bad token never becomes a JOIN: it says so and closes.
            if "authentication failed" in trailing.lower():
                raise IOError("Twitch rejected the token: " + trailing)

    def _handle_privmsg(self, parsed: dict) -> None:
        trailing = parsed["trailing"]
        if trailing is None:
            return

        login = parsed["prefix"].partition("!")[0]
        if not login:
            return

        tags = parsed["tags"]
        broadcaster = has_badge(tags, "broadcaster")

        message = ChatMessage(
            login=login,
            # display-name carries the capitalisation and any non-Latin
            # spelling the viewer chose; the login is the ASCII fallback.
            display_name=tags.get("display-name") or login,
            text=trailing,
            is_moderator=tags.get("mod") == "1" or broadcaster,
            is_subscriber=tags.get("subscriber") == "1",
            is_broadcaster=broadcaster,
            bits=_parse_int(tags.get("bits")),
        )

        with self._stats_lock:
            self._stats.messages_seen += 1

        self._consider(message)

    def _consider(self, message: ChatMessage) -> None:
        """One message, from arrival to a queued call or a logged reason not to."""
        allowed, paid, exempt = self._rules.allows(message)
        if not allowed:
            log.debug("drop %s: not allowed by the ini", message.login)
            return

        spoken = self._rules.spoken_text(message, paid)
        if spoken is None:
            return

        # Cheers buy past the cooldown, and so do the broadcaster and their
        # mods: allows() already waved those through.
        if not paid and not exempt and not self._rules.passes_cooldown(message.login):
            log.info("drop %s: still on cooldown", message.login)
            return

        # A call that never paid the cooldown must not start one either, or a
        # mod testing the mod holds up all of chat.
        if not exempt:
            self._rules.mark_used(message.login)

        kind = "bits" if paid else "chat"
        if not self._on_call(spoken, message.display_name, kind):
            with self._stats_lock:
                self._stats.dropped += 1
            return

        with self._stats_lock:
            self._stats.calls_queued += 1

        log.info("chat: queued from %s (%s): %s", message.display_name, kind, spoken[:60])

    def _send(self, line: str) -> None:
        with self._write_lock:
            sock = self._socket
            if sock is None:
                return
            try:
                sock.sendall((line + "\r\n").encode("utf-8"))
            except OSError:
                # The socket died under us; the reader is about to notice.
                pass

    def _note_disconnect(self, reason: str) -> None:
        with self._stats_lock:
            self._stats.connected = False
            self._stats.last_error = reason
            self._stats.connected_at = 0.0

    def _close(self) -> None:
        with self._write_lock:
            sock = self._socket
            self._socket = None

        self._live_channel = ""
        with self._stats_lock:
            self._stats.connected = False
            self._stats.connected_at = 0.0

        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _sleep(self, seconds: float) -> None:
        """Interruptible, so stop() during a backoff is not a two-second wait."""
        self._stop.wait(seconds)


def _normalize_token(token: str) -> str:
    """
    Accepts a token with or without the "oauth:" prefix the IRC gateway wants:
    the docs and most token generators disagree about whether it is part of the
    token, and pasting either should work.
    """
    return token if token.lower().startswith("oauth:") else "oauth:" + token


def _parse_int(value: Optional[str]) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
