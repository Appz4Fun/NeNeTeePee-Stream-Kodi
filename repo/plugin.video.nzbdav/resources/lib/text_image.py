# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Text drawn to a PNG with a fixed 8x16 terminal font.

An NFO is laid out for a character grid: its box art only lines up in a
monospace font with the box-drawing and block characters of the old PC
character set. A Kodi add-on can only use the fonts the active skin defines,
and many skins define no monospace font at all. So the picker's viewer does
not ask Kodi to draw the text: it draws each page itself, glyph by glyph,
into an image, and shows the image.

The font is a table of 8-pixel-wide, 16-row glyphs (``load_font``). Drawing
is pure Python on packed bits and stays fast: a page is a few thousand table
lookups, and ``zlib`` does the rest.

Two things are drawn:

* a page of text in one color (``page_png``), one bit per pixel;
* a line of colored words of two sizes (``words_png``), for the row of
  attributes under the text, where "NEW" is set small beside a word.

Both take a ``scale``: how many image pixels a font pixel is. The viewer
picks it for the screen, so the image is shown pixel for pixel.
"""

import struct
import zlib

GLYPH_WIDTH = 8
GLYPH_HEIGHT = 16
_MAGIC = b"NZF1"
_RECORD = 4 + GLYPH_HEIGHT
_TAB = 8

MAX_SCALE = 6
# scale -> (byte -> the same eight pixels, each ``scale`` wide: ``scale`` bytes)
_SPREAD = {}


def _spread(scale):
    table = _SPREAD.get(scale)
    if table is None:
        run = (1 << scale) - 1
        table = tuple(
            sum(
                run << (scale * (7 - bit))
                for bit in range(8)
                if byte & (0x80 >> bit)
            ).to_bytes(scale, "big")
            for byte in range(256)
        )
        _SPREAD[scale] = table
    return table


def _scale(value):
    try:
        return min(MAX_SCALE, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def load_font(path):
    """``{codepoint: sixteen bytes}`` from a packed font file, or None.

    The file is ``NZF1``, a glyph count, then per glyph its code point (four
    bytes, big-endian) and its sixteen rows, one byte each, top row first,
    leftmost pixel in the high bit. A missing or damaged file gives None, and
    the viewer falls back to the skin's own text box.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(4 * 1024 * 1024)
    except OSError:
        return None
    if len(data) < 8 or data[:4] != _MAGIC:
        return None
    (count,) = struct.unpack(">I", data[4:8])
    if count <= 0 or len(data) < 8 + count * _RECORD:
        return None
    glyphs = {}
    for index in range(count):
        start = 8 + index * _RECORD
        (codepoint,) = struct.unpack(">I", data[start : start + 4])
        glyphs[codepoint] = data[start + 4 : start + _RECORD]
    return glyphs if 0x20 in glyphs else None


def pack_font(glyphs):
    """The packed file for ``{codepoint: sixteen bytes}`` (see ``load_font``)."""
    parts = [_MAGIC, struct.pack(">I", len(glyphs))]
    for codepoint in sorted(glyphs):
        rows = bytes(glyphs[codepoint])
        if len(rows) != GLYPH_HEIGHT:
            raise ValueError("a glyph is {} rows".format(GLYPH_HEIGHT))
        parts.append(struct.pack(">I", codepoint) + rows)
    return b"".join(parts)


def _glyph(font, char):
    """The rows of ``char``: its own, else the replacement's, else blank."""
    rows = font.get(ord(char))
    if rows is None:
        rows = font.get(0xFFFD) or font.get(ord("?")) or font[0x20]
    return rows


def wrap(text, columns):
    """``text`` as lines of at most ``columns`` characters.

    Tabs become spaces to the next eighth column, other control characters
    are dropped, and a longer line is cut into pieces: an NFO is never
    re-flowed, since that would break its art.
    """
    columns = max(1, int(columns))
    lines = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = []
        for char in raw:
            if char == "\t":
                line.extend(" " * (_TAB - len(line) % _TAB))
            elif ord(char) >= 0x20 and ord(char) != 0x7F:
                line.append(char)
        line = "".join(line).rstrip()
        if not line:
            lines.append("")
        for start in range(0, len(line), columns):
            lines.append(line[start : start + columns])
    while lines and not lines[-1]:
        lines.pop()
    return lines


def paginate(lines, rows):
    """``lines`` in pages of ``rows``; at least one page."""
    rows = max(1, int(rows))
    pages = [lines[start : start + rows] for start in range(0, len(lines), rows)]
    return pages or [[]]


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def _png(width, height, depth, palette, scanlines):
    """An indexed PNG; palette entry 0 is fully transparent."""
    header = struct.pack(">IIBBBBB", width, height, depth, 3, 0, 0, 0)
    colors = b"".join(bytes(color) for color in palette)
    raw = b"".join(b"\x00" + line for line in scanlines)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", header),
            _chunk(b"PLTE", colors),
            _chunk(b"tRNS", b"\x00"),
            _chunk(b"IDAT", zlib.compress(raw, 6)),
            _chunk(b"IEND", b""),
        )
    )


def _rgb(color):
    """``(r, g, b)`` for "RRGGBB" or "AARRGGBB"."""
    text = str(color or "").strip().lstrip("#")[-6:].rjust(6, "f")
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def page_png(lines, font, columns, rows, color="D4D4D8", scale=2):
    """A page of text as PNG bytes: ``columns`` by ``rows`` characters.

    ``lines`` are the page's lines (``wrap`` and ``paginate``). The image is
    always the full page size, so every page of a text has the same frame.
    Every font pixel is ``scale`` image pixels wide and tall, so the viewer
    can draw for the screen it is on and Kodi never has to stretch the text.
    """
    columns = max(1, int(columns))
    rows = max(1, int(rows))
    scale = _scale(scale)
    spread = _spread(scale)
    blank = [b"\x00" * (columns * scale)] * (GLYPH_HEIGHT * scale)
    scanlines = []
    for index in range(rows):
        line = lines[index] if index < len(lines) else ""
        if not line.strip():
            scanlines.extend(blank)
            continue
        glyphs = [_glyph(font, char) for char in line[:columns].ljust(columns)]
        for row in range(GLYPH_HEIGHT):
            packed = b"".join(spread[glyph[row]] for glyph in glyphs)
            scanlines.extend([packed] * scale)
    width = columns * GLYPH_WIDTH * scale
    height = rows * GLYPH_HEIGHT * scale
    return _png(width, height, 1, [(0, 0, 0), _rgb(color)], scanlines)


# The row of an 8x16 glyph its letters stand on.
_BASELINE = 12


def words_png(words, font, width, scale=2, gap=3):
    """One line of colored words as PNG bytes, ``width`` pixels wide.

    ``words`` are ``(text, color, small)``. A word is drawn at ``scale``; a
    ``small`` one at half that, on the same baseline, right after the word
    before it. Other words are ``gap`` character cells apart. Words that do
    not fit are left out.
    """
    scale = _scale(scale)
    height = GLYPH_HEIGHT * scale
    width = max(GLYPH_WIDTH, int(width))
    palette = [(0, 0, 0)]
    canvas = [bytearray(width) for _ in range(height)]
    cursor = 0
    for text, color, small in words:
        rgb = _rgb(color)
        if rgb not in palette:
            if len(palette) >= 255:
                break
            palette.append(rgb)
        ink = bytes((palette.index(rgb),))
        size = max(1, scale // 2) if small else scale
        top = _BASELINE * (scale - size)
        if cursor:
            cursor += GLYPH_WIDTH * size if small else gap * GLYPH_WIDTH * scale
        if cursor + len(text) * GLYPH_WIDTH * size > width:
            break
        for char in text:
            glyph = _glyph(font, char)
            for row in range(GLYPH_HEIGHT):
                bits = glyph[row]
                if not bits:
                    continue
                for column in range(GLYPH_WIDTH):
                    if bits & (0x80 >> column):
                        x = cursor + column * size
                        for dy in range(size):
                            line = canvas[top + row * size + dy]
                            line[x : x + size] = ink * size
            cursor += GLYPH_WIDTH * size
    return _png(width, height, 8, palette, [bytes(line) for line in canvas])


def check_mark(font):
    """The check mark the font has: U+2713, else the square-root sign."""
    for char in ("✓", "√"):
        if ord(char) in font:
            return char
    return "v"
