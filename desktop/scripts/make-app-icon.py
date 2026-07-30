#!/usr/bin/env python3
"""Render Kith's mark to the icon files macOS actually reads, with no dependencies.

`resources/icon.svg` is the design. This is the same geometry in numbers, because nothing
on a stock macOS box can rasterise an SVG: there is no rsvg-convert, no Inkscape, no
ImageMagick, and `sips` does not read SVG. Asking whoever builds this to install a toolchain
to get an app icon is the kind of friction that ends with the Electron default shipping —
which is exactly the bug this fixes.

So the shapes are drawn analytically with 4x4 supersampling, written as PNGs with zlib and
struct (the same approach as make-tray-icon.py), and handed to `iconutil`, which is part of
macOS. The numbers below are proportional to the SVG's 1024 canvas, so the two stay in step:
change the SVG, change these, run `npm run icons`.

Why it matters at all: macOS attributes a notification to an app and draws that app's icon
beside it. Without a real .icns in the bundle, every notification Kith sends arrives wearing
Electron's face, and so does the Dock, Cmd-Tab and the Notification Center settings list.
"""

from __future__ import annotations

import math
import pathlib
import shutil
import struct
import subprocess
import zlib

HERE = pathlib.Path(__file__).resolve().parent
RESOURCES = HERE.parent / "resources"

#: iconutil's required names. Both 1x and 2x of each size, or macOS silently falls back to
#: upscaling and the icon looks soft at exactly the sizes people see most.
ICONSET = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

# --- the mark, proportional to a 1024 canvas ------------------------------- #
ORB_R = 150 / 1024
ARC_R = 268 / 1024
ARC_W = 84 / 1024
GLOW_R = 250 / 1024
#: The opening, centred on the lower-right diagonal.
GAP_CENTRE_DEG = 45.0
GAP_HALF_DEG = 34.0
#: Squircle corner. macOS masks a real .icns itself, but the plate is drawn anyway so the
#: file is correct wherever else it is used.
PLATE_INSET = 40 / 1024
PLATE_RADIUS = 0.46

AMBER = (241, 182, 90)
AMBER_LIGHT = (255, 224, 168)
AMBER_DEEP = (208, 140, 46)
PLATE_TOP = (36, 30, 25)
PLATE_MID = (20, 16, 11)
PLATE_BOTTOM = (14, 9, 5)

SAMPLES = 4  # 4x4 per pixel; 16 at 16px is visibly lumpy with fewer


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[float, float, float]:
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))  # type: ignore[return-value]


def over(dst: tuple[float, float, float], src, alpha: float):
    """Source-over compositing on an opaque destination."""
    return tuple(src[i] * alpha + dst[i] * (1 - alpha) for i in range(3))


def in_squircle(u: float, v: float) -> bool:
    """Is this point inside the rounded plate?

    A rounded rectangle rather than a true superellipse: the difference is invisible under
    macOS's own mask, and the closed form keeps this readable.
    """
    inset = PLATE_INSET
    radius = PLATE_RADIUS - inset
    lo, hi = inset, 1.0 - inset
    if not (lo <= u <= hi and lo <= v <= hi):
        return False
    cx = min(max(u, lo + radius), hi - radius)
    cy = min(max(v, lo + radius), hi - radius)
    return (u - cx) ** 2 + (v - cy) ** 2 <= radius**2 + 1e-9


def on_arc(u: float, v: float, arc_r: float, arc_w: float, gap_half: float) -> bool:
    """Inside the stroked arc, round caps included.

    The caps are the reason the opening reads as space rather than as a break, so they are
    modelled rather than approximated: a point counts if it is within half the stroke width
    of the arc band *and* inside the kept angular range, or within half a stroke of either
    cap centre.
    """
    if arc_w <= 0:
        return False
    dx, dy = u - 0.5, v - 0.5
    distance = math.hypot(dx, dy)
    half = arc_w / 2
    if abs(distance - arc_r) <= half:
        angle = math.degrees(math.atan2(dy, dx)) % 360
        delta = abs((angle - GAP_CENTRE_DEG + 180) % 360 - 180)
        if delta > gap_half:
            return True
    for sign in (-1, 1):
        angle = math.radians(GAP_CENTRE_DEG + sign * gap_half)
        cap = (0.5 + arc_r * math.cos(angle), 0.5 + arc_r * math.sin(angle))
        if math.hypot(u - cap[0], v - cap[1]) <= half:
            return True
    return False


def geometry(size: int) -> tuple[float, float, float, float]:
    """The mark's proportions at this size, simplified as it gets small.

    Apple's own icons are redrawn at small sizes rather than scaled, and this is why: at
    16px the arc and the orb are each about a pixel apart, so they merge into a blob and the
    mark becomes a brown dot. Below 32 the arc is dropped entirely and the orb grows to
    carry the identity on its own — at that size the warm circle IS the recognisable thing.
    At 32 the arc returns, thicker and with a wider opening than the full design, because a
    one-pixel stroke with a two-pixel gap reads as a smudge.
    """
    if size < 32:
        return ORB_R * 2.0, 0.0, 0.0, GAP_HALF_DEG
    if size < 64:
        return ORB_R * 1.16, ARC_R, ARC_W * 1.30, GAP_HALF_DEG * 1.25
    return ORB_R, ARC_R, ARC_W, GAP_HALF_DEG


def shade(u: float, v: float, orb_r: float, arc_r: float, arc_w: float, gap_half: float):
    """Colour and coverage for one sample, or None outside the plate."""
    if not in_squircle(u, v):
        return None

    # Plate: warm, lit from the top left, deepening downward.
    diagonal = (u * 0.45 + v) / 1.45
    colour = lerp(PLATE_TOP, PLATE_MID, diagonal / 0.55) if diagonal < 0.55 else lerp(
        PLATE_MID, PLATE_BOTTOM, (diagonal - 0.55) / 0.45
    )
    # The broad highlight across the top, matching the SVG's ellipse.
    hx, hy, rx, ry = 380 / 1024, 150 / 1024, 520 / 1024, 300 / 1024
    if ((u - hx) / rx) ** 2 + ((v - hy) / ry) ** 2 <= 1:
        colour = over(colour, AMBER, 0.05)

    # Glow, then arc, then orb — the SVG's paint order.
    distance = math.hypot(u - 0.5, v - 0.5)
    if distance <= GLOW_R:
        t = distance / GLOW_R
        alpha = 0.42 * (1 - t / 0.45) if t < 0.45 else 0.16 * (1 - (t - 0.45) / 0.55)
        colour = over(colour, AMBER, max(0.0, alpha))

    if on_arc(u, v, arc_r, arc_w, gap_half):
        colour = over(colour, AMBER, 0.94)

    if distance <= orb_r:
        # Highlight up and left, exactly where the SVG's radial gradient puts it.
        t = math.hypot(u - (0.5 - orb_r * 0.28), v - (0.5 - orb_r * 0.36)) / (orb_r * 1.64)
        orb = lerp(AMBER_LIGHT, AMBER, t / 0.45) if t < 0.45 else lerp(
            AMBER, AMBER_DEEP, (t - 0.45) / 0.55
        )
        colour = orb

    return (*colour, 1.0)


def render(size: int) -> bytes:
    """One RGBA image, supersampled."""
    rows = bytearray()
    orb_r, arc_r, arc_w, gap_half = geometry(size)
    step = 1.0 / (size * SAMPLES)
    for y in range(size):
        rows.append(0)  # PNG filter byte: none
        for x in range(size):
            r = g = b = a = 0.0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    u = (x * SAMPLES + sx + 0.5) * step
                    v = (y * SAMPLES + sy + 0.5) * step
                    sample = shade(u, v, orb_r, arc_r, arc_w, gap_half)
                    if sample is None:
                        continue
                    r += sample[0]
                    g += sample[1]
                    b += sample[2]
                    a += sample[3]
            n = SAMPLES * SAMPLES
            if a <= 0:
                rows += bytes((0, 0, 0, 0))
                continue
            # Divide colour by coverage, not by sample count: at the rounded edge the
            # covered samples are the only ones with colour, and averaging over all of
            # them would darken the rim into a grey fringe.
            rows += bytes(
                (
                    max(0, min(255, round(r / a))),
                    max(0, min(255, round(g / a))),
                    max(0, min(255, round(b / a))),
                    max(0, min(255, round(255 * a / n))),
                )
            )
    return bytes(rows)


def write_png(path: pathlib.Path, size: int, raw: bytes) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    iconset = RESOURCES / "Kith.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir(parents=True)

    # Rendered once per distinct size and copied, since several names share dimensions and
    # the 1024 render is the slow part.
    cache: dict[int, bytes] = {}
    for name, size in ICONSET.items():
        if size not in cache:
            print(f"[icon] rendering {size}x{size}")
            cache[size] = render(size)
        write_png(iconset / name, size, cache[size])

    icns = RESOURCES / "icon.icns"
    result = subprocess.run(
        ["iconutil", "--convert", "icns", "--output", str(icns), str(iconset)],
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"iconutil failed: {result.stderr.decode(errors='replace')}")
    # A 512px PNG as well: electron-builder takes one, and Linux wants it.
    write_png(RESOURCES / "icon.png", 512, cache[512])
    shutil.rmtree(iconset)
    print(f"[icon] wrote {icns.name} ({icns.stat().st_size:,} bytes) and icon.png")


if __name__ == "__main__":
    main()
