# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio parsing, the four audio tiers, and the pinned release groups."""

import pytest
from resources.lib import filter as filters
from resources.lib.filter_audio import audio_label, audio_tier
from resources.lib.filter_tags import (
    detect_audio,
    detect_channels,
    main_track_channels,
)


def _name(tags, group="GRP"):
    return "Movie.2023.{}-{}".format(tags, group)


@pytest.mark.parametrize(
    "tags,audio",
    [
        # Dolby Digital Plus in every spelling, the mangled ones included.
        ("1080p.AMZN.WEB-DL.DDP5.1.H.264", ["DD+"]),
        ("1080p.AMZN.WEB-DL.DD+5.1.H.264", ["DD+"]),
        ("1080p.AMZN.WEB-DL.DD+.5.1.H.264", ["DD+"]),
        ("1080p.AMZN.WEB-DL.EAC3.5.1.H.264", ["DD+"]),
        ("1080p.AMZN.WEB-DL.DD_5.1.H.264", ["DD+"]),  # "+" turned into "_"
        ("1080p.AMZN.WEB-DL.DD 5.1.H.264", ["DD+"]),  # "+" URL-decoded to " "
        ("1080p.AMZN.WEB-DL.DD_2.0.H.264", ["DD+"]),
        # Atmos rides on Dolby Digital Plus, never on plain Dolby Digital.
        ("2160p.NF.WEB-DL.DDPA5.1.DV.H.265", ["Atmos", "DD+"]),
        ("2160p.NF.WEB-DL.DD+A5.1.DV.H.265", ["Atmos", "DD+"]),
        ("2160p.NF.WEB-DL.DDP5.1.Atmos.H.265", ["Atmos", "DD+"]),
        ("2160p.NF.WEB-DL.DD5.1.Atmos.H.265", ["Atmos", "DD+"]),
        ("2160p.NF.WEB-DL.DD.Atmos.5.1.H.265", ["Atmos", "DD+"]),
        ("2160p.NF.WEB-DL.Atmos.AC3.5.1.H.265", ["Atmos", "DD+"]),
        # TrueHD is the Atmos bed when it is there; a DD track beside it is
        # the plain compatibility track.
        ("2160p.UHD.BluRay.REMUX.HEVC.TrueHD.7.1.Atmos", ["Atmos", "TrueHD"]),
        ("2160p.UHD.BluRay.REMUX.TrueHD.Atmos.7.1.DD5.1", ["Atmos", "TrueHD", "DD"]),
        ("1080p.WEB-DL.DDP5.1.Atmos.DD2.0.H.264", ["Atmos", "DD+", "DD"]),
        ("1080p.WEB-DL.DD2.0.EAC3.5.1.Atmos.H.264", ["Atmos", "DD+", "DD"]),
        # Plain Dolby Digital stays Dolby Digital.
        ("1080p.BluRay.DD5.1.x264", ["DD"]),
        ("1080p.BluRay.DD.5.1.x264", ["DD"]),
        ("1080p.BluRay.AC3.x264", ["DD"]),
        ("1080p.BluRay.FLAC.2.0.x264", ["FLAC"]),
        ("1080p.BluRay.FLAC1.0.x264", ["FLAC"]),
        ("1080p.WEB-DL.Opus.5.1.AV1", ["OPUS"]),
        ("1080p.WEB-DL.OPUS2.0.AV1", ["OPUS"]),
        ("2160p.UHD.BluRay.REMUX.DTS-X.7.1.HEVC", ["DTS:X"]),
        ("2160p.UHD.BluRay.REMUX.DTS-HD.MA.7.1.HEVC", ["DTS-HD MA"]),
    ],
)
def test_audio_is_parsed_from_the_name(tags, audio):
    assert filters.parse_title_metadata(_name(tags))["audio"] == audio


@pytest.mark.parametrize(
    "name",
    [
        # The name separates its words with the character after "DD", so
        # that character is no lost "+".
        "Movie 2023 1080p BluRay DD 5.1 x264-GRP",
        "Movie_2023_1080p_BluRay_DD_5.1_x264-GRP",
    ],
)
def test_dd_followed_by_the_names_own_separator_stays_dolby_digital(name):
    assert detect_audio(name) == ["DD"]


@pytest.mark.parametrize(
    "tags,channels",
    [
        ("1080p.BluRay.FLAC.1.0.x264", "1.0"),
        ("1080p.BluRay.FLAC.2.0.x264", "2.0"),
        ("2160p.REMUX.TrueHD.Atmos.7.1.HEVC", "7.1"),
        ("1080p.WEB-DL.DD_5.1.H.264", "5.1"),
        ("1080p.WEB-DL.Opus.5.1.AV1", "5.1"),
        ("1080p.BluRay.DTS.x264", ""),
    ],
)
def test_channels_are_read_after_the_audio_tag(tags, channels):
    assert filters.parse_title_metadata(_name(tags))["channels"] == channels


def test_detect_channels_ignores_numbers_that_follow_no_audio_tag():
    assert not detect_channels("Movie.2.0.2023.1080p.BluRay.x264-GRP")
    assert not detect_channels("Movie.2023.1080p.BluRay.AC3.10bit.x264-GRP")
    assert detect_channels("Movie.2023.TrueHD.7.1.Atmos.DD5.1.AC3.5.1-GRP") == [
        "7.1",
        "5.1",
    ]


@pytest.mark.parametrize(
    "tags,channels",
    [
        # The count by the main track's tag, wherever that track is named.
        ("2160p.BluRay.REMUX.TrueHD.7.1.Atmos.DD5.1", "7.1"),
        ("2160p.BluRay.REMUX.DD5.1.TrueHD.7.1.Atmos", "7.1"),
        ("2160p.BluRay.REMUX.TrueHD.Atmos.7.1.DD.5.1", "7.1"),
        ("1080p.BluRay.REMUX.DD.2.0.DTS-HD.MA.5.1", "5.1"),
        ("1080p.BluRay.DD5.1.FLAC.2.0.x264", "2.0"),
        ("2160p.NF.WEB-DL.DDPA5.1.H.265", "5.1"),
        ("2160p.NF.WEB-DL.DD5.1.Atmos.H.265", "5.1"),
        # No count by the main track: the only count, else none.
        ("2160p.BluRay.REMUX.TrueHD.Atmos.DD5.1", "5.1"),
        ("2160p.BluRay.REMUX.TrueHD.DD5.1.AAC2.0", ""),
        # No count by any audio tag: what PTT read.
        ("1080p.BluRay.DTS.x264.5.1", "5.1"),
    ],
)
def test_channels_belong_to_the_main_track(tags, channels):
    assert filters.parse_title_metadata(_name(tags))["channels"] == channels


def test_main_track_channels_falls_back_to_the_parsed_value():
    name = "Movie.2023.1080p.BluRay.AAC.x264-GRP"
    assert main_track_channels(name, ["AAC"], "BluRay", "stereo") == "stereo"
    assert main_track_channels(name, [], "BluRay", "") == ""


@pytest.mark.parametrize(
    "audio,quality,tier",
    [
        (["Atmos", "TrueHD"], "BluRay REMUX", 0),
        (["TrueHD"], "BluRay", 0),
        (["FLAC"], "BluRay", 0),
        (["PCM"], "BluRay", 0),
        (["DTS-HD MA"], "BluRay", 0),
        (["DTS:X", "DTS-HD MA"], "BluRay REMUX", 0),
        (["DTS:X"], "BluRay REMUX", 0),  # a disc's DTS:X sits on DTS-HD MA
        (["DTS:X"], "", 0),
        (["DTS:X", "DTS-HD MA"], "WEB-DL", 0),
        (["DTS:X"], "WEB-DL", 1),  # the lossy streaming kind
        (["Atmos", "DD+"], "WEB-DL", 1),
        (["Atmos"], "WEB-DL", 1),
        (["DD+"], "WEB-DL", 2),
        (["DD"], "BluRay", 2),
        (["DTS"], "BluRay", 2),
        (["DTS-HD HR"], "BluRay", 2),
        (["AAC"], "WEB-DL", 3),
        (["OPUS"], "WEB-DL", 3),
        (["MP3"], "", 3),
        (["DD", "FLAC"], "BluRay", 0),  # the best track counts
        ([], "WEB-DL", 4),
    ],
)
def test_audio_tier(audio, quality, tier):
    assert audio_tier(audio, quality) == tier


@pytest.mark.parametrize(
    "audio,channels,quality,label",
    [
        (["Atmos", "TrueHD"], "7.1", "BluRay REMUX", "TrueHD Atmos 7.1"),
        (["Atmos", "TrueHD", "DD"], "7.1", "BluRay REMUX", "TrueHD Atmos 7.1"),
        (["Atmos", "DD+"], "5.1", "WEB-DL", "DD+ Atmos 5.1"),
        (["DD+"], "5.1", "WEB-DL", "DD+ 5.1"),
        (["DD"], "5.1", "BluRay", "DD 5.1"),
        (["DTS:X", "DTS-HD MA"], "7.1", "BluRay REMUX", "DTS:X 7.1"),
        (["DTS-HD MA"], "5.1", "BluRay", "DTS-HD MA 5.1"),
        (["FLAC"], "stereo", "BluRay", "FLAC 2.0"),
        (["FLAC"], "mono", "BluRay", "FLAC 1.0"),
        (["OPUS"], "5.1", "WEB-DL", "Opus 5.1"),
        (["Atmos"], "", "WEB-DL", "Atmos"),
        (["AAC"], "", "WEB-DL", "AAC"),
        ([], "5.1", "WEB-DL", ""),
    ],
)
def test_audio_label(audio, channels, quality, label):
    assert audio_label(audio, channels, quality) == label


def _sorted_titles(titles):
    settings = filters._get_filter_settings(lambda key, default="": default)
    rows = [
        {
            "title": title,
            "size": "5000000000",
            "_meta": filters.parse_title_metadata(title),
        }
        for title in titles
    ]
    return [row["title"] for row in filters._sort_results(rows, settings)]


def test_relevance_ranks_the_four_audio_tiers_in_order():
    titles = [
        "Movie.2023.1080p.WEB-DL.H.264-GRP",  # no audio tag
        "Movie.2023.1080p.WEB-DL.AAC2.0.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.Opus.5.1.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.DD5.1.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.DTS.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.DTS-X.7.1.H.264-GRP",  # lossy DTS:X
        "Movie.2023.1080p.WEB-DL.DDP5.1.Atmos.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.FLAC.2.0.H.264-GRP",
        "Movie.2023.1080p.WEB-DL.TrueHD.7.1.Atmos.H.264-GRP",
    ]
    ranked = _sorted_titles(titles)
    tiers = [
        audio_tier(filters.parse_title_metadata(title)["audio"], "WEB-DL")
        for title in ranked
    ]
    assert tiers == [0, 0, 1, 1, 2, 2, 3, 3, 4]


def test_flac_ranks_beside_truehd_atmos_and_above_dd_plus_atmos():
    flac = "Movie.2023.1080p.BluRay.FLAC.2.0.x264-GRP"
    truehd = "Movie.2023.1080p.BluRay.TrueHD.7.1.Atmos.x264-GRP"
    ddp_atmos = "Movie.2023.1080p.BluRay.DDP5.1.Atmos.x264-GRP"
    # Equal tier and size: the input order is kept, either way round.
    assert _sorted_titles([ddp_atmos, flac, truehd]) == [flac, truehd, ddp_atmos]
    assert _sorted_titles([ddp_atmos, truehd, flac]) == [truehd, flac, ddp_atmos]


def test_cinephiles_then_framestor_lead_their_resolution():
    titles = [
        "Movie.2023.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1.Atmos-BMF",
        "Movie.2023.Hybrid.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1.Atmos-3L",
        "Movie.2023.2160p.UHD.BluRay.REMUX.HEVC.DTS-HD.MA.5.1-FraMeSToR",
        "Movie.2023.2160p.BluRay.x265.DD5.1-CiNEPHiLES",
    ]
    assert _sorted_titles(titles) == list(reversed(titles))


def test_the_pinned_groups_match_in_any_letter_case():
    titles = [
        "Movie.2023.2160p.WEB-DL.DV.HEVC.DDP5.1.Atmos-FLUX",
        "Movie.2023.2160p.BluRay.x265.DD5.1-FRAMESTOR",
        "Movie.2023.2160p.BluRay.x265.DD5.1-cinephiles",
    ]
    assert _sorted_titles(titles) == list(reversed(titles))


def test_a_pinned_group_does_not_outrank_a_higher_resolution():
    low = "Movie.2023.1080p.BluRay.REMUX.AVC.FLAC.2.0-CiNEPHiLES"
    high = "Movie.2023.2160p.WEB-DL.H.265.AAC2.0-GRP"
    assert _sorted_titles([low, high]) == [high, low]


def test_a_group_that_only_contains_a_pinned_name_is_not_pinned():
    titles = [
        "Movie.2023.2160p.BluRay.x265.DD5.1-NotFraMeSToR",
        "Movie.2023.2160p.WEB-DL.DV.HEVC.DDP5.1.Atmos-FLUX",
    ]
    assert _sorted_titles(titles) == list(reversed(titles))


_ROP = "The.Lord.of.the.Rings.The.Rings.of.Power.S03E02.The.Gift.of.Men."


def test_atmos_from_an_unlisted_group_ranks_above_plain_dd_plus_from_a_preferred_one():
    # Seen on a real search: NTb is in a preferred tier, playWEB is in none,
    # and NTb's plain DD+ led playWEB's DD+ Atmos. Audio is compared first.
    ntb = _ROP + "2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-NTb"
    playweb = _ROP + "2160p.AMZN.WEB-DL.DD+5.1.Atmos.DoVi.H.265-playWEB"
    tiers = filters.compile_remux_tiers(filters.DEFAULT_REMUX_GROUPS)
    assert filters.remux_group_rank(
        {"_meta": {"group": "NTb"}}, tiers
    ) < filters.remux_group_rank({"_meta": {"group": "playWEB"}}, tiers)
    assert _sorted_titles([ntb, playweb]) == [playweb, ntb]


def test_the_preferred_group_still_leads_among_releases_with_the_same_audio():
    ntb = _ROP + "2160p.AMZN.WEB-DL.DDP5.1.Atmos.DV.H.265-NTb"
    playweb = _ROP + "2160p.AMZN.WEB-DL.DD+5.1.Atmos.DoVi.H.265-playWEB"
    assert _sorted_titles([playweb, ntb]) == [ntb, playweb]
    # DD+ and plain DD are one tier, so the group decides there too.
    plain_ntb = _ROP + "2160p.AMZN.WEB-DL.DD5.1.DV.H.265-NTb"
    plain_playweb = _ROP + "2160p.AMZN.WEB-DL.DDP5.1.DoVi.H.265-playWEB"
    assert _sorted_titles([plain_playweb, plain_ntb]) == [plain_ntb, plain_playweb]


def test_audio_does_not_outrank_hdr_or_the_pinned_groups():
    # HDR is still compared before audio: Dolby Vision with plain DD+ leads
    # HDR10 with Atmos.
    vision = _ROP + "2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-GRP"
    atmos_hdr = _ROP + "2160p.AMZN.WEB-DL.DDP5.1.Atmos.HDR.H.265-GRP"
    assert _sorted_titles([atmos_hdr, vision]) == [vision, atmos_hdr]
    # CiNEPHiLES and FraMeSToR keep the top of their resolution whatever the
    # audio of the releases around them.
    pinned = "Movie.2023.2160p.UHD.BluRay.REMUX.HEVC.DTS-HD.HR.5.1-CiNEPHiLES"
    second = "Movie.2023.2160p.UHD.BluRay.REMUX.HEVC.DD.5.1-FraMeSToR"
    lossless = "Movie.2023.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1.Atmos-GRP"
    assert _sorted_titles([lossless, second, pinned]) == [pinned, second, lossless]
