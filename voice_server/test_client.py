"""Smoke-test the voice server without launching GTA.

    venv\Scripts\python.exe test_client.py "Hello from Los Santos" trevor
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else "Testing the CallFromTwitch voice pipeline."
    voice = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        with urllib.request.urlopen(f"{BASE}/health", timeout=10) as resp:
            print("health:", json.load(resp))
    except urllib.error.URLError as exc:
        print(f"Server unreachable at {BASE}: {exc}")
        print("Start it with run_server.bat")
        return 1

    payload = {"text": text}
    if voice:
        payload["voice"] = voice

    request = urllib.request.Request(
        f"{BASE}/speak",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            audio = resp.read()
            print(
                f"got {len(audio)} bytes | voice={resp.headers.get('X-Voice')} "
                f"converted={resp.headers.get('X-Converted')} "
                f"tts={resp.headers.get('X-TTS-Ms')}ms rvc={resp.headers.get('X-RVC-Ms')}ms"
            )
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}")
        return 1

    out = "test_output.wav"
    with open(out, "wb") as handle:
        handle.write(audio)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
