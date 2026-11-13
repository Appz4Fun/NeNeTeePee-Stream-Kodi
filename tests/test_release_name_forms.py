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
