# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""One release listed under several spellings is one release.

Live searches listed the same release in separate picker rows because its name
came back with different punctuation, and listed different things in one row
because only the name was compared.
"""

from unittest.mock import MagicMock, patch

import pytest
from resources.lib import filter as flt
from resources.lib.fallback_streams import _release_identity
from resources.lib.filter import drop_other_years, parse_title_metadata
from resources.lib.release_match import same_release
from resources.lib.results_groups import release_groups


def _row(title, size="5000000000", link=None):
    return {
        "title": title,
        "link": link or "http://example.com/{}/{}".format(abs(hash(title)), size),
        "size": str(size),
        "indexer": "test",
        "pubdate": "",
        "age": "1 day",
    }


def _defaults(_key, default=""):
    return default


def _groups(titles):
    rows = [_row(title) for title in titles]
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    return release_groups(filtered)


def _titles(rows):
    return [row["title"] for row in rows]


# --- spellings of one name ---------------------------------------------------


@pytest.mark.parametrize(
    "first, second",
    [
        # a dot where the hyphen before the group should be
        (
            "Severance.2022.S02E01.2160p.ATVP.WEB-DL.H265.DV.DDP.5.1.English-HONE",
            "Severance.2022.S02E01.2160p.ATVP.WEB-DL.H265.DV.DDP.5.1.English.HONE",
        ),
        (
            "Severance.2022.S02E01.1080p.ATVP.Webrip.DV.DDP5.1.x265-Vialle",
            "Severance.2022.S02E01.1080p.ATVP.Webrip.DV.DDP5.1.x265.Vialle",
        ),
        # the parenthesized style, spaced and dotted
        (
            "House of the Dragon (2022) S02E01 (2160p MAX WEB-DL H265 DV DDP 5.1"
            " English - HONE)",
            "House.of.the.Dragon.(2022).S02E01.(2160p.MAX.WEB-DL.H265.DV.DDP.5.1"
            ".English.-.HONE)",
        ),
        # a plus sign the indexer replaced
        (
            "Dune.Part.Two.2024.1080p.BluRay.DD+7.1.x264-SPHD",
            "Dune.Part.Two.2024.1080p.BluRay.DD_7.1.x264-SPHD",
        ),
        (
            "Dune.Part.Two.2024.BluRay.2160p.UHD.TrueHD.Atmos.7.1.DV.HDR10+.x265-ZAX",
            "Dune Part Two 2024 BluRay 2160p UHD TrueHD Atmos 7 1 DV HDR10 x265-ZAX",
        ),
        # underscores between words
        (
            "Dune.Part.Two.2024.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HEVC.REMUX"
            "-FraMeSToR",
            "Dune_Part_Two_2024_UHD_BluRay_2160p_TrueHD_Atmos_7_1_DV_HEVC_REMUX"
            "-FraMeSToR",
        ),
        # "br remux" spaced and dotted
        (
            "Dune-Part Two 2024 2160p UHD br remux dv hdr hevc-d3g",
            "Dune-Part.Two.2024.2160p.UHD.br.remux.dv.hdr.hevc-d3g",
        ),
        # anime: the dotted form hides the episode number from the parser
        (
            "[GbR] Sousou no Frieren - 05 [2160p H.265]",
            "[GbR].Sousou.no.Frieren-05.[2160p.H.265]",
        ),
        # an apostrophe the indexer dropped
        (
            "Frieren.Beyond.Journey's.End.S01E05.1080p.FLAC.2.0.AVC.REMUX-FraMeSToR",
            "Frieren.Beyond.Journeys.End.S01E05.1080p.FLAC.2.0.AVC.REMUX-FraMeSToR",
        ),
        # a trailing semicolon on the listing
        (
            "The.Sopranos.S02E01.1080p.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR",
            "The.Sopranos.S02E01.1080p.DTS-HD.MA.5.1.AVC.REMUX-FraMeSToR;",
        ),
    ],
)
def test_two_spellings_of_a_name_are_one_picker_row(first, second):
    groups = _groups([first, second])
    assert len(groups) == 1
    assert sorted(_titles(groups[0])) == sorted([first, second])


def test_every_spelling_takes_the_most_detailed_parse():
    hyphen = _row("Show.S01E01.1080p.WEB-DL.DD+5.1.H.264-GRP")
    lost = _row("Show S01E01 1080p WEB-DL DD 5 1 H 264 GRP")
    flt.filter_results([lost, hyphen], settings_getter=_defaults)
    assert lost["_meta"]["group"] == hyphen["_meta"]["group"] == "GRP"
    assert lost["_meta"]["audio"] == hyphen["_meta"]["audio"]
    assert _release_identity(lost) == _release_identity(hyphen)


def test_a_spelling_keeps_its_own_container():
    """A .mkv and a .mp4 of one name are different files."""
    mkv = _row('[1/3] - "Show.S01E01.1080p.WEB-DL.H.264-GRP.mkv" yEnc 100')
    mp4 = _row('[1/3] - "Show S01E01 1080p WEB-DL H 264 GRP.mp4" yEnc 100')
    flt.filter_results([mkv, mp4], settings_getter=_defaults)
    assert mkv["_meta"]["container"] != mp4["_meta"]["container"]
    assert not same_release(mkv, mp4)


def test_unifying_twice_changes_nothing():
    rows = [
        _row("Show.S01E01.1080p.WEB-DL.DD+5.1.H.264-GRP"),
        _row("Show S01E01 1080p WEB-DL DD 5 1 H 264 GRP"),
    ]
    flt.filter_results(rows, settings_getter=_defaults)
    first = [dict(row["_meta"]) for row in rows]
    flt.filter_results(rows, settings_getter=_defaults)
    assert [dict(row["_meta"]) for row in rows] == first


def test_names_with_different_words_are_not_unified():
    first = _row("Show.S01E01.1080p.WEB-DL.DD5.1.H.264-GRP")
    second = _row("Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP")
    flt.filter_results([first, second], settings_getter=_defaults)
    assert first["_meta"]["audio"] != second["_meta"]["audio"]


# --- single parser fixes -----------------------------------------------------


def test_part_number_is_read_next_to_underscores():
    dotted = {"title": "Dune.Part.Two.2024.2160p.BluRay.REMUX-GRP"}
    underscored = {"title": "Dune_Part_Two_2024_2160p_BluRay_REMUX-GRP"}
    assert _release_identity(dotted)[4] == _release_identity(underscored)[4] == 2


def test_trailing_semicolon_is_not_part_of_the_group():
    assert parse_title_metadata("Movie.2020.1080p.BluRay.x264-DON;")["group"] == "DON"


@pytest.mark.parametrize(
    "title, plus",
    [
        ("Movie.2020.1080p.UHD.BluRay.DoVi.HDR10_.x265-GRP", True),
        ("Movie.2020.1080p.UHD.BluRay.DoVi.HDR10+.x265-GRP", True),
        ("Movie_2020_1080p_UHD_BluRay_DoVi_HDR10_x265-GRP", False),
        ("Movie.2020.1080p.UHD.BluRay.DoVi.HDR10.x265-GRP", False),
    ],
)
def test_hdr10_with_a_replaced_plus_sign(title, plus):
    assert ("HDR10+" in parse_title_metadata(title)["hdr"]) is plus


def test_a_v2_re_release_is_not_a_copy_of_the_first():
    groups = _groups(
        [
            "[Yameii] Frieren - S01E05 [English Dub] [CR WEB-DL 1080p] [536EA39C]",
            "[Yameii] Frieren - S01E05 v2 [English Dub] [CR WEB-DL 1080p] [45586A01]",
        ]
    )
    assert len(groups) == 2


# --- different works from one group -------------------------------------------


@pytest.mark.parametrize(
    "first, second",
    [
        (
            "The.Sopranos.S02E01.Guy.Walks.Into.a.Psychiatrists.Office.720p.HEVC"
            ".x265-MeGusta",
            "The.Office.US.S02E01.720p.HEVC.x265-MeGusta",
        ),
        (
            "House.S02E01.720p.HEVC.x265-MeGusta",
            "House.of.the.Dragon.S02E01.720p.HEVC.x265-MeGusta",
        ),
        (
            "Oppenheimer.2023.1080p.BluRay.x264-OFT",
            "The.Making.of.Hanger.1080p.BluRay.x264-OFT",
        ),
    ],
)
def test_different_titles_from_one_group_are_not_one_release(first, second):
    left = {"title": first, "_meta": parse_title_metadata(first)}
    right = {"title": second, "_meta": parse_title_metadata(second)}
    assert not same_release(left, right)
    assert not same_release(right, left)
    assert len(_groups([first, second])) == 2


# --- oddly sized copies --------------------------------------------------------

EPISODE = "House.of.the.Dragon.S02E01.2160p.MAX.WEB-DL.DDP5.1.DoVi.x265-NTb"
GB = 1000000000


def _sized(sizes, title=EPISODE):
    return [
        _row(title, size=size, link="http://example.com/{}".format(n))
        for n, size in enumerate(sizes)
    ]


def _ordered_sizes(rows):
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    return [int(row["size"] or 0) // GB for row in filtered], filtered


def test_an_oversized_copy_no_longer_leads_its_release():
    """A 91 GB listing of a 10 GB episode sorted first and was what played."""
    sizes, filtered = _ordered_sizes(_sized([91 * GB, 10 * GB, 10 * GB, 11 * GB]))
    assert sizes == [11, 10, 10, 91]
    assert [bool(row.get("_size_outlier")) for row in filtered] == [
        False,
        False,
        False,
        True,
    ]
    assert int(release_groups(filtered)[0][0]["size"]) == 11 * GB


def test_an_undersized_copy_is_marked_and_stays_below():
    sizes, filtered = _ordered_sizes(_sized([10 * GB, 10 * GB, 11 * GB, GB // 20]))
    assert sizes[-1] == 0
    assert filtered[-1].get("_size_outlier") is True


def test_ordinary_size_differences_are_left_alone():
    """Posters add different amounts of repair data."""
    sizes, filtered = _ordered_sizes(_sized([12 * GB, 11 * GB, 10 * GB, 10 * GB]))
    assert sizes == [12, 11, 10, 10]
    assert not any(row.get("_size_outlier") for row in filtered)


def test_two_copies_are_too_few_to_call_one_odd():
    sizes, filtered = _ordered_sizes(_sized([91 * GB, 10 * GB]))
    assert sizes == [91, 10]
    assert not any(row.get("_size_outlier") for row in filtered)


def test_a_copy_with_no_size_is_never_odd():
    rows = _sized([10 * GB, 10 * GB, 11 * GB]) + [_row(EPISODE, size="", link="x")]
    _sizes, filtered = _ordered_sizes(rows)
    assert not any(row.get("_size_outlier") for row in filtered)


def test_another_release_between_the_copies_keeps_its_place():
    other = "House.of.the.Dragon.S02E01.2160p.MAX.WEB-DL.DDP5.1.DoVi.x265-FLUX"
    rows = _sized([91 * GB, 10 * GB, 10 * GB, 10 * GB]) + [_row(other, size=50 * GB)]
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    ntb = [row for row in filtered if row["title"] == EPISODE]
    assert int(ntb[-1]["size"]) == 91 * GB
    assert [row["title"] for row in filtered].count(other) == 1


def test_size_sort_is_not_reordered():
    def size_sort(key, default=""):
        return "1" if key == "sort_order" else default

    rows = _sized([91 * GB, 10 * GB, 10 * GB, 11 * GB])
    filtered, _all = flt.filter_results(rows, settings_getter=size_sort)
    assert [int(row["size"]) // GB for row in filtered] == [91, 11, 10, 10]


def test_an_odd_copy_is_not_a_backup_of_a_normal_one():
    from resources.lib.router_play import _same_release_backups

    _sizes, filtered = _ordered_sizes(_sized([91 * GB, 10 * GB, 10 * GB, 11 * GB]))
    pick = filtered[0]
    backups = _same_release_backups(pick, filtered)
    assert len(backups) == 2
    odd = filtered[-1]
    assert odd["link"] not in [backup["link"] for backup in backups]
    # Picking the odd copy on purpose still gets the others as backups.
    assert len(_same_release_backups(odd, filtered)) == 3


# --- movie searches: other years ----------------------------------------------


def test_a_movie_dated_for_another_year_is_dropped():
    rows = [
        _row("Avatar.Fire.and.Ash.2025.2160p.UHD.BluRay.REMUX-FraMeSToR"),
        _row("Avatar.The.Way.of.Water.2022.2160p.UHD.BluRay.REMUX-FraMeSToR"),
        _row("Avatar.2009.2160p.UHD.BluRay.REMUX-BiTOR"),
    ]
    assert _titles(drop_other_years(rows, "2009")) == [
        "Avatar.2009.2160p.UHD.BluRay.REMUX-BiTOR"
    ]


@pytest.mark.parametrize(
    "title, year",
    [
        ("Avatar.2010.1080p.BluRay.x264-GRP", "2009"),  # a year either side
        ("Avatar.1080p.BluRay.x264-GRP", "2009"),  # no year at all
        ("2001.A.Space.Odyssey.1968.1080p.BluRay.x264-GRP", "1968"),
        ("Blade.Runner.2049.2017.1080p.BluRay.x264-GRP", "2017"),
        ("Avatar.S01E01.2005.1080p.WEB-DL.x264-GRP", "2009"),  # not a movie row
    ],
)
def test_rows_that_may_be_the_movie_are_kept(title, year):
    rows = [_row(title)]
    assert drop_other_years(rows, year) is rows


def test_a_sequel_named_with_a_number_is_dropped_by_its_own_year():
    rows = [_row("Blade.Runner.2049.2017.1080p.BluRay.x264-GRP")]
    assert drop_other_years(rows, "1982") == []


@pytest.mark.parametrize("year", [None, "", "_", "abcd", "20", "99999"])
def test_unknown_requested_year_leaves_results_untouched(year):
    rows = [_row("Avatar.Fire.and.Ash.2025.2160p.UHD.BluRay.REMUX-FraMeSToR")]
    assert drop_other_years(rows, year) is rows


def test_router_applies_the_year_only_to_movie_searches():
    from resources.lib.router import _requested_movie_rows

    rows = [
        _row("Avatar.Fire.and.Ash.2025.2160p.UHD.BluRay.REMUX-FraMeSToR"),
        _row("Avatar.2009.2160p.UHD.BluRay.REMUX-BiTOR"),
    ]
    assert len(_requested_movie_rows(list(rows), "movie", "2009")) == 1
    assert _requested_movie_rows(rows, "episode", "2009") is rows


@patch("resources.lib.router_scriptplay._script_play_filter_autoselect_tag")
@patch("resources.lib.router_scriptplay._script_play_search_results")
def test_script_play_drops_other_years_before_filtering(mock_search, mock_tag):
    from resources.lib.router_scriptplay import _script_play_search_filter_tag

    mock_search.return_value = [
        _row("Avatar.Fire.and.Ash.2025.2160p.UHD.BluRay.REMUX-FraMeSToR"),
        _row("Avatar.2009.2160p.UHD.BluRay.REMUX-BiTOR"),
    ]
    _script_play_search_filter_tag(
        {}, "movie", "Avatar", "2009", {"season": "", "episode": ""}, MagicMock()
    )
    assert _titles(mock_tag.call_args.args[2]) == [
        "Avatar.2009.2160p.UHD.BluRay.REMUX-BiTOR"
    ]


# --- review findings -----------------------------------------------------------


def test_titles_in_another_script_are_not_one_name():
    """An ASCII-only key gave every such title with the same tags one key."""
    first = "满江红.2023.1080p.BluRay.x264-GRP"
    second = "长安三万里.2023.1080p.BluRay.x264-GRP"
    assert flt._name_form_key(first) != flt._name_form_key(second)
    groups = _groups([first, second])
    assert len(groups) == 2
    assert not same_release(groups[0][0], groups[1][0])


def test_a_complete_series_pack_says_so_in_its_name():
    from resources.lib.release_display import display_name

    title = "Show.Complete.Series.1080p.WEB.H264-GRP"
    shown = display_name({"title": title, "_meta": parse_title_metadata(title)})
    assert "Complete" in shown
    season = "Show.S02.COMPLETE.1080p.WEB.H264-GRP"
    shown = display_name({"title": season, "_meta": parse_title_metadata(season)})
    assert "S02" in shown and "Complete" not in shown


def test_a_v2_re_release_shows_its_revision():
    from resources.lib.release_display import display_name

    first = "[Yameii] Frieren - S01E05 [English Dub] [CR WEB-DL 1080p] [536EA39C]"
    second = "[Yameii] Frieren - S01E05 v2 [English Dub] [CR WEB-DL 1080p] [45586A01]"
    shown = [
        display_name({"title": title, "_meta": parse_title_metadata(title)})
        for title in (first, second)
    ]
    assert "[v2]" in shown[1] and "[v2]" not in shown[0]
    assert shown[0] != shown[1]


# --- found in past searches: one group, different releases ---------------------


@pytest.mark.parametrize(
    "first, second",
    [
        # the streaming service the WEB release came from
        (
            "MobLand.2025.S01E02.2160p.AMZN.WEB-DL.Hybrid.H265.DV.HDR10.DDP.5.1-HONE",
            "MobLand.2025.S01E02.2160p.PMTP.WEB-DL.Hybrid.H265.DV.HDR10.DDP.5.1-HONE",
        ),
        (
            "The.Town.2010.1080p.AMZN.WEB-DL.DDP5.1.H.264-GPRS",
            "The.Town.2010.1080p.MAX.WEB-DL.DDP5.1.H.264-GPRS",
        ),
        (
            "Narcos.S03E02.German.DD51.DL.1080p.NetflixHD.x264-TvS",
            "Narcos.S03E02.German.DD51.DL.1080p.NetflixUHD.x264-TvS",
        ),
        # a group's iNTERNAL release beside its regular one
        (
            "Cube.1997.iNTERNAL.DVDRip.XviD-SChiZO",
            "Cube.1997.DVDRip.XviD-SChiZO",
        ),
        # a smaller "m1080p" encode beside the full-size one
        (
            "Resident.Evil.2002.1080p.BluRay.x264-GENEMiGE",
            "Resident.Evil.2002.m1080p.BluRay.x264-GENEMiGE",
        ),
    ],
)
def test_two_releases_from_one_group_stay_apart(first, second):
    assert len(_groups([first, second])) == 2


def test_a_remux_with_and_without_its_disc_named_is_one_release():
    groups = _groups(
        [
            "The.Town.2010.1080p.BluRay.Remux.AVC.DTS-HD.MA.5.1-playBD",
            "The.Town.2010.1080p.Remux.AVC.DTS-HD.MA.5.1-playBD",
        ]
    )
    assert len(groups) == 1


@pytest.mark.parametrize(
    "title, network",
    [
        ("Show.S01E01.2160p.ATVP.WEB-DL.DDP5.1.H.265-NTb", "ATVP"),
        ("Show.S01E01.1080p.AMZN.WEBRip.DDP5.1.x264-GRP", "AMZN"),
        ("Show S01E01 1080p NF WEB-DL DDP5 1 H 264-GRP", "NF"),
        ("Mad.Max.1979.1080p.BluRay.x264-GRP", ""),
        ("Show.S01E01.1080p.WEB.h264-GRP", ""),
        ("Show.S01E01.1080p.REPACK.WEB-DL.H.264-GRP", ""),
    ],
)
def test_streaming_service_is_read_before_the_web_tag(title, network):
    assert parse_title_metadata(title)["network"] == network


def test_the_service_and_variant_show_in_the_normalized_name():
    from resources.lib.release_display import display_name

    def shown(title):
        return display_name({"title": title, "_meta": parse_title_metadata(title)})

    assert "[AMZN]" in shown("The.Town.2010.1080p.AMZN.WEB-DL.DDP5.1.H.264-GPRS")
    assert "[MAX]" in shown("The.Town.2010.1080p.MAX.WEB-DL.DDP5.1.H.264-GPRS")
    assert "[INTERNAL]" in shown("Cube.1997.iNTERNAL.1080p.WEB.H264-FLAME")
    assert "[MINI]" in shown("Resident.Evil.2002.m1080p.BluRay.x264-GENEMiGE")


# --- a release listed once ranks last in its resolution ------------------------


def _ranked(rows):
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    return filtered


def _copies(title, count, size=10 * GB):
    return [
        _row(title, size=size, link="http://example.com/{}/{}".format(title, n))
        for n in range(count)
    ]


def test_a_single_copy_ranks_below_releases_listed_more_than_once():
    """One NZB from one indexer led its resolution on a better HDR tag."""
    stray = "Movie.2024.2160p.UHD.BluRay.REMUX.DV.HDR10+.TrueHD.7.1.Atmos-OPTIMUM"
    common = "Movie.2024.2160p.WEB-DL.DDP5.1.H.265-FLUX"
    ranked = _ranked(_copies(stray, 1) + _copies(common, 3))
    assert _titles(ranked) == [common, common, common, stray]


def test_a_single_copy_still_ranks_above_a_lower_resolution():
    stray = "Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRPA"
    common = "Movie.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX"
    ranked = _ranked(_copies(common, 3) + _copies(stray, 1))
    assert ranked[0]["title"] == stray


def test_a_pinned_group_stays_first_with_one_copy():
    pinned = "Movie.2024.2160p.UHD.BluRay.REMUX.HDR.HEVC.TrueHD.7.1-CiNEPHiLES"
    common = "Movie.2024.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC.TrueHD.7.1-GRPA"
    ranked = _ranked(_copies(common, 3) + _copies(pinned, 1))
    assert ranked[0]["title"] == pinned


def test_single_copies_keep_their_order_when_nothing_is_listed_twice():
    better = "Movie.2024.2160p.WEB-DL.DV.DDP5.1.H.265-GRPA"
    plain = "Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRPB"
    assert _titles(_ranked(_copies(plain, 1) + _copies(better, 1))) == [better, plain]


def test_a_single_copy_ranks_above_a_release_with_no_group():
    named = "Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRPA"
    nameless = "Movie 2024 2160p WEB-DL DV DDP5 1 H 265"
    assert _titles(_ranked(_copies(nameless, 1) + _copies(named, 1))) == [
        named,
        nameless,
    ]


def test_copies_listed_under_two_spellings_are_not_a_single_copy():
    dotted = "Movie.2024.2160p.WEB-DL.DDP5.1.H.265-GRPA"
    spaced = "Movie 2024 2160p WEB-DL DDP5 1 H 265-GRPA"
    better_single = "Movie.2024.2160p.WEB-DL.DV.DDP5.1.H.265-GRPB"
    ranked = _ranked(
        _copies(better_single, 1) + _copies(dotted, 1) + _copies(spaced, 1)
    )
    assert ranked[-1]["title"] == better_single


def test_a_second_track_with_other_channels_is_another_release():
    groups = _groups(
        [
            "Movie.2023.2160p.BluRay.REMUX.TrueHD.7.1.Atmos.DD5.1-GRP",
            "Movie.2023.2160p.BluRay.REMUX.TrueHD.7.1.Atmos.DD2.0-GRP",
        ]
    )
    assert len(groups) == 2


def test_remux_spelled_two_ways_is_not_a_single_copy():
    """Both spellings count toward one release's copies, so it is not demoted."""
    remux = "Movie.2023.2160p.BluRay.REMUX.TrueHD.7.1.HEVC-GRPA"
    bare = "Movie.2023.2160p.REMUX.TrueHD.7.1.HEVC-GRPA"
    encode = "Movie.2023.2160p.BluRay.x265.AAC-GRPB"
    ranked = _ranked(_copies(encode, 2) + _copies(remux, 1) + _copies(bare, 1))
    assert {row["title"] for row in ranked[:2]} == {remux, bare}
    counts = flt._nzb_copy_counts(ranked)
    assert counts[id(ranked[0])] == counts[id(ranked[1])] == 2


# --- another show, matched outside its title -----------------------------------


def _shows(titles, requested):
    from resources.lib.filter import drop_other_shows

    return _titles(drop_other_shows([_row(title) for title in titles], requested))


RIGHT = [
    "Foundation.S03E05.Where.Tyrants.Spend.Eternity.2160p.ATVp.WEB-DL.H.265-NTb",
    "Foundation.2021.S03E05.1080p.WEB.H264-SuccessfulCrab",
    "Foundation S03E05 1080p ATVP WEB-DL DDP5 1 H 264-FLUX",
]


def test_another_shows_episode_named_like_the_show_is_dropped():
    """An indexer that matches text returned it for the words in its name."""
    stray = "Sweet.Magnolias.S03E05.On.This.Foundation.1080p.NF.WEB-DL.H.264-NTb"
    assert _shows(RIGHT + [stray], "Foundation") == RIGHT


def test_a_release_group_named_like_the_show_is_dropped():
    right = ["Narcos.S03E05.MRO.2160p.NF.WEB-DL.DTS-HD.MA.5.1.HEVC-PABLO"] * 2
    stray = "SomeSite.26.09.30.Some.Scene.XXX.1080p.MP4-Narcos"
    assert _shows(right + [stray], "Narcos") == right


@pytest.mark.parametrize(
    "requested, title",
    [
        # a foreign title of the right show has none of the requested words
        ("Six Feet Under", "A.Sete.Palmos.S02E01.1080p.HMAX.WEB-DL.DD2.0.x264-PiA"),
        # an apostrophe an indexer dropped
        (
            "It's Always Sunny in Philadelphia",
            "Its.Always.Sunny.in.Philadelphia.S17E01.1080p.WEB.H264-GRP",
        ),
        # a number the parser takes out of the title
        ("Station 19", "Station.19.S07E01.1080p.WEB.H264-GRP"),
        # a name with no parsed title at all
        ("One Piece", "[SubsPlease] One Piece - 1100 (1080p) [ABCD1234].mkv"),
    ],
)
def test_rows_of_the_requested_show_are_kept(requested, title):
    rows = RIGHT + [title]
    assert _shows(rows, requested) == rows


def test_nothing_is_dropped_when_most_results_would_go():
    """Then the title is being misread, not the indexers returning strays."""
    strays = [
        "Sweet.Magnolias.S03E05.On.This.Foundation.1080p.NF.WEB-DL.H.264-NTb",
        "In.The.Cut.S01E02.Solid.Foundation.1080p.WEB.H264-GRP",
    ]
    rows = strays + RIGHT[:1]
    assert _shows(rows, "Foundation") == rows


@pytest.mark.parametrize("requested", ["", None, "The", "24"])
def test_a_title_with_no_usable_words_leaves_results_untouched(requested):
    from resources.lib.filter import drop_other_shows

    rows = [_row(title) for title in RIGHT]
    assert drop_other_shows(rows, requested) is rows


def test_router_applies_the_show_rule_to_episode_searches():
    from resources.lib.router import _requested_episode_rows

    stray = "Sweet.Magnolias.S03E05.On.This.Foundation.1080p.NF.WEB-DL.H.264-NTb"
    rows = [_row(title) for title in RIGHT + [stray]]
    kept = _requested_episode_rows(rows, "episode", "3", "5", title="Foundation")
    assert _titles(kept) == RIGHT


# --- a title that is the requested one plus more words --------------------------


def test_a_longer_title_is_another_show_and_is_demoted():
    from resources.lib.filter import partition_series_rows

    office = _row("The.Office.US.S02E01.The.Dundies.1080p.WEB-DL.H.264-GRP")
    dated = _row("The.Office.2005.S02E01.1080p.WEB-DL.H.264-GRP")
    polish = _row("The.Office.PL.S02E01.POLISH.2160p.WEB.H265-FLAME")
    movers = _row("The.Office.Movers.S02E01.MULTI.2160p.WEB.H265-BAWLS")
    matching, rest = partition_series_rows(
        [polish, movers, office, dated], "The Office"
    )
    assert matching == [office, dated]
    assert rest == [polish, movers]


# --- movie searches: a show's episode -------------------------------------------


def test_a_shows_episode_is_dropped_from_a_movie_search():
    from resources.lib.filter import drop_tv_episodes

    movie = ["Ebenezer.2026.1080p.WEB-DL.DDP5.1.H.264-GRP"] * 2
    episode = "Little.House.on.the.Prairie.S02E10.Ebenezer.1080p.WEB.H264-GRP"
    rows = [_row(title) for title in movie + [episode]]
    assert _titles(drop_tv_episodes(rows)) == movie


def test_a_special_stays_on_a_movie_search():
    """A special can be what a movie entry points at."""
    from resources.lib.filter import drop_tv_episodes

    special = "IT.Welcome.to.Derry.S01E00.A.Story.of.Derry.1080p.WEB-DL.H.264-GRP"
    rows = [_row(special), _row("A.Story.of.Derry.2025.1080p.WEB-DL.H.264-GRP")]
    assert drop_tv_episodes(rows) is rows


def test_a_request_with_a_country_code_keeps_that_countrys_show():
    """The parser takes "UK" out of the title, so it is not judged as a word."""
    uk = "The.Office.UK.S01E01.1080p.WEB.H264-GRP"
    us = "The.Office.US.S01E01.1080p.WEB.H264-GRP"
    assert _shows([uk, us], "The Office UK") == [uk, us]


def test_sizes_of_a_mkv_and_a_mp4_are_not_compared():
    """They are different files, so one's size says nothing about the other."""
    name = "Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP"

    def subject(extension, size, n):
        return _row(
            '[1/3] - "{}.{}" yEnc 100'.format(name, extension),
            size=size,
            link="http://example.com/{}/{}".format(extension, n),
        )

    rows = [subject("mkv", 10 * GB, n) for n in range(3)] + [
        subject("mp4", 13 * GB, 0),
        subject("mp4", 15 * GB, 1),
    ]
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    assert not any(row.get("_size_outlier") for row in filtered)


def test_sizes_of_two_known_years_are_not_compared():
    """A film and its remake are different releases under one set of tags."""
    old = "Movie.1990.1080p.BluRay.x264-GRP"
    new = "Movie.2020.1080p.BluRay.x264-GRP"
    rows = _sized([10 * GB] * 3, title=old) + [
        _row(new, size=13 * GB, link="http://example.com/new/0"),
        _row(new, size=15 * GB, link="http://example.com/new/1"),
    ]
    filtered, _all = flt.filter_results(rows, settings_getter=_defaults)
    assert not any(row.get("_size_outlier") for row in filtered)


@pytest.mark.parametrize(
    "title, requested, year",
    [
        ("Blade.Runner.2049.2160p.BluRay.x265-GRP", "Blade Runner 2049", "2017"),
        ("2001.A.Space.Odyssey.1080p.BluRay.x264-GRP", "2001: A Space Odyssey", "1968"),
    ],
)
def test_a_year_in_the_requested_title_is_not_a_release_year(title, requested, year):
    """With no release year in the name, the parser takes the title's number."""
    rows = [_row(title)]
    assert drop_other_years(rows, year, requested) is rows


def test_a_title_number_does_not_excuse_another_film():
    rows = [_row("Blade.Runner.2049.2160p.BluRay.x265-GRP")]
    assert drop_other_years(rows, "1982", "Blade Runner") == []


def test_a_second_tracks_channels_show_in_the_normalized_name():
    from resources.lib.release_display import display_name

    def shown(title):
        return display_name({"title": title, "_meta": parse_title_metadata(title)})

    surround = shown("Movie.2023.2160p.BluRay.REMUX.TrueHD.7.1.Atmos.DD5.1-GRP")
    stereo = shown("Movie.2023.2160p.BluRay.REMUX.TrueHD.7.1.Atmos.DD2.0-GRP")
    assert "DD 5.1" in surround and "DD 2.0" in stereo
    assert surround != stereo


# --- groups the hyphen rule cannot see (from real downloaded NZBs) -------------


@pytest.mark.parametrize(
    "title, group",
    [
        # a poster or indexer tag written after the file extension
        (
            "A.Beautiful.Mind.2001.2160p.UHD.Blu-ray.Remux.DV.HDR.HEVC.DTS-HD.MA.5.1"
            "-CiNEPHiLES.mkv-WtF",
            "CiNEPHiLES",
        ),
        (
            "Stitch.2002.MULTi.UHD.BluRay.2160p.TrueHD.Atmos.7.1.DV.HDR10.REMUX"
            "-seedpool.mkv-[N-Z-B]",
            "seedpool",
        ),
        # a group of several words
        (
            "Crime.101.2026.2160p.UHD.BluRay.REMUX.DV.P7.HDR.MULTi[Ben The Men]",
            "Ben The Men",
        ),
        (
            "Ace.Ventura.1994.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi.DTS-MA.5.1-H265"
            "-BEN.THE.MEN",
            "BEN.THE.MEN",
        ),
        # a tag block wrapped in brackets
        (
            "Casa.Grande.[2026].[2160p.YKW.WEB-DL.Hybrid.H265.DV.HDR.DDP.5.1.English"
            "-HONE]",
            "HONE",
        ),
        # the last word after a tag, with no hyphen before it
        ("House of the Dragon S03E01 2160p HBO Max WEBDL H265 EAC3 TSRG", "TSRG"),
        ("Monarch (2026) S02E03 Secrets 1080p WEBDL H264 Atmos @TSRG", "TSRG"),
        ("Return.to.Silent.Hill.2026.1080p.WEB-DL.DUAL.AAC2.0.H.264.TSRG", "TSRG"),
        ("9-1-1 - S02E06 - Dosed.WEBDL-1080p.EAC3.h264.NTb", "NTb"),
    ],
)
def test_groups_read_from_real_download_names(title, group):
    assert parse_title_metadata(title)["group"] == group


@pytest.mark.parametrize(
    "title",
    [
        "The.Eric.Andre.Show.S04E07.Raymond.Cruz.1080p.WEB-DL.DD5.1.H.264",
        "Married.At.First.Sight.S12E04.720p.HEVC.x265",
        "MasterChef.US.S10E01.1080p.AMZN.WEB-DL",
        "Married.at.First.Sight.S08E02.WEB-DL.720p.H.264.AAC",
        "Movie.2020.1080p.BluRay.x264.German",
        "Movie.2020.1080p.BluRay.DTS.x264.Extended",
        "Show.S01E01.1080p.WEB.h264.REPACK",
        "Show S01E01 1080p WEB h264 Episode Title Words",
    ],
)
def test_names_with_no_group_stay_without_one(title):
    assert parse_title_metadata(title)["group"] == ""


def test_a_site_tag_in_brackets_is_not_the_group():
    title = "Movie.2020.1080p.BluRay.x264-GRP[rarbg]"
    assert parse_title_metadata(title)["group"] == "GRP"


def test_a_group_is_the_same_however_its_words_are_joined():
    groups = _groups(
        [
            "Movie.2026.2160p.UHD.BluRay.REMUX.DV.HDR.MULTi.TrueHD.7.1-BEN.THE.MEN",
            "Movie 2026 2160p UHD BluRay REMUX DV HDR MULTi TrueHD 7 1 [Ben The Men]",
        ]
    )
    assert len(groups) == 1


def test_an_anime_episode_number_is_read_in_a_dotted_name():
    """All five unread names among 42,004 real TV downloads had this shape."""
    dotted = "[Erai-raws].rakudai.kenja-07.[1080p.CR.WEB-DL.AVC.AAC][MultiSub]"
    assert parse_title_metadata(dotted)["ptt_identity"]["episodes"] == [7]
    spaced = "[Erai-raws] Sousou no Frieren - 05 [1080p][165931CD]"
    assert parse_title_metadata(spaced)["ptt_identity"]["episodes"] == [5]
