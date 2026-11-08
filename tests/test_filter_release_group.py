# SPDX-License-Identifier: GPL-3.0-or-later
"""Release-group detection where PTT reads the group wrong or not at all."""

import pytest
from resources.lib.filter import parse_title_metadata
from resources.lib.filter_tags import detect_release_group
from resources.lib.nzbget_fleet_dedup import same_release


@pytest.mark.parametrize(
    "title, group",
    [
        # A tag posted after the group.
        (
            "Six.Feet.Under.2001.S02E01.1080p.AMZN.WEB-DL.H265.SDR.DDP.5.1"
            ".English-HONE.REMASTERED",
            "HONE",
        ),
        ("Movie.2020.1080p.WEB-DL.x264-GRP.PROPER", "GRP"),
        ("Movie.2020.1080p.WEB-DL.DDP5.1.H.264-GRP.REPACK2", "GRP"),
        ("Show.S01E01.1080p.WEB.h264-GRP[rarbg]", "GRP"),
        # The parenthesized naming style.
        (
            "Six Feet Under (2001) S02E01 (1080p AMZN WEB-DL H265 SDR DDP 5.1 "
            "English - HONE) [REMASTERED]",
            "HONE",
        ),
        # A yEnc subject: the group is inside the quoted filename, not "1/3".
        (
            '[1/3] - "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1'
            '.H.264-ASiANLAD.mkv" yEnc  4400443301',
            "ASiANLAD",
        ),
        # Indexer and posting suffixes.
        ("Movie.2020.1080p.BluRay.x264-GRP-Obfuscated", "GRP"),
        ("Show.S01E01.720p.HDTV.x264-GRP-postbot", "GRP"),
        # Unchanged shapes.
        ("Show S01E01 1080p WEB-DL DDP5 1 H 264-FLUX", "FLUX"),
        ("Six.Feet.Under.S02E01.1080p.AMZN.WEB-DL.DDP2.0.H.264.1-KAIZEN", "KAIZEN"),
        ("Movie.2024.1080p.WEB-DL.x264-GROUP-NAME", "GROUP-NAME"),
        ("Movie.2020.1080p.BluRay.DTS.x264-D-Z0N3", "D-Z0N3"),
        ("Movie.2024.1080p.WEB-DL-GRP", "GRP"),
        ("Movie.2020.MULTi.1080p.WEB.x264-GRP.mkv", "GRP"),
        ("[SubsPlease] Show - 01 (1080p) [ABCD1234].mkv", "SubsPlease"),
        # No group: tags and title words are never read as one.
        ("Movie.2020.1080p.WEB-DL", ""),
        ("Movie.2024.1080p.WEB-DL.HE-AAC", ""),
        ("Movie 2020 - 1080p", ""),
        ("Spider-Man.2002.1080p", ""),
        ("Show - S01E01", ""),
        ("the-matrix-1999-1080p", ""),
    ],
)
def test_parse_title_metadata_reads_the_release_group(title, group):
    assert parse_title_metadata(title)["group"] == group


def test_detect_release_group_keeps_a_plausible_ptt_group():
    assert detect_release_group("Some Release Name", "GRP") == "GRP"


@pytest.mark.parametrize("parsed", ["1/3", "Obfuscated", "DL", "1080p", ""])
def test_detect_release_group_rejects_an_implausible_ptt_group(parsed):
    assert detect_release_group("Some Release Name", parsed) == ""


def test_both_hone_naming_styles_are_one_release():
    """Both names now parse group HONE, so the same-group gate accepts them."""
    dotted = {
        "title": "Six.Feet.Under.2001.S02E01.1080p.AMZN.WEB-DL.H265.SDR.DDP.5.1"
        ".English-HONE.REMASTERED",
        "size": "4140000000",
    }
    parenthesized = {
        "title": "Six Feet Under (2001) S02E01 (1080p AMZN WEB-DL H265 SDR DDP 5.1 "
        "English - HONE) [REMASTERED]",
        # A different size: no longer needs the WEB-DL same-size exception.
        "size": "4300000000",
    }
    assert same_release(dotted, parenthesized)
