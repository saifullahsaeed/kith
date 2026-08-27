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

Three states, because the menu bar has three things to say and was saying one. Resting is the
mark. Working is the mark with its opening walked round the circle — a spinner made out of the
drawing that is already there, so nobody has to learn a second symbol, and motion is the only
signal that reads at this size without stealing width. Waiting on you is a filled disc, and it
is the one icon that is *not* a template: solid against open is the strongest contrast sixteen
pixels can carry, and a request he is blocked on should pull the eye rather than politely match
whatever the menu bar is doing.

The first attempt at "working" was a `·` in the text beside the icon. At menu-bar size a lone
dot is indistinguishable from a dead pixel, and nothing about it says "mid-turn".
"""

from __future__ import annotations

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "resources"
# 16pt is the standard macOS menu-bar icon size; @2x is the Retina variant.
SIZES = {"trayTemplate.png": 16, "trayTemplate@2x.png": 32}

#: How many positions the opening takes as it goes round. Eight is what a menu-bar spinner
#: wants: fewer and the step is visible as a jump, more and nothing is gained at 16px.
FRAMES = 8

#: Where the opening sits on the resting mark, and the step between frames.
GAP_CENTRE = 45.0

#: `oklch(0.64 0.14 62)` — the app's own accent, in sRGB. The waiting disc is drawn in it
#: rather than in black, which is why that one file has no `Template` in its name.
ACCENT = (200, 124, 46)


def ring_alpha(size: int, x: int, y: int, gap_centre: float = GAP_CENTRE) -> int:
    """Alpha for one pixel of an antialiased ring.

    `gap_centre` is where the opening sits, in degrees. Moving it is the whole spinner: the
    frames are this same drawing eight times, not a rotated bitmap, so every frame is drawn at
    full quality instead of resampled from the one before.
    """
    centre = (size - 1) / 2
    # Proportional so 16px and 32px render the same shape.
    outer = size * 0.42
    thickness = max(1.6, size * 0.13)
    inner = outer - thickness

    # The opening, centred on the lower-right diagonal — the same place the app icon's is.
    import math

    gap_half = 34.0

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


def disc_alpha(size: int, x: int, y: int) -> int:
    """Alpha for one pixel of a filled dot, supersampled the same way the ring is."""
    centre = (size - 1) / 2
    radius = size * 0.31
    hits = samples = 0
    for sub_y in (-0.33, 0.0, 0.33):
        for sub_x in (-0.33, 0.0, 0.33):
            samples += 1
            if ((x + sub_x - centre) ** 2 + (y + sub_y - centre) ** 2) ** 0.5 <= radius:
                hits += 1
    return round(255 * hits / samples)


def png(size: int, gap_centre: float = GAP_CENTRE, colour: tuple | None = None) -> bytes:
    """A PNG of the mark. Hand-rolled so this needs nothing installed.

    Greyscale+alpha for a template, RGBA when a colour is given — macOS reads only the alpha of
    a template image, so the waiting disc has to be a real picture to keep its colour.
    """
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0 (None) for each scanline
        for x in range(size):
            if colour is None:
                raw += bytes((0, ring_alpha(size, x, y, gap_centre)))  # black, varying alpha
            else:
                raw += bytes((*colour, disc_alpha(size, x, y)))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    # Colour type 4 = greyscale with alpha; 6 = RGBA. 8 bits per channel either way.
    header = struct.pack(">IIBBBBB", size, size, 8, 6 if colour else 4, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def write(name: str, data: bytes) -> None:
    target = OUT / name
    target.write_bytes(data)
    print(f"  {target.relative_to(OUT.parent)}  {target.stat().st_size}B")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in SIZES.items():
        write(name, png(size))
    # Working: the opening walked round the circle. `Template` before `@2x`, which is the order
    # macOS looks for — get it the other way round and Retina silently upscales the 1x.
    for frame in range(FRAMES):
        angle = GAP_CENTRE + frame * (360.0 / FRAMES)
        write(f"trayWorking{frame}Template.png", png(16, angle))
        write(f"trayWorking{frame}Template@2x.png", png(32, angle))
    # Waiting: solid, and in colour, so it is not a template and does not end in one.
    write("trayWaiting.png", png(16, colour=ACCENT))
    write("trayWaiting@2x.png", png(32, colour=ACCENT))


if __name__ == "__main__":
    main()
