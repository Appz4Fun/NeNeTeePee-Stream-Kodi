#!/usr/bin/env python3
"""Build the viewer's terminal font from Terminus's 8x16 BDF.

Usage: python3 scripts/make_terminal_font.py path/to/ter-u16n.bdf

Writes repo/plugin.video.nzbdav/resources/fonts/terminal-8x16.bin (format
``NZF1``, see ``text_image.load_font``) with the characters NFO art uses:
ASCII, Latin-1, the old PC character set (CP437), the box and block
characters, a check mark and the replacement character. The font is
Terminus by Dimitar Toshkov Zhekov, under the SIL Open Font License 1.1
(resources/fonts/OFL.txt). The derived file does not carry the reserved
name.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "repo", "plugin.video.nzbdav"))

from resources.lib.text_image import pack_font  # noqa: E402

OUT = os.path.join(
    HERE, "..", "repo", "plugin.video.nzbdav", "resources", "fonts", "terminal-8x16.bin"
)


def wanted():
    points = set(range(0x20, 0x7F)) | set(range(0xA0, 0x100))
    points |= {ord(c) for c in bytes(range(0x80, 0x100)).decode("cp437")}
    points |= set(range(0x2500, 0x25A0))
    points |= {0x2713, 0xFFFD}
    return points


def read_bdf(path):
    glyphs = {}
    with open(path, encoding="latin-1") as handle:
        lines = iter(handle.read().splitlines())
    for line in lines:
        if not line.startswith("ENCODING "):
            continue
        code = int(line.split()[1])
        for line in lines:
            if line == "BITMAP":
                break
        rows = []
        for line in lines:
            if line == "ENDCHAR":
                break
            rows.append(int(line, 16) & 0xFF)
        if len(rows) == 16:
            glyphs[code] = bytes(rows)
    return glyphs


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    source = read_bdf(argv[1])
    glyphs = {code: source[code] for code in wanted() if code in source}
    with open(OUT, "wb") as handle:
        handle.write(pack_font(glyphs))
    print("{} glyphs -> {}".format(len(glyphs), os.path.normpath(OUT)))


if __name__ == "__main__":
    main(sys.argv)
