#!/usr/bin/env python3
"""Generate the macOS menu-bar tray icons, with no image-library dependency.

macOS template images are a mask, not a picture: only the alpha channel is read,
and the system paints them black in a light menu bar and white in a dark one. That
is why this writes pure-black pixels and varies only alpha — a coloured icon would
come out as a solid blob.

Two rules the platform enforces, both easy to break silently:

* The filename must end in ``Template``. Without it macOS neither inverts the icon
  for dark mode nor dims it correctly when the menu bar is inactive.
* ``@2x`` must share the same base name and be exactly double the pixel size, or
  Retina displays fall back to upscaling the 1x and it looks soft.

The mark matches the app icon: an arc with an opening at the lower right, which is Kith's
mark everywhere else. A ring — or an arc — reads at 16px where a glyph would turn to mush,
and the gap survives because it is a third of the circle rather than a nick in it.
"""

from __future__ import annotations

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "resources"
# 16pt is the standard macOS menu-bar icon size; @2x is the Retina variant.
SIZES = {"trayTemplate.png": 16, "trayTemplate@2x.png": 32}


def ring_alpha(size: int, x: int, y: int) -> int:
    """Alpha for one pixel of an antialiased ring."""
    centre = (size - 1) / 2
    # Proportional so 16px and 32px render the same shape.
    outer = size * 0.42
    thickness = max(1.6, size * 0.13)
    inner = outer - thickness

    # The opening, centred on the lower-right diagonal — the same place the app icon's is.
    import math

    gap_centre, gap_half = 45.0, 34.0

    # Supersample 3x3 — at 16px an aliased circle looks visibly lumpy.
    hits = 0
    samples = 0
    for sub_y in (-0.33, 0.0, 0.33):
        for sub_x in (-0.33, 0.0, 0.33):
            dx, dy = x + sub_x - centre, y + sub_y - centre
            distance = (dx**2 + dy**2) ** 0.5
            samples += 1
            angle = math.degrees(math.atan2(dy, dx)) % 360
            if abs((angle - gap_centre + 180) % 360 - 180) <= gap_half:
                # Inside the gap — unless it is within a round cap, which is what keeps the
                # opening from looking chewed.
                capped = False
                for sign in (-1, 1):
                    a = math.radians(gap_centre + sign * gap_half)
                    cx_, cy_ = centre + (outer - thickness / 2) * math.cos(a), centre + (
                        outer - thickness / 2
                    ) * math.sin(a)
                    if ((x + sub_x - cx_) ** 2 + (y + sub_y - cy_) ** 2) ** 0.5 <= thickness / 2:
                        capped = True
                        break
                if not capped:
                    continue
            if inner <= distance <= outer:
                hits += 1
    return round(255 * hits / samples)


def png(size: int) -> bytes:
    """A greyscale+alpha PNG. Hand-rolled so this needs nothing installed."""
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0 (None) for each scanline
        for x in range(size):
            raw += bytes((0, ring_alpha(size, x, y)))  # black, varying alpha

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # Colour type 4 = greyscale with alpha, 8 bits per channel.
    header = struct.pack(">IIBBBBB", size, size, 8, 4, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        target = OUT / name
        target.write_bytes(png(size))
        print(f"  {target.relative_to(OUT.parent)}  {size}x{size}  {target.stat().st_size}B")


if __name__ == "__main__":
    main()
