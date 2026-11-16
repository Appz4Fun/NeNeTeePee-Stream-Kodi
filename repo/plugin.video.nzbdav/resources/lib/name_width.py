# SPDX-License-Identifier: GPL-3.0-or-later
"""How wide Kodi draws a name, near enough to end a line under it.

The picker strikes a replaced release's name through with a drawn line
(``results-dialog.xml``): Kodi's text markup has no strikethrough, and a
skin's font may not hold the combining stroke character. Kodi sizes no
control to the text of a list row and tells an add-on nothing about a font,
so the length of the line is worked out here.

The widths are the average of four sans-serif fonts Kodi skins use for
their list text (Noto Sans, Kodi's own Arial, Inter and one more), in
thousandths of the font size, for the printable ASCII characters. Fonts and skins
differ by a few percent, so the estimate leans long: a line a little past
the last letter reads as struck through, one that stops short does not.
"""

import math

# Width of chr(32) .. chr(126), in thousandths of the font size.
_ASCII_WIDTHS = (
    271, 312, 444, 682, 615, 932, 725, 259, 345, 345, 513, 671, 289, 407, 289,
    374, 613, 557, 607, 609, 617, 607, 610, 596, 610, 610, 294, 297, 671, 671,
    671, 515, 989, 680, 669, 695, 739, 607, 576, 750, 751, 299, 438, 657, 563,
    892, 757, 780, 627, 780, 661, 616, 622, 738, 669, 974, 668, 631, 629, 364,
    374, 364, 623, 475, 422, 570, 618, 537, 618, 581, 355, 619, 608, 259, 259,
    555, 259, 912, 608, 602, 618, 618, 395, 508, 361, 606, 554, 800, 556, 554,
    515, 463, 387, 463, 671,
)  # fmt: skip
# Any other letter (accented Latin, Cyrillic, Greek), and the full-width
# scripts from the CJK radicals on.
_OTHER_WIDTH = 600
_WIDE_WIDTH = 1000
_WIDE_FROM = 0x2E80
# The size skins give ``font13``, the picker's name font, in the picker's
# 1080-line coordinates.
_NAME_FONT_SIZE = 30
# A skin that draws its list font in Kodi's "lighten" style (Estuary and
# many others) takes about a pixel off every letter; a plain one does not.
# Measured on Kodi 21, a name comes out between 0.90 and 0.98 of the table's
# width across those skins and fonts. The scale sits toward the long end.
_SCALE = 0.955


def _char_width(char):
    code = ord(char)
    if 32 <= code <= 126:
        return _ASCII_WIDTHS[code - 32]
    if code < 32:
        return 0
    return _WIDE_WIDTH if code >= _WIDE_FROM else _OTHER_WIDTH


def name_width(text, font_size=_NAME_FONT_SIZE):
    """The estimated drawn width of ``text``, in skin coordinates."""
    total = sum(_char_width(char) for char in str(text or ""))
    return total * font_size * _SCALE / 1000.0


def strike_percent(text, column_width):
    """How much of a ``column_width`` wide name column ``text`` covers, as a
    whole percent from 1 to 100. A name too long for the column is cut off
    at its end, so its line runs the whole way."""
    if column_width <= 0:
        return 100
    percent = int(math.ceil(100.0 * name_width(text) / column_width))
    return max(1, min(100, percent))
