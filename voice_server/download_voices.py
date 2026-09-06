"""Fetch the RVC character voices listed in voices.json into models/rvc.

    python download_voices.py                     every voice in the manifest
    python download_voices.py Michael             just one
    python download_voices.py --list              what the manifest offers
    python download_voices.py --url URL Michael   install from a link directly

Standard library only, on purpose: setup.bat calls this before the venv
exists, so it has to run on a bare Python 3.10.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MANIFEST = BASE_DIR / "voices.json"
TARGET = BASE_DIR / "models" / "rvc"

# Some hosts (Hugging Face among them) answer a bare urllib request with 403.
HEADERS = {"User-Agent": "CallFromTwitch-installer"}

# What a usable voice folder holds. Anything else in an archive is dropped.
KEEP_SUFFIXES = {".pth", ".index"}


def load_manifest() -> dict:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"  ! voices.json is not readable: {exc}", file=sys.stderr)
        return {}
    voices = data.get("voices")
    return voices if isinstance(voices, dict) else {}


def installed(name: str) -> bool:
    """True when a voice folder already holds a model the engine would find."""
    folder = TARGET / name
    return folder.is_dir() and any(folder.glob("*.pth"))


def _human(size: float) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def download(url: str, dest: Path, label: str, expect_mb: float = 0) -> None:
    """Stream a URL to disk, drawing a one-line progress bar."""
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        # A share page answers 200 with HTML; catch that before writing 5 KB
        # of markup into a file named .pth and failing much later.
        ctype = (response.headers.get("Content-Type") or "").lower()
        if ctype.startswith("text/html"):
            raise RuntimeError(
                "the link returned a web page, not a file - it has to be a "
                "direct download link"
            )

        total = int(response.headers.get("Content-Length") or 0)
        if not total and expect_mb:
            total = int(expect_mb * 1024 * 1024)

        done = 0
        with dest.open("wb") as out:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    filled = int(28 * done / total)
                    bar = "#" * filled + "-" * (28 - filled)
                    sys.stdout.write(f"\r    {label} [{bar}] {100 * done / total:5.1f}%")
                else:
                    sys.stdout.write(f"\r    {label} {_human(done)}")
                sys.stdout.flush()
    sys.stdout.write(f"\r    {label} [{'#' * 28}] {_human(done)}      \n")


def verify(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual.lower() != expected.lower():
        raise RuntimeError(f"checksum mismatch (got {actual[:16]}...)")


def _extract(archive: Path, folder: Path) -> None:
    """
    Flatten a voice archive into folder, keeping only .pth and .index.

    Uploads nest the files under a folder of their own about as often as not,
    so the archive's own layout is discarded rather than reproduced.
    """
    with zipfile.ZipFile(archive) as zf:
        members = [
            m for m in zf.infolist()
            if not m.is_dir() and Path(m.filename).suffix.lower() in KEEP_SUFFIXES
        ]
        if not any(Path(m.filename).suffix.lower() == ".pth" for m in members):
            raise RuntimeError("the archive holds no .pth model file")

        for member in members:
            # Name only: a zip entry may carry .. or an absolute path.
            name = Path(member.filename).name
            with zf.open(member) as src, (folder / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)


def install(name: str, spec: dict) -> bool:
    """Download and unpack one voice. Returns False on any failure."""
    url = (spec.get("url") or "").strip()
    if not url:
        print(f"  - {name}: no download link set in voices.json - skipped")
        return False

    print(f"  {name}: downloading ({spec.get('size_mb', '?')} MB)")
    folder = TARGET / name
    # Built in a temp folder and moved into place, so an interrupted download
    # never leaves a half-installed voice that later runs take for a real one.
    staging = Path(tempfile.mkdtemp(prefix=f"cft_{name}_", dir=TARGET))
    try:
        is_zip = url.lower().split("?")[0].endswith(".zip")
        payload = staging / ("voice.zip" if is_zip else f"{name}.pth")
        download(url, payload, name, float(spec.get("size_mb") or 0))

        if spec.get("sha256"):
            verify(payload, spec["sha256"])

        if is_zip:
            _extract(payload, staging)
            payload.unlink()
        elif spec.get("index_url"):
            download(spec["index_url"], staging / f"{name}.index", f"{name}.index")

        if folder.is_dir():
            shutil.rmtree(folder)
        staging.replace(folder)
        staging = None  # moved; nothing left to clean up
        print(f"  {name}: installed")
        return True
    except (urllib.error.URLError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"\n  ! {name}: {exc}", file=sys.stderr)
        return False
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def show_list(manifest: dict) -> int:
    if not manifest:
        print("voices.json lists no voices.")
        return 0
    print("Character voices in voices.json:\n")
    for name, spec in manifest.items():
        if installed(name):
            state = "installed"
        elif spec.get("url"):
            state = "available"
        else:
            state = "no link set"
        print(f"  {name:<14} {state:<12} {spec.get('description', '')}")
    print(f"\nInstalled into: {TARGET}")
    return 0


def main(argv: list[str]) -> int:
    args = list(argv)
    force = "--force" in args
    if force:
        args.remove("--force")

    # --url installs a link the manifest does not carry, which is how a voice
    # shared as a one-off gets in without editing the file.
    direct = ""
    if "--url" in args:
        at = args.index("--url")
        if at + 1 >= len(args):
            print("--url needs a link after it", file=sys.stderr)
            return 2
        direct = args[at + 1]
        del args[at:at + 2]

    manifest = load_manifest()
    if "--list" in args:
        return show_list(manifest)

    names = [a for a in args if not a.startswith("-")]
    TARGET.mkdir(parents=True, exist_ok=True)

    if direct:
        if len(names) != 1:
            print("--url takes exactly one voice name", file=sys.stderr)
            return 2
        return 0 if install(names[0], {"url": direct}) else 1

    if not names:
        names = list(manifest)
    if not names:
        print("Nothing to download: voices.json lists no voices.")
        return 0

    wanted = []
    for name in names:
        spec = manifest.get(name)
        if spec is None:
            # Case-insensitive, so "michael" works like the folder name.
            match = next((k for k in manifest if k.lower() == name.lower()), None)
            if match is None:
                print(f"  ! unknown voice '{name}' - see --list", file=sys.stderr)
                return 2
            name, spec = match, manifest[match]
        if installed(name) and not force:
            print(f"  {name}: already there")
            continue
        wanted.append((name, spec))

    if not wanted:
        return 0

    failed = [name for name, spec in wanted if not install(name, spec)]
    if failed:
        print(
            f"\n  {len(failed)} voice(s) did not install: {', '.join(failed)}\n"
            f"  They can also be dropped in by hand:\n"
            f"    {TARGET}\\<Name>\\<model>.pth",
            file=sys.stderr,
        )
        # Not an error for setup: the mod runs without character voices.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
