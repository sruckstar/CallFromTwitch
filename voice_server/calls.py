"""
Calls that are already spoken and waiting for the game to take them.

The game is the reason this exists. SHVDN gives a script nothing but its tick,
and the tick stops dead in the pause menu - no timers, no callbacks, no
continuations. A mod that synthesised its own audio therefore did nothing at
all while the player sat in a menu: the request was never sent, and the reply
was never collected. The first thing the player heard after unpausing was a
minute of silence while synthesis finally ran.

So synthesis happens here instead, on a thread of our own that a paused game
cannot stop, and the mod's tick is left with the one job it can still do in a
single frame: ask whether something is ready, take the bytes, and say so.

Handing the audio over is a two-step: the mod GETs the call, plays it, and
POSTs an ack naming it. Nothing is deleted until that ack arrives, because the
game can vanish between the two - a crash, an alt-F4, or a script reload - and
a call dropped on delivery is a redemption the viewer paid for and never got.
An unacked call is offered again on the next poll, which is what "keep telling
the mod until it confirms" comes down to.
"""
from __future__ import annotations

import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

log = logging.getLogger("cft.calls")
log.setLevel(logging.INFO)


@dataclass
class Call:
    """One line, from the text that asked for it to the WAV that answers it."""

    id: str
    text: str
    user: str
    kind: str
    # Filled in by the worker once synthesis finishes.
    audio: Optional[bytes] = None
    voice: str = ""
    created_at: float = field(default_factory=time.time)
    ready_at: float = 0.0
    # When the mod was last offered this call. Advisory only: the offer
    # repeats regardless, and this exists so the log can say how long for.
    offered_at: float = 0.0
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.audio is not None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "user": self.user,
            "kind": self.kind,
            "voice": self.voice,
            "bytes": len(self.audio) if self.audio else 0,
            "age": round(time.time() - self.created_at, 1),
            "waiting": round(time.time() - self.ready_at, 1) if self.ready_at else 0.0,
        }


class CallStore:
    """
    The synthesis queue and the delivery slot, behind one lock.

    Two threads meet here: the worker that renders audio, and the HTTP
    handlers the mod calls. Everything shared is guarded, and nothing holds
    the lock for long - the rendering itself happens outside it.
    """

    def __init__(self, max_pending: int = 8, max_age: float = 900.0) -> None:
        self._lock = threading.Lock()
        # Woken when work arrives, so the worker sleeps instead of polling.
        self._wake = threading.Condition(self._lock)
        self._waiting: list[Call] = []      # queued for synthesis
        self._ready: list[Call] = []        # spoken, waiting for the game
        self._ids = itertools.count(1)
        self._max_pending = max_pending
        self._max_age = max_age
        self._dropped = 0

    def submit(self, text: str, user: str, kind: str) -> Optional[Call]:
        """
        Accepts a line for synthesis. Returns the call, or None when the
        backlog is full - the caller decides whether that is worth reporting.
        """
        with self._lock:
            self._expire_locked()
            if len(self._waiting) + len(self._ready) >= self._max_pending:
                self._dropped += 1
                log.warning("call queue full (%d); dropping %r from %s",
                            self._max_pending, text[:40], user)
                return None

            call = Call(id="c%d" % next(self._ids), text=text, user=user, kind=kind)
            self._waiting.append(call)
            self._wake.notify()
            return call

    def take_for_synthesis(self, timeout: float = 1.0) -> Optional[Call]:
        """Blocks until there is something to speak, or the timeout passes."""
        with self._lock:
            if not self._waiting:
                self._wake.wait(timeout)
            if not self._waiting:
                return None
            return self._waiting.pop(0)

    def finish(self, call: Call, audio: bytes, voice: str) -> None:
        """Files a spoken call for delivery."""
        with self._lock:
            call.audio = audio
            call.voice = voice
            call.ready_at = time.time()
            self._ready.append(call)
        log.info("ready: %s %r (%d bytes) - waiting for the game",
                 call.id, call.text[:40], len(audio))

    def fail(self, call: Call, error: str) -> None:
        """
        Drops a call that could not be spoken.

        Never offered to the mod: there is no audio to play, and the server
        window already says more about the failure than a game notification
        could.
        """
        call.error = error
        log.error("call %s failed: %s", call.id, error)

    def peek(self) -> Optional[Call]:
        """
        The call the game should be ringing, without removing it.

        The same call comes back every time until it is acked. That is the
        point: the mod can be told a hundred times and act on it once.
        """
        with self._lock:
            self._expire_locked()
            if not self._ready:
                return None
            call = self._ready[0]
            call.offered_at = time.time()
            return call

    def ack(self, call_id: str) -> bool:
        """
        The game says it has the audio. False for an id we do not hold, which
        is what a duplicate ack from a retrying mod looks like.
        """
        with self._lock:
            for index, call in enumerate(self._ready):
                if call.id == call_id:
                    del self._ready[index]
                    log.info("delivered: %s (held %.1fs)", call_id,
                             time.time() - call.ready_at)
                    return True
        return False

    def _expire_locked(self) -> None:
        """
        Throws away calls nobody came for.

        The cap is generous - a pause menu is a legitimate reason to hold a
        call for minutes - but not infinite: a server left running overnight
        should not greet the next session with yesterday's redemptions.
        """
        if self._max_age <= 0:
            return

        now = time.time()
        for bucket in (self._waiting, self._ready):
            fresh = [call for call in bucket if now - call.created_at <= self._max_age]
            if len(fresh) != len(bucket):
                self._dropped += len(bucket) - len(fresh)
                log.info("dropped %d call(s) older than %.0fs",
                         len(bucket) - len(fresh), self._max_age)
                bucket[:] = fresh

    def configure(self, max_pending: int, max_age: float) -> None:
        """
        Takes new limits from a reloaded ini.

        Applied to what is already queued as well as to what arrives next: a
        streamer who just cut MaxQueue did it because the backlog is too long
        right now, and leaving the existing one alone would answer the wrong
        question. Nothing is dropped here, though - the next submit and the
        next expiry sweep do that, in the order they already use.
        """
        with self._lock:
            self._max_pending = max_pending
            self._max_age = max_age

    def stats(self) -> dict:
        with self._lock:
            return {
                "synthesizing": len(self._waiting),
                "ready": len(self._ready),
                "calls_dropped": self._dropped,
            }


@dataclass
class Rules:
    """
    Which paid events become calls, and how their text is trimmed.

    These belong to the streamer, and the streamer edits CallFromTwitch.ini -
    so the mod sends them up at startup rather than the server growing a
    second copy of the same settings. Until it does, the defaults here let a
    call through, because a server that silently swallowed every redemption
    would look exactly like a broken one.
    """

    allow_points: bool = True
    allow_donations: bool = True
    allow_bits: bool = True
    min_donation: float = 0.0
    min_bits: int = 1
    min_length: int = 2
    max_length: int = 300
    # Spoken when a redemption or donation carries no message of its own.
    default_text: str = "Hey! Someone just sent you a call."

    def accepts(self, kind: str, amount: float) -> bool:
        if kind == "points":
            return self.allow_points
        if kind == "donation":
            return self.allow_donations and amount >= self.min_donation
        if kind == "bits":
            return self.allow_bits and amount >= self.min_bits
        return False

    def text_for(self, text: str) -> Optional[str]:
        """
        The line to speak, or None when there is nothing to say.

        A viewer who paid and wrote nothing still gets a call: they bought the
        phone ringing, not the sentence.
        """
        text = (text or "").strip()
        if len(text) < self.min_length:
            text = self.default_text.strip()
            if not text:
                return None
        return text[: self.max_length]


class CallWorker:
    """
    The thread that turns queued text into audio.

    One at a time on purpose: RVC holds a GPU model, and two conversions at
    once are slower than the same two in a row, on top of risking the memory.
    """

    def __init__(self, store: CallStore,
                 synthesize: Callable[[str], "tuple[bytes, str]"]) -> None:
        self._store = store
        self._synthesize = synthesize
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cft.calls", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            call = self._store.take_for_synthesis()
            if call is None:
                continue
            try:
                audio, voice = self._synthesize(call.text)
                self._store.finish(call, audio, voice)
            except Exception as exc:
                # The worker outlives any single bad line; raising here would
                # end synthesis for the rest of the session.
                log.exception("synthesis failed")
                self._store.fail(call, str(exc))

    def stop(self) -> None:
        self._stop.set()
