"""
CallFromTwitch voice server.

Piper TTS renders neutral speech, RVC converts the timbre to the chosen
character, and a telephone filter colours the result. Models stay resident.

Every way a viewer can ask for a call arrives here, on this process's own
sockets: chat over IRC (chat.py), redemptions over EventSub and donations over
Centrifugo (events.py). None of it depends on the game running. A message that
arrives with GTA closed is filtered, synthesised and held, and the mod finds it
waiting whenever it starts.

The mod neither synthesises nor waits on a response, because a paused game runs
no script code at all - see calls.py. It asks /call whether something is ready;
the audio it takes is kept here until /call/ack says it arrived.

  GET  /call                                     -> the ready call, or none
  GET  /call/<id>/audio                          -> its WAV bytes
  POST /call/ack  {"id": "c7"}                   -> the game has it; drop it
  POST /speak     {"text": "..."}                -> audio/wav, synchronous
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

import calls as calls_module
import chat as chat_module
import events as events_module
import mod_ini
import phone_fx
from config import Config
from tts_engine import TTSEngine
from rvc_engine import RVCEngine

config = Config()

logging.basicConfig(
    level=logging.WARNING if config.quiet else logging.INFO,
    format="%(message)s" if config.quiet else "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cft.server")
# Status lines stay visible even when everything else is turned down.
log.setLevel(logging.INFO)

if config.quiet:
    for _noisy in ("uvicorn", "uvicorn.error", "uvicorn.access",
                   "fairseq", "numba", "matplotlib"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

tts: Optional[TTSEngine] = None
rvc: Optional[RVCEngine] = None
phone: Optional[phone_fx.PhoneFX] = None
# The voice every call is spoken in, settled once at startup.
active_voice: str = config.default_voice
# Always present; it simply starts nothing when unconfigured.
hub: events_module.EventHub = events_module.EventHub(config)
# Spoken calls waiting for a game that may be sitting in its pause menu. The
# limits below are the fallback for a missing ini; MaxQueue and MaxAgeSeconds
# replace them as soon as one is found.
store: calls_module.CallStore = calls_module.CallStore(
    max_pending=config.call_max_pending, max_age=config.call_max_age
)
worker: Optional[calls_module.CallWorker] = None


def queue_chat_call(text: str, user: str, kind: str) -> bool:
    """
    A chat line that passed every rule, on its way to being spoken.

    Returns False when the store refused it, which is what a full queue looks
    like from the reader thread - it counts those so /health can say so.
    """
    return store.submit(text, user or "Viewer", kind) is not None


def rules_from(settings) -> calls_module.Rules:
    """The paid-event thresholds, as the streamer wrote them in the ini."""
    return calls_module.Rules(
        allow_points=settings.allow_points,
        allow_donations=settings.allow_donations,
        allow_bits=settings.allow_bits,
        min_donation=settings.min_donation,
        min_bits=settings.min_bits,
        min_length=settings.min_length,
        max_length=settings.max_length,
        default_text=settings.default_event_text or calls_module.Rules.default_text,
    )


# CallFromTwitch.ini, read from wherever the game reads it. The streamer edits
# one file; both halves obey it. The mod no longer sends its settings up,
# because chat has to work with the game closed and a closed game pushes
# nothing.
settings_file: mod_ini.SettingsFile = mod_ini.SettingsFile(mod_ini.locate())
# Which paid events become calls, derived from that ini.
rules: calls_module.Rules = rules_from(settings_file.settings)
# Chat rules and the socket that feeds them. Both read the same settings.
chat_rules: chat_module.ChatRules = chat_module.ChatRules(settings_file.settings)
chat_client: chat_module.TwitchChat = chat_module.TwitchChat(chat_rules, queue_chat_call)


def set_window_title(text: str) -> None:
    """Rename the console window, so an alt-tabbed player can see the state."""
    if not sys.stdout or not sys.stdout.isatty():
        return
    try:
        # OSC 0: ESC ] 0 ; <title> BEL
        sys.stdout.write("]0;" + text + "")
        sys.stdout.flush()
    except Exception:
        pass


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1)
    # The mod never sends this: the voice is chosen when the server starts, and
    # a name coming in per request used to override that choice silently.
    # Kept for test_client.py, which needs to audition one model on purpose.
    voice: Optional[str] = None


class AckRequest(BaseModel):
    """The game confirming it has the audio for one call."""

    id: str = Field(..., min_length=1)


class TestEvent(BaseModel):
    """A synthetic redemption or donation, for POST /events/test."""

    kind: str = Field("points", pattern="^(points|donation|bits)$")
    user: str = "TestViewer"
    text: str = ""
    amount: float = 0.0
    currency: str = ""
    reward: str = ""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    yield


app = FastAPI(title="CallFromTwitch Voice Server", version="0.1.0", lifespan=lifespan)


def startup() -> None:
    global tts, rvc, phone
    try:
        tts = TTSEngine(
            config.piper_model,
            config.piper_length_scale,
            config.piper_cuda,
            config.piper_sentence_silence,
            config.piper_comma_pause,
        )
    except FileNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)

    if config.rvc_enabled:
        rvc = RVCEngine(config.rvc_models_dir, config.rvc_device, **config.rvc_params())
        voices = rvc.available_voices()
        if voices:
            if not config.quiet:
                log.info("RVC voices available: %s", ", ".join(voices))
            select_voice(voices)
        else:
            log.warning(
                "No RVC models in %s - speech will play in the raw Piper voice. "
                "Drop <name>.pth (or a <Character>/ folder containing one) there.",
                config.rvc_models_dir,
            )
    else:
        if not config.quiet:
            log.info("RVC disabled; serving raw Piper output.")

    if config.phone_enabled:
        phone = phone_fx.with_overrides(
            phone_fx.preset(config.phone_preset), config.phone_overrides()
        )
        if not config.quiet:
            log.info(
                "Phone effect '%s': %g-%g Hz, drive %.1f, %.0f:1 comp, codec %s",
                config.phone_preset, phone.low_hz, phone.high_hz, phone.drive,
                phone.compress_ratio,
                f"{phone.codec_rate} Hz" if phone.codec_rate else "off",
            )
    else:
        if not config.quiet:
            log.info("Phone effect disabled; serving the dry voice.")

    if config.warmup:
        warmup()

    hub.start()

    global worker
    worker = calls_module.CallWorker(store, render)
    worker.start()

    # Paid events no longer wait for the mod to come and get them: the game
    # may be paused, and then nobody would come for minutes. They are turned
    # into calls here, the moment the socket delivers them.
    hub.on_event = queue_event

    # An ini saved mid-stream reaches both readers without a restart. Watched
    # on a thread of our own rather than off an incoming request: the game may
    # be closed for hours, and chat still has to obey what the streamer saved.
    settings_file.on_change = apply_settings
    if settings_file.path is not None:
        apply_queue_limits(settings_file.settings)
    _watch_settings()
    _report_settings_source()

    # Chat, on this process's socket. Started even when the ini has it off:
    # the thread costs nothing while idle, and turning Enabled on is then a
    # save away rather than a server restart.
    chat_client.start()

    _print_ready()


def _watch_settings() -> None:
    """Rechecks the ini every few seconds, forever, on a daemon thread."""
    def loop() -> None:
        while True:
            time.sleep(5.0)
            try:
                settings_file.reload()
            except Exception:
                log.exception("settings watch failed")

    threading.Thread(target=loop, name="cft.ini", daemon=True).start()


def _report_settings_source() -> None:
    """Says which ini won, because the copy in the repo is rarely the one."""
    settings = settings_file.settings
    if settings_file.path is None:
        log.warning("No CallFromTwitch.ini found - chat is off and paid events use "
                    "defaults. Point CFT_INI at the file in GTA V\scripts\.")
        return

    log.info("settings: %s", settings_file.path)
    if settings.enabled and settings.channel:
        log.info("chat: #%s, command %r, cooldown %ds/user",
                 settings.channel, settings.command, settings.user_cooldown_seconds)
    elif settings.enabled:
        log.warning("chat: [Twitch] Channel is empty - write just the channel name; "
                    "a full https:// link does not survive the ini parser")
    else:
        log.info("chat: off ([Twitch] Enabled = false)")


def apply_settings(settings) -> None:
    """
    Takes a reloaded ini. Called from whichever thread noticed the change.

    Both readers are pointed at the new values, and the chat socket decides
    for itself whether anything it is bound to actually moved.
    """
    global rules
    rules = rules_from(settings)
    chat_rules.settings = settings
    apply_queue_limits(settings)
    chat_client.settings_changed()


def apply_queue_limits(settings) -> None:
    """
    Points the call store at the streamer's MaxQueue and MaxAgeSeconds.

    Both used to be enforced in the mod, on a queue that lived there. The queue
    moved here, so the limits had to follow, or a channel that had asked for
    three waiting calls would silently hold eight. MaxAgeSeconds of 0 means
    "keep everything", which the store spells as a non-positive max age.
    """
    max_age = float(settings.max_age_seconds) if settings.max_age_seconds > 0 else 0.0
    store.configure(settings.max_queue, max_age)


def _print_ready() -> None:
    """The status block the player checks before alt-tabbing into the game."""
    set_window_title(f"CallFromTwitch - {active_voice} - port {config.port}")
    if not config.quiet:
        log.info("Ready on http://%s:%s (voice: %s)",
                 config.host, config.port, active_voice)
        return

    voices = rvc.available_voices() if rvc else []
    effects = config.phone_preset if phone else "off"
    settings = settings_file.settings
    if settings.enabled and settings.channel:
        listening = f"#{settings.channel}  ({settings.command})"
    elif settings.enabled:
        listening = "no channel set in CallFromTwitch.ini"
    else:
        listening = "off"

    print()
    print(f"  Listening on  http://{config.host}:{config.port}")
    print(f"  Twitch chat   {listening}")
    print(f"  Voice         {active_voice}" + (f"   (of {len(voices)} installed)" if len(voices) > 1 else ""))
    print(f"  Phone effect  {effects}")
    print()
    # Said plainly, because it is the change: chat used to need the game.
    print("  Ready - chat is being read now; GTA V can start whenever you like.")
    print("  Keep this window open; Ctrl+C stops the server.")
    print()


def select_voice(voices: list[str]) -> None:
    """
    Ask which of the installed voices this run speaks in.

    The answer settles the voice for the whole run: it is what warmup loads
    and what every call is spoken in. Skipped when there is one voice, when
    CFT_VOICE_PROMPT is off, or when there is no console to answer on, so
    unattended starts never hang - those fall back to CFT_DEFAULT_VOICE.
    """
    global active_voice

    preferred = rvc.canonical(active_voice)
    if len(voices) == 1:
        active_voice = voices[0]
        return
    if not config.voice_prompt or not sys.stdin or not sys.stdin.isatty():
        if preferred:
            active_voice = preferred
        else:
            active_voice = voices[0]
            log.info("Default voice '%s' not installed; using '%s'.",
                     config.default_voice, active_voice)
        return

    default_index = voices.index(preferred) + 1 if preferred else 1
    print("\nAvailable RVC voices:", flush=True)
    for number, voice in enumerate(voices, start=1):
        mark = "  (default)" if number == default_index else ""
        print(f"  {number}. {voice}{mark}", flush=True)

    while True:
        try:
            answer = input(f"Choose a voice [1-{len(voices)}, Enter = {voices[default_index - 1]}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            answer = ""
        if not answer:
            active_voice = voices[default_index - 1]
            break
        if answer.isdigit() and 1 <= int(answer) <= len(voices):
            active_voice = voices[int(answer) - 1]
            break
        named = rvc.canonical(answer)
        if named:
            active_voice = named
            break
        print("  Not a listed voice - enter a number or the name.", flush=True)

    print(f"Using voice: {active_voice}\n", flush=True)
    log.info("Active voice: %s", active_voice)


def warmup() -> None:
    """
    Load every lazy model before the first request instead of during it.

    Piper defers its onnx session and RVC its hubert and rmvpe checkpoints;
    together that is over a minute, which would trip the mod's HTTP timeout.
    """
    started = time.perf_counter()
    set_window_title(f"CallFromTwitch - loading {active_voice}...")
    if config.quiet:
        print("  Loading models (30-60s on first run)... ", end="", flush=True)
    else:
        log.info("Warming up models (first call would otherwise pay this cost)...")
    try:
        sample = tts.synthesize("Warming up.")
    except Exception:
        log.exception("TTS warmup failed; the first call will be slow")
        return

    if rvc is not None:
        rvc.warmup(active_voice)

    del sample
    elapsed = time.perf_counter() - started
    if config.quiet:
        print(f"done in {elapsed:.0f}s", flush=True)
    else:
        log.info("Warmup done in %.1fs", elapsed)


@app.get("/health")
def health() -> dict:
    """Whether the server is up, and what it is hearing."""
    return {
        "status": "ok",
        "tts": tts is not None,
        "rvc": rvc is not None,
        "phone": config.phone_preset if phone else None,
        "voices": rvc.available_voices() if rvc else [],
        "events": hub.sources,
        "chat": chat_client.stats(),
        **hub.queue.stats(),
        **store.stats(),
    }


@app.get("/events")
def take_events(limit: int = 10) -> dict:
    """
    Hands the mod whatever paid events have arrived since it last asked.

    Draining rather than peeking: there is exactly one consumer, and it must
    never see the same redemption twice.
    """
    taken = hub.queue.drain(max(1, min(limit, 50)))
    return {"events": [event.as_dict() for event in taken], "sources": hub.sources}


@app.post("/events/test")
def test_event(request: TestEvent) -> dict:
    """
    Injects a fake redemption or donation into the real queue.

    Nobody can donate to themselves or buy their own channel points, so this
    is the only way to rehearse the paid paths before a viewer pays for one.
    """
    if not config.events_test:
        raise HTTPException(status_code=403, detail="test events are disabled (CFT_EVENTS_TEST=0)")

    event = hub.post_test(
        request.kind, request.user, request.text,
        request.amount, request.currency, request.reward,
    )
    return {"queued": event.as_dict(), **hub.queue.stats()}


@app.get("/voices")
def voices() -> dict:
    return {
        "voices": rvc.available_voices() if rvc else [],
        "active": active_voice,
        # Old name for the same thing, kept so an existing caller still reads it.
        "default": active_voice,
    }


def _log_received(text: str, voice: str) -> None:
    """
    Says a line came in, matching the one-line-per-call shape of the result.

    "->" is a request starting; the timing in parentheses is one that
    finished. A request that never gets its partner line is the one that broke.
    """
    shown = text if len(text) <= 60 else text[:57] + "..."
    if config.quiet:
        print(f"  [{time.strftime('%H:%M:%S')}] -> {voice}: {shown}", flush=True)
    else:
        log.info("received %r voice=%s", text[:60], voice)


def queue_event(event: events_module.Event) -> None:
    """
    Turns a redemption or donation into a call, or drops it.

    Called on the socket thread the moment the event lands, rather than when
    the mod next asks. That is the whole point of the redesign: synthesis for
    a call bought during a pause menu runs while the menu is still up, so the
    audio is already finished when the player comes back.
    """
    if not rules.accepts(event.kind, event.amount):
        log.info("ignored %s from %s: the ini does not allow it", event.kind, event.user)
        return

    text = rules.text_for(event.text)
    if text is None:
        return

    store.submit(text, event.user or "Viewer", event.kind)


def render(text: str, voice: Optional[str] = None,
           stats: Optional[dict] = None) -> "tuple[bytes, str]":
    """
    Text in, finished telephone audio out. The whole pipeline, in one place.

    Called from two directions: the worker thread draining the call store, and
    the synchronous /speak route. Neither touches the stages directly, so a
    call the game takes and a line auditioned by test_client.py sound alike.

    A stage that fails is skipped rather than fatal - a dry voice reaches the
    player, an exception reaches nobody.

    Pass a dict as stats to be told how long each stage took; /speak turns
    that into the headers test_client.py prints. The worker does not care,
    and a shared attribute would be a race between the two callers.
    """
    if tts is None:
        raise RuntimeError("TTS engine not ready")

    # Normally the voice picked at startup. A caller may still name one -
    # test_client.py auditions a single model that way - but the mod does not,
    # so what the console reported at startup is what the player hears.
    chosen = (voice or active_voice).strip()
    if rvc is not None:
        # The character's own name, even when a .pth file name was asked for.
        named = rvc.canonical(chosen)
        if named is None:
            if chosen != active_voice:
                log.info("No RVC model %r; using %r", chosen, active_voice)
            chosen = active_voice
        else:
            chosen = named

    # Before synthesis, not after: the timing line below can be a minute away,
    # and until then a hung request looks like one that never arrived.
    _log_received(text, chosen)

    started = time.perf_counter()
    audio = tts.synthesize(text)
    tts_ms = (time.perf_counter() - started) * 1000

    rvc_ms = 0.0
    converted = False
    if rvc is not None and rvc.has_voice(chosen):
        rvc_started = time.perf_counter()
        try:
            audio = rvc.convert(audio, chosen)
            converted = True
        except Exception:
            log.exception("RVC failed, serving raw TTS")
        rvc_ms = (time.perf_counter() - rvc_started) * 1000
    elif rvc is not None:
        log.warning("No RVC model for voice %r; serving raw TTS", chosen)

    fx_ms = 0.0
    if phone is not None:
        fx_started = time.perf_counter()
        try:
            audio = phone_fx.apply(audio, phone)
        except Exception:
            log.exception("Phone effect failed, serving unfiltered audio")
        fx_ms = (time.perf_counter() - fx_started) * 1000

    if config.quiet:
        total_s = (tts_ms + rvc_ms + fx_ms) / 1000
        spoken = text if len(text) <= 60 else text[:57] + "..."
        print(f"  [{time.strftime('%H:%M:%S')}] {chosen}: {spoken}  ({total_s:.1f}s)", flush=True)
    else:
        log.info(
            "spoke %r voice=%s tts=%.0fms rvc=%.0fms fx=%.0fms converted=%s",
            text[:60], chosen, tts_ms, rvc_ms, fx_ms, converted,
        )

    if stats is not None:
        stats.update({
            "converted": str(converted).lower(),
            "tts_ms": f"{tts_ms:.0f}",
            "rvc_ms": f"{rvc_ms:.0f}",
            "fx_ms": f"{fx_ms:.0f}",
        })
    return audio, chosen


def _clean(text: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    return text[: config.max_text_length]


@app.get("/call")
def next_call() -> dict:
    """
    The call the game should be ringing right now, if there is one.

    Deliberately not draining. The same call is handed out on every poll until
    /call/ack confirms it landed, because between this answer and that ack the
    game can pause, crash or reload a script, and a paid call lost in that gap
    is one the viewer never hears.
    """
    call = store.peek()
    if call is None:
        return {"call": None, **store.stats()}
    return {"call": call.as_dict(), **store.stats()}


@app.get("/call/{call_id}/audio")
def call_audio(call_id: str) -> Response:
    """The WAV for a ready call. Still not a hand-off; only /call/ack is."""
    call = store.peek()
    if call is None or call.id != call_id:
        raise HTTPException(status_code=404, detail=f"no ready call {call_id}")

    return Response(
        content=call.audio,
        media_type="audio/wav",
        headers={
            "X-Call-Id": call.id,
            "X-Voice": call.voice,
            "X-User": call.user,
        },
    )


@app.post("/call/ack")
def call_ack(request: AckRequest) -> dict:
    """
    The game has the audio; stop offering this call.

    A false "known" is the normal answer to a retry the mod sent after we had
    already dropped the call, so it is reported rather than refused.
    """
    known = store.ack(request.id)
    return {"acked": known, **store.stats()}


@app.post("/speak")
def speak(request: SpeakRequest) -> Response:
    """
    Synthesis with the answer in the response, for test_client.py.

    The mod used this until pausing the game proved it could not: a script
    that is not ticking cannot collect a reply. It stays because auditioning a
    voice from the command line wants exactly this shape.
    """
    if tts is None:
        raise HTTPException(status_code=503, detail="TTS engine not ready")

    stats: dict = {}
    try:
        audio, voice = render(_clean(request.text), request.voice, stats)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "X-Voice": voice,
            "X-Converted": stats.get("converted", "false"),
            "X-TTS-Ms": stats.get("tts_ms", "0"),
            "X-RVC-Ms": stats.get("rvc_ms", "0"),
            "X-FX-Ms": stats.get("fx_ms", "0"),
            "X-Phone": config.phone_preset if phone else "off",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="warning" if config.quiet else "info",
        access_log=not config.quiet,
    )
