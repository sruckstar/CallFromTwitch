"""
CallFromTwitch voice server.

POST /speak {"text": "...", "voice": "trevor"} -> audio/wav

Piper TTS renders neutral speech, RVC converts the timbre to the requested
character, and a telephone filter colours the result. Models stay resident.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

import events as events_module
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
# Used when a request names no voice, or one that is not installed.
active_voice: str = config.default_voice
# Always present; it simply starts nothing when unconfigured.
hub: events_module.EventHub = events_module.EventHub(config)


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
    voice: Optional[str] = None


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

    _print_ready()


def _print_ready() -> None:
    """The status block the player checks before alt-tabbing into the game."""
    set_window_title(f"CallFromTwitch - {active_voice} - port {config.port}")
    if not config.quiet:
        log.info("Ready on http://%s:%s (voice: %s)",
                 config.host, config.port, active_voice)
        return

    voices = rvc.available_voices() if rvc else []
    effects = config.phone_preset if phone else "off"
    print()
    print(f"  Listening on  http://{config.host}:{config.port}")
    print(f"  Voice         {active_voice}" + (f"   (of {len(voices)} installed)" if len(voices) > 1 else ""))
    print(f"  Phone effect  {effects}")
    print()
    print("  Ready - start GTA V. Keep this window open; Ctrl+C stops the server.")
    print()


def select_voice(voices: list[str]) -> None:
    """
    Ask which of several installed voices this run should default to.

    Only decides the fallback for a request naming a voice that is not
    installed. Skipped when there is one voice, when CFT_VOICE_PROMPT is off,
    or when there is no console to answer on, so unattended starts never hang.
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
    return {
        "status": "ok",
        "tts": tts is not None,
        "rvc": rvc is not None,
        "phone": config.phone_preset if phone else None,
        "voices": rvc.available_voices() if rvc else [],
        "events": hub.sources,
        **hub.queue.stats(),
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


@app.post("/speak")
def speak(request: SpeakRequest) -> Response:
    if tts is None:
        raise HTTPException(status_code=503, detail="TTS engine not ready")

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")
    if len(text) > config.max_text_length:
        text = text[: config.max_text_length]

    voice = (request.voice or active_voice).strip()
    if rvc is not None:
        # The character's own name, even when the ini asked for a file name.
        named = rvc.canonical(voice)
        if named is None:
            if voice != active_voice:
                log.info("No RVC model '%s'; using '%s'", voice, active_voice)
            voice = active_voice
        else:
            voice = named

    # Before synthesis, not after: the "spoke ..." line below can be a minute
    # away, and until then a hung request looks like one that never arrived.
    _log_received(text, voice)

    started = time.perf_counter()
    try:
        audio = tts.synthesize(text)
    except Exception as exc:
        log.exception("TTS failed")
        raise HTTPException(status_code=500, detail=f"TTS failed: {exc}") from exc
    tts_ms = (time.perf_counter() - started) * 1000

    rvc_ms = 0.0
    converted = False
    if rvc is not None and rvc.has_voice(voice):
        rvc_started = time.perf_counter()
        try:
            audio = rvc.convert(audio, voice)
            converted = True
        except Exception:
            log.exception("RVC failed, serving raw TTS")
        rvc_ms = (time.perf_counter() - rvc_started) * 1000
    elif rvc is not None:
        log.warning("No RVC model for voice '%s'; serving raw TTS", voice)

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
        print(f"  [{time.strftime('%H:%M:%S')}] {voice}: {spoken}  ({total_s:.1f}s)", flush=True)
    else:
        log.info(
            "spoke %r voice=%s tts=%.0fms rvc=%.0fms fx=%.0fms converted=%s",
            text[:60], voice, tts_ms, rvc_ms, fx_ms, converted,
        )

    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "X-Voice": voice,
            "X-Converted": str(converted).lower(),
            "X-TTS-Ms": f"{tts_ms:.0f}",
            "X-RVC-Ms": f"{rvc_ms:.0f}",
            "X-FX-Ms": f"{fx_ms:.0f}",
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
