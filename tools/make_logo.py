"""Draw the channel logo -- a straight sword crossing a crescent moon inside a ring.

Pure Python: the shapes are sampled with 4x4 supersampling and the PNG is written with zlib, so
nothing needs to be installed. Run it again after changing a colour or a radius:

    python tools/make_logo.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 512
SS = 4  # supersamples per pixel per axis

GOLD = (232, 181, 75)
GOLD_LIGHT = (246, 219, 155)
BLADE = (228, 238, 248)
BLADE_EDGE = (146, 170, 196)
INK = (16, 18, 26)

# Geometry in a unit square, (0,0) top-left. The sword stands upright, the moon opens to the right.
RING_R, RING_W = 0.455, 0.030
MOON_C, MOON_R = (0.470, 0.520), 0.330
MOON_CUT_C, MOON_CUT_R = (0.585, 0.455), 0.300
TIP_Y, GUARD_Y, GRIP_Y, POMMEL_Y = 0.105, 0.640, 0.815, 0.845
BLADE_HW = 0.031
GUARD_HW, GUARD_HH = 0.135, 0.020
GRIP_HW = 0.023
POMMEL_R = 0.034


def _in_disc(x: float, y: float, c: tuple[float, float], r: float) -> bool:
    return (x - c[0]) ** 2 + (y - c[1]) ** 2 <= r * r


def _in_box(x: float, y: float, cx: float, cy: float, hw: float, hh: float) -> bool:
    return abs(x - cx) <= hw and abs(y - cy) <= hh


def _sword(x: float, y: float) -> tuple[int, int, int] | None:
    """The blade tapers to a point, sits on a guard, and ends in a round pommel."""
    if _in_disc(x, y, (0.5, POMMEL_Y), POMMEL_R):
        return GOLD
    if _in_box(x, y, 0.5, (GUARD_Y + GRIP_Y) / 2, GRIP_HW, (GRIP_Y - GUARD_Y) / 2):
        return INK
    if _in_box(x, y, 0.5, GUARD_Y, GUARD_HW, GUARD_HH):
        return GOLD
    if TIP_Y <= y <= GUARD_Y:
        # Full width at the guard, a point at the tip; the last 12% of the length is the taper.
        t = (y - TIP_Y) / (GUARD_Y - TIP_Y)
        hw = BLADE_HW * min(1.0, t / 0.12)
        if abs(x - 0.5) <= hw:
            return BLADE if abs(x - 0.5) <= hw * 0.55 else BLADE_EDGE
    return None


def _sample(x: float, y: float) -> tuple[int, int, int, int]:
    px = _sword(x, y)
    if px is not None:
        return (*px, 255)
    if _in_disc(x, y, MOON_C, MOON_R) and not _in_disc(x, y, MOON_CUT_C, MOON_CUT_R):
        return (*GOLD_LIGHT, 255)
    d2 = (x - 0.5) ** 2 + (y - 0.5) ** 2
    if (RING_R - RING_W) ** 2 <= d2 <= RING_R ** 2:
        return (*GOLD, 255)
    if d2 <= RING_R ** 2:
        return (*INK, 150)  # a soft dark disc so the mark reads on bright scenes
    return (0, 0, 0, 0)


def render(size: int = SIZE) -> list[bytes]:
    rows = []
    step = 1.0 / (size * SS)
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                y = (py * SS + sy + 0.5) * step
                for sx in range(SS):
                    sr, sg, sb, sa = _sample((px * SS + sx + 0.5) * step, y)
                    # Weight colour by alpha so edges do not darken towards black.
                    r += sr * sa
                    g += sg * sa
                    b += sb * sa
                    a += sa
            n = SS * SS
            row += bytes((r // a, g // a, b // a, a // n)) if a else b"\0\0\0\0"
        rows.append(bytes(row))
    return rows


def write_png(path: Path, rows: list[bytes]) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    size = len(rows)
    head = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    body = zlib.compress(b"".join(b"\x00" + r for r in rows), 9)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", body) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "assets" / "logos" / "logo.png"
    write_png(out, render())
    print(f"wrote {out}")
