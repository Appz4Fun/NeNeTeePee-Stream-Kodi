# SPDX-License-Identifier: GPL-3.0-or-later
"""The estimated drawn width of a picker name (the strikethrough's length)."""

import pytest
from resources.lib.name_width import name_width, strike_percent

# Widths Kodi 21 drew these names at, in font13, measured on screenshots:
# Estuary's default font set (Noto Sans, "lighten") and its Arial set.
_MEASURED = (
    ("Test Movie (2024) [WEB-DL-1080p][DD+ 5.1][H264]-GRPB", 748, 791),
    ("Test Movie (2024) [HDTV-720p][x264]-GRPF", 567, 596),
)


@pytest.mark.parametrize("name,narrow,wide", _MEASURED)
def test_the_estimate_reaches_the_end_of_the_name_and_little_further(
    name, narrow, wide
):
    width = name_width(name)

    assert width >= wide
    assert width <= narrow * 1.09


def test_a_longer_name_is_wider():
    assert name_width("abcdef") > name_width("abc") > name_width("") == 0


def test_letters_outside_ascii_have_a_width():
    assert name_width("é") > 0
    assert name_width("映画") > name_width("ab")
    assert name_width("\n") == 0
    # A combining accent sits on its letter and adds no width.
    assert name_width("e\u0301") == name_width("e")


def test_strike_percent_is_a_share_of_the_column():
    name = _MEASURED[0][0]

    assert strike_percent(name, 1140) < strike_percent(name, 860) < 100
    assert strike_percent(name, 1140) == pytest.approx(71, abs=2)


def test_strike_percent_stays_between_1_and_100():
    assert strike_percent("", 860) == 1
    assert strike_percent("x" * 400, 860) == 100
    assert strike_percent("name", 0) == 100
