#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Draw the status icons of the search progress window.

Writes white-on-transparent PNGs into the add-on's skin media folder; the
window tints them with ``colordiffuse``. Standard library only, so the icons
can be redrawn anywhere:

    python3 scripts/make_progress_icons.py
"""

import math
import os
import struct
import zlib

SIZE = 64
SAMPLES = 4
MEDIA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "skins",
    "Default",
    "media",
)
CENTER = SIZE / 2.0
RADIUS = 26.0
STROKE = 6.0


def _segment_distance(x, y, start, end):
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if not length else ((x - ax) * dx + (y - ay) * dy) / length
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (ax + t * dx), y - (ay + t * dy))


def _radius(x, y):
    return math.hypot(x - CENTER, y - CENTER)


def _ring(x, y):
    return abs(_radius(x, y) - RADIUS) <= STROKE / 2.0


def _disc(x, y):
    return _radius(x, y) <= RADIUS + STROKE / 2.0


def _check(x, y):
    return (
        min(
            _segment_distance(x, y, (19, 33), (28, 42)),
            _segment_distance(x, y, (28, 42), (46, 23)),
        )
        <= STROKE / 2.0
    )


def _exclamation(x, y):
    return (
        _segment_distance(x, y, (32, 18), (32, 35)) <= STROKE / 2.0
        or math.hypot(x - 32, y - 45) <= STROKE / 2.0 + 0.5
    )


def _dash(x, y):
    return _segment_distance(x, y, (21, 32), (43, 32)) <= STROKE / 2.0


def _arc(x, y):
    """Three quarters of the ring: the gap is the top-right quarter."""
    angle = math.degrees(math.atan2(y - CENTER, x - CENTER)) % 360.0
    return _ring(x, y) and not 270.0 < angle < 360.0


SHAPES = {
    "progress-pending.png": _ring,
    "progress-active.png": _arc,
    "progress-done.png": lambda x, y: _disc(x, y) and not _check(x, y),
    "progress-warn.png": lambda x, y: _disc(x, y) and not _exclamation(x, y),
    "progress-skipped.png": lambda x, y: _ring(x, y) or _dash(x, y),
    "progress-dot.png": lambda x, y: _radius(x, y) <= 18.0,
}


def _alpha(shape, px, py):
    hits = 0
    for sy in range(SAMPLES):
        for sx in range(SAMPLES):
            x = px + (sx + 0.5) / SAMPLES
            y = py + (sy + 0.5) / SAMPLES
            hits += 1 if shape(x, y) else 0
    return int(round(255.0 * hits / (SAMPLES * SAMPLES)))


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def render(shape):
    """Return the PNG bytes of ``shape`` as white with an alpha channel."""
    rows = bytearray()
    for py in range(SIZE):
        rows.append(0)
        for px in range(SIZE):
            rows.extend((255, 255, 255, _alpha(shape, px, py)))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )


def main():
    """Write every icon."""
    for name, shape in SHAPES.items():
        path = os.path.join(MEDIA_DIR, name)
        with open(path, "wb") as handle:
            handle.write(render(shape))
        print(path)


if __name__ == "__main__":
    main()
