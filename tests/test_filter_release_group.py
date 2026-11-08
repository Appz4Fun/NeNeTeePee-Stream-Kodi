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
        # A hyphenated audio tag before the group is not part of it.
        ("Movie.2020.1080p.BluRay.x264.DTS-HD-MA-FGT", "FGT"),
        ("Movie.2020.1080p.BluRay.x264.DTS-X-FGT", "FGT"),
        ("Movie.2020.1080p.BluRay.x264.DTS-HD-MA", ""),
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


_HONE_PARENTHESIZED = (
    "Six Feet Under (2001) S02E01 (1080p AMZN WEB-DL H265 SDR DDP 5.1 English - "
    "HONE) [REMASTERED]"
)

_SUBJECT_BASES = [
    "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1.H.264-ASiANLAD",
    _HONE_PARENTHESIZED,
    "The.Matrix.1999.2160p.UHD.BluRay.REMUX.DV.HDR10.HEVC.TrueHD.7.1.Atmos-FGT",
    "Breaking.Bad.S05.1080p.BluRay.x264-ROVERS",
    "The.Office.US.S03E01E02.1080p.WEB-DL.DD5.1.H.264-NTb",
    "Band.of.Brothers.Part.1.Currahee.2001.1080p.BluRay.x264-HDMaNiAcS",
    "[SubsPlease] Sousou no Frieren - 28 (1080p) [A1B2C3D4]",
    "Movie.Title.2020.1080p.WEB-DL.H.264",
]

_SUBJECT_SHAPES = [
    '[1/3] - "{name}.mkv" yEnc  4400443301',
    '[01/50] - "{name}.part01.rar" yEnc (1/123)',
    '[23/50] - "{name}.vol031+32.par2" yEnc (1/88)',
    '"{name}.mkv" yEnc (1/4521)',
    '(7/50) "{name}.r07" - 4.68 GB - yEnc',
    '{name} [01/50] - "{name}.part01.rar" yEnc',
    '[PRiVATE]-[WtFnZb]-[{name}]-[1/7] - "" yEnc (1/45)',
    '[PRiVATE]-[WtFnZb]-[{name}.mkv]-[1/7] - "a8f3c9e1b2d4f6a7c9e1.mkv" yEnc',
    '[12/77] - "{name}.7z.012" yEnc',
    '"{name}.001" yEnc (1/200)',
    '[1 of 3] - "{name}.mkv" yEnc',
    "{name} (1/45)",
]


def _parsed_fields(title):
    from resources.lib import fallback_streams as fs

    meta = parse_title_metadata(title)
    keys = ("resolution", "hdr", "audio", "channels", "codec", "quality", "group")
    fields = {key: meta.get(key) for key in keys}
    fields["identity"] = fs._release_identity({"title": title})
    return fields


@pytest.mark.parametrize("shape", _SUBJECT_SHAPES)
@pytest.mark.parametrize("name", _SUBJECT_BASES)
def test_a_usenet_subject_parses_like_its_release_name(name, shape):
    """Every field, not only the group, comes from the name inside the subject."""
    assert _parsed_fields(shape.format(name=name)) == _parsed_fields(name)


@pytest.mark.parametrize("name", _SUBJECT_BASES)
def test_release_name_from_subject_leaves_a_release_name_alone(name):
    from resources.lib.release_subject import release_name_from_subject

    assert release_name_from_subject(name) == name


def test_a_yenc_backup_is_the_same_release_as_its_named_listing():
    subject = {
        "title": '[1/3] - "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1'
        '.H.264-ASiANLAD.mkv" yEnc  4400443301',
        "size": "4960000000",
    }
    named = {
        "title": "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1"
        ".H.264-ASiANLAD",
        "size": "5000000000",
    }
    assert same_release(named, subject)


def test_a_full_name_outside_the_quotes_beats_a_short_archive_name():
    from resources.lib.release_subject import release_name_from_subject

    subject = (
        'Movie.Title.2020.1080p.BluRay.x264-GRP [01/50] - "Movie.Title.2020.part01.rar"'
        " yEnc"
    )
    name = "Movie.Title.2020.1080p.BluRay.x264-GRP"
    assert release_name_from_subject(subject) == name
