"""Fetch a Piper voice into models/piper.

    venv\Scripts\python.exe download_voice.py en_US-ryan-high
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# American English on purpose: RVC keeps the carrier's accent and phonetics,
# so an en_US reference is what makes GTA character models sound native.
DEFAULT_VOICE = "en_US-ryan-high"
TARGET = Path(__file__).resolve().parent / "models" / "piper"


def main() -> int:
    voice = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VOICE
    TARGET.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Piper voice '{voice}' into {TARGET}")
    result = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", voice, "--data-dir", str(TARGET)],
        check=False,
    )
    if result.returncode != 0:
        print(
            "\nDownload failed. Browse available voices at:\n"
            "  https://huggingface.co/rhasspy/piper-voices",
            file=sys.stderr,
        )
        return result.returncode

    onnx = TARGET / f"{voice}.onnx"
    if onnx.is_file():
        print(f"\nReady: {onnx}")
        print(f"Set it with: set CFT_PIPER_MODEL={onnx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
