#!/usr/bin/env python3
"""Reproduce approved icon packaging; never synthesize or retouch the source."""
from hashlib import sha256
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/branding/navmate-adopted-source.png"
SOURCE_SHA256 = "30faff87f7820ae4e62cc4c3b1bca016137b3af041672166472aaf5cf166c3e6"
# Square crop removes stray low-alpha outer edge pixels, leaving the rounded tile
# and the complete N/aircraft artwork. Coordinates: left, top, right, bottom.
CROP = (26, 28, 1228, 1230)


def main() -> None:
    if sha256(SOURCE.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Adopted source checksum mismatch")
    with Image.open(SOURCE) as original:
        if original.mode != "RGBA" or original.size != (1254, 1254):
            raise ValueError("Unexpected adopted source format")
        icon = original.crop(CROP)
        target = ROOT / "web/public"
        for size, name in (
            (32, "favicon-32.png"),
            (180, "apple-touch-icon.png"),
            (192, "navmate-192.png"),
            (512, "navmate-512.png"),
        ):
            icon.resize((size, size), Image.Resampling.LANCZOS).save(target / name)
        icon.save(target / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])


if __name__ == "__main__":
    main()
