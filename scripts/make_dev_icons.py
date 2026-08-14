"""Generate the PLACEHOLDER app icons for the LUXOR Dispatch PWA.

These are deliberately temporary local-development icons: a dark tile
with a cyan keyway mark, drawn from the console's own palette. They are
NOT a corporate logo and no permanent brand mark has been approved --
see docs/PHONE_DISPATCH_PILOT.md ("icon finalization: pending owner").

Regenerate with:  python scripts/make_dev_icons.py

Written with zlib + struct only so the repo gains no image dependency.
"""
from __future__ import annotations

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "operator_ui_v2_connected" / "icons"

BG = (0x0B, 0x0E, 0x14)      # --bg
PLATE = (0x11, 0x18, 0x27)   # --panel
ACCENT = (0x00, 0xD4, 0xFF)  # --accent


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(pixels, size: int) -> bytes:
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixels[y][x]) for x in range(size))
        for y in range(size)
    )
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def _draw(size: int):
    """Maskable-safe: the mark stays inside the middle 80% so a circular
    iOS/Android mask cannot clip it."""
    px = [[BG for _ in range(size)] for _ in range(size)]
    u = size / 16.0  # layout unit

    def rect(x0, y0, x1, y1, colour):
        for y in range(max(0, int(y0)), min(size, int(y1))):
            for x in range(max(0, int(x0)), min(size, int(x1))):
                px[y][x] = colour

    def disc(cx, cy, r, colour):
        r2 = r * r
        for y in range(max(0, int(cy - r)), min(size, int(cy + r + 1))):
            for x in range(max(0, int(cx - r)), min(size, int(cx + r + 1))):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                    px[y][x] = colour

    # Inset plate, so the tile reads as an app rather than a flat square.
    rect(u, u, size - u, size - u, PLATE)

    # Keyway: a bow (ring) over a blade with one ward -- a locksmith
    # mark, not a letterform, and legible at 48 px.
    cx = size / 2.0
    disc(cx, u * 5.6, u * 2.5, ACCENT)
    disc(cx, u * 5.6, u * 1.15, PLATE)
    rect(cx - u * 0.85, u * 7.4, cx + u * 0.85, u * 12.6, ACCENT)
    rect(cx + u * 0.85, u * 9.6, cx + u * 2.6, u * 10.8, ACCENT)
    rect(cx + u * 0.85, u * 11.4, cx + u * 2.2, u * 12.6, ACCENT)
    return px


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512, 180):
        name = "apple-touch-icon.png" if size == 180 else f"icon-{size}.png"
        (OUT / name).write_bytes(_png(_draw(size), size))
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
