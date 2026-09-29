"""Download the manga-colorization-v2 weights on first run (they are not redistributed here)."""
import hashlib
import os
import urllib.request
from pathlib import Path

MODELS_DIR = Path(os.environ.get("MODELS_DIR", Path(__file__).parent / "models"))

# Mirrors published by manga-image-translator; original weights by qweasdd/manga-colorization-v2.
WEIGHTS = {
    "generator.zip": (
        "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/manga-colorization-v2-generator.zip",
        "087e6a0bc02770e732a52f33878b71a272a6123c9ac649e9b5bfb75e39e5c1d5",
    ),
    "net_rgb.pth": (
        "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/manga-colorization-v2-net_rgb.pth",
        "0fe98bfd2ac870b15f360661b1c4789eecefc6dc2e4462842a0dd15e149a0433",
    ),
}
CHUNK_SIZE = 1 << 20


def ensure_weights() -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, (url, sha256) in WEIGHTS.items():
        target = MODELS_DIR / filename
        if target.exists() and _sha256(target) == sha256:
            continue
        print(f"Downloading {filename} ...", flush=True)
        partial = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url) as response, partial.open("wb") as out:
            while chunk := response.read(CHUNK_SIZE):
                out.write(chunk)
        if _sha256(partial) != sha256:
            partial.unlink()
            raise RuntimeError(f"Checksum mismatch for {filename}; download corrupted or file changed upstream")
        partial.replace(target)
    return MODELS_DIR


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
