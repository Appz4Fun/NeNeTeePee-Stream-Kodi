# SPDX-License-Identifier: GPL-3.0-or-later
"""Releases tagged as an upscale: the tag, the hide setting, and the rank."""

from unittest.mock import patch

import pytest
from resources.lib import filter as filters
from resources.lib.filter_upscale import detect_ai_upscale, is_ai_upscale
from resources.lib.release_display import display_name
from resources.lib.release_match import same_release
from resources.lib.results_dialog import _attribute_tag

STREET_FIGHTER = "Street Fighter II The Animated Movie 4k [2160]-AI-Raws"
SIZE = "5000000000"


def _meta(title):
    return filters.parse_title_metadata(title)


def _row(title, link="", size=SIZE):
    return {
        "title": title,
        "link": link or title,
        "size": size,
        "pubdate": "",
        "_meta": _meta(title),
    }


@pytest.mark.parametrize(
    "title",
    [
        STREET_FIGHTER,
        "[AI-Raws] Cowboy Bebop - 01 (BD HEVC 3840x2160 FLAC)",
        "Cowboy.Bebop.S01E01.2160p.BluRay.FLAC.x265-AI_Raws",
        # A group named "AI", or with it as a word of its name.
        "Hail.Mary.1985.1080p.WEB.h264-AI",
        "Movie.1994.2160p.BluRay.x265-Upscale-AI",
        # "AI" beside an upscale word, wherever it stands.
        "Movie.1994.2160p.AI.Upscale.x265-GRP",
        "Movie.1994.AI-Upscaled.2160p.BluRay.x265-GRP",
        "Movie 1994 A.I. Upscale 2160p x265-GRP",
        "Movie.1994.2160p.Upscaled.by.AI.x265-GRP",
        "Movie AI Upscaled 4K",
        "Star.Trek.DS9.S01E01.1080p.AI.Upscaled.DVD.x265-Joe",
        "Show.S01E01.AI.Upscale.1080p.x265-GRP",
        "Top Gear.S01.AI UPSCALE.1080p.x265.AAC-[BeesKnees]",
        "Invincible.2021.S01.2160p.AI.Upscale.DDP5.1.H.265-Jmess",
        # An upscale tag with no "AI" beside it.
        "Bluey.S02E27.Grandad.2160p.DSNP.WEB-DL.ESRGAN.Upscale.EAC3.5.1.x264-PoF",
        "Home 2015 Upscaled BluRay 2160p HDR10 HEVC DTS-HD MA 7.1 x265-E",
        "Scrubs.S02E01.My.Overkill.Upscale.Hybrid.1080p.WEBRip.DD5.1.H.264-GRP",
        "Movie.1994.2160p.UPSCALED.BluRay.x265-GRP",
        "Movie.1994.UpsUHD.2160p.x265-GRP",
        "Movie.1994.Upscale.1080p.DVD.x264-GRP",
        # A bare "AI" among the technical tags.
        "Movie.1994.2160p.AI.BluRay.x265-GRP",
        "Movie.1994.AI.2160p.BluRay.x265-GRP",
        "Movie.1994.4K.AI.Remaster.2160p.x265-GRP",
        "Movie.1994.2160p.AI.Enhanced.BluRay.x265-GRP",
        "[Group] Old Show - 07 [BD 2160p AI][HEVC]",
        "Show.S02E03.1080p.AI.WEB-DL.x264-GRP",
        "movie.1994.2160p.ai.x265-grp",
    ],
)
def test_an_upscale_tag_is_read(title):
    meta = _meta(title)
    assert meta["ai_upscale"] is True
    assert detect_ai_upscale(title, meta["group"]) is True


@pytest.mark.parametrize(
    "title",
    [
        # "AI" is the title, or a word of it.
        "A.I.Artificial.Intelligence.2001.1080p.BluRay.x264-GRP",
        "A.I. Artificial Intelligence (2001) 2160p UHD BluRay REMUX-FraMeSToR",
        "AI.Artificial.Intelligence.2001.2160p.UHD.BluRay.REMUX-FraMeSToR",
        "A.I.2001.Remastered.1080p.BluRay.x264-GRP",
        "A.I.Remastered.2001.1080p.BluRay.x264-GRP",
        "The.AI.Doc.2026.1080p.WEB-DL.x264-GRP",
        "AI.Love.You.2022.1080p.NF.WEB-DL.DDP5.1.H.264-GRP",
        "A.I.Rising.2018.1080p.BluRay.x264-GRP",
        "2001.A.Space.Odyssey.1968.2160p.UHD.BluRay.REMUX-FraMeSToR",
        # An episode's own title follows its number.
        "Black.Mirror.S07E01.AI.1080p.WEB-DL.x264-GRP",
        "Show.2024.S01E03.The.AI.Episode.1080p.WEB.h264-GRP",
        "Daily.Show.2026.11.12.AI.Special.1080p.WEB.h264-GRP",
        "Ancient.Aliens.S22E11.Alien.A.I.1080p.HULU.WEB-DL.AAC2.0.H.264-RAWR",
        "Stargate.SG-1.S01E16.Cor-Ai.REPACK.1080p.BluRay.REMUX.AVC.DD.5.1-TRiToN",
        "Sons.of.Anarchy.S05E13.J'ai.Obtenu.Cette.Extended.1080p.AVC.REMUX-FraMeSToR",
        "[Group] Ai no Kusabi - 01 [BD 1080p]",
        "[Group] Show (2019) - 05 - AI Uprising [1080p]",
        # The letters inside a word or a group name.
        "Air.2023.2160p.WEB-DL.DDP5.1.Atmos-FLUX",
        "Samurai.Champloo.Upscaled.S01E01.1080p.BluRay.x265-GRP",
        "Movie.2001.1080p.BluRay.x264-RAiN",
        "Movie.2001.1080p.BluRay.x264-AiR",
        "Movie.2001.1080p.BluRay.x264-SAiNTS",
        "Movie.2001.1080p.BluRay.x264-TAiCHi",
        # Machine-made subtitles or dubbing, not upscaled video.
        "Movie.2001.1080p.BluRay.AI.Subs.x264-GRP",
        "Movie.2001.1080p.WEB-DL.AI-Dub.x264-GRP",
        # Words PTT reads as "upscaled" that say nothing of the kind.
        "Blade.Runner.1982.Enhanced.Edition.1080p.BluRay-GRP",
        "Movie.2021.IMAX.Enhanced.2160p.DSNP.WEB-DL.DDP5.1.Atmos.DV.H.265-GRP",
        "Upscaled.2019.1080p.WEB-DL.x264-GRP",
        # An ordinary release.
        "Street.Fighter.II.The.Animated.Movie.1994.2160p.UHD.BluRay.x265-GRP",
    ],
)
def test_ai_in_the_title_or_inside_a_word_is_not_a_tag(title):
    meta = _meta(title)
    assert meta["ai_upscale"] is False
    assert detect_ai_upscale(title, meta["group"]) is False


def test_a_row_parsed_before_the_tag_existed_is_read_from_its_title():
    row = _row(STREET_FIGHTER)
    del row["_meta"]["ai_upscale"]
    assert is_ai_upscale(row) is True
    plain = _row("Movie.1994.2160p.BluRay.x265-GRP")
    del plain["_meta"]["ai_upscale"]
    assert is_ai_upscale(plain) is False
    assert is_ai_upscale({"title": "Movie.1994.2160p.AI.Upscale.x265-GRP"}) is True
    assert is_ai_upscale({"title": None}) is False


def test_a_raw_usenet_subject_is_read_as_its_release_name():
    subject = '[01/45] - "Movie.1994.2160p.AI.Upscale.x265-GRP.part01.rar" yEnc (1/99)'
    assert _meta(subject)["ai_upscale"] is True
    assert is_ai_upscale({"title": subject}) is True


# --- ranking ---


def _order(rows, sort_order=0):
    return [
        row["link"] for row in filters._sort_results(rows, {"sort_order": sort_order})
    ]


def test_an_upscale_ranks_below_every_genuine_release_of_its_resolution():
    genuine = "Street.Fighter.II.The.Animated.Movie.1994.2160p.UHD.BluRay.x265-GRP"
    lower = "Street.Fighter.II.The.Animated.Movie.1994.1080p.BluRay.x264-GRP"
    rows = [
        _row(STREET_FIGHTER, "ai-1", size="90000000000"),
        _row(STREET_FIGHTER, "ai-2", size="90000000000"),
        _row(genuine, "genuine-1"),
        _row(genuine, "genuine-2"),
        _row(lower, "lower-1"),
        _row(lower, "lower-2"),
    ]
    # Resolution still comes first: the upscale stays in the 2160p block.
    assert _order(rows) == [
        "genuine-1",
        "genuine-2",
        "ai-1",
        "ai-2",
        "lower-1",
        "lower-2",
    ]


def test_better_tags_do_not_lift_an_upscale():
    upscale = "Movie.1994.2160p.AI.Upscale.UHD.BluRay.REMUX.DV.HDR.TrueHD.7.1.Atmos-BMF"
    plain = "Movie.1994.2160p.WEB-DL.DDP5.1.H.265-GRP"
    rows = [
        _row(upscale, "up-1"),
        _row(upscale, "up-2"),
        _row(plain, "plain-1"),
        _row(plain, "plain-2"),
    ]
    assert _order(rows) == ["plain-1", "plain-2", "up-1", "up-2"]


def test_an_upscale_ranks_below_single_copy_and_groupless_releases():
    upscale = "Movie.1994.2160p.AI.Upscale.BluRay.x265-GRP"
    listed = "Movie.1994.2160p.WEB-DL.DDP5.1.H.265-NTb"
    single = "Movie.1994.2160p.UHD.BluRay.REMUX.DV.TrueHD.7.1.Atmos-ONCE"
    groupless = "Movie.1994.2160p.WEB-DL.DDP5.1.H.265"
    rows = [
        _row(upscale, "up-1"),
        _row(upscale, "up-2"),
        _row(groupless, "groupless"),
        _row(single, "single"),
        _row(listed, "listed-1"),
        _row(listed, "listed-2"),
    ]
    assert _order(rows) == [
        "listed-1",
        "listed-2",
        "single",
        "groupless",
        "up-1",
        "up-2",
    ]


def test_upscales_keep_the_usual_order_among_themselves():
    best = "Movie.1994.2160p.AI.Upscale.BluRay.DV.TrueHD.7.1.Atmos.x265-GRPA"
    worse = "Movie.1994.2160p.AI.Upscale.BluRay.DD5.1.x265-GRPB"
    rows = [
        _row(worse, "worse-1"),
        _row(worse, "worse-2"),
        _row(best, "best-1"),
        _row(best, "best-2"),
    ]
    assert _order(rows) == ["best-1", "best-2", "worse-1", "worse-2"]


def test_the_pinned_groups_and_other_ranks_are_unchanged_without_an_upscale():
    cinephiles = "Movie.1994.2160p.UHD.BluRay.REMUX.DTS-HD.MA.5.1-CiNEPHiLES"
    framestor = "Movie.1994.2160p.UHD.BluRay.REMUX.DV.TrueHD.7.1.Atmos-FraMeSToR"
    atmos = "Movie.1994.2160p.WEB-DL.DDP5.1.Atmos.H.265-GRP"
    plain = "Movie.1994.2160p.WEB-DL.DDP5.1.H.265-NTb"
    upscale = "Movie.1994.2160p.AI.Upscale.BluRay.DV.TrueHD.7.1.Atmos.x265-GRP"
    titles = [upscale, plain, atmos, framestor, cinephiles]
    rows = [
        _row(title, "{}-{}".format(n, copy))
        for n, title in enumerate(titles)
        for copy in (1, 2)
    ]
    ordered = [row["title"] for row in filters._sort_results(rows, {"sort_order": 0})]
    assert ordered[::2] == [cinephiles, framestor, atmos, plain, upscale]


def test_size_and_age_sorts_ignore_the_upscale_tag():
    rows = [
        _row(STREET_FIGHTER, "ai", size="90000000000"),
        _row("Movie.1994.2160p.UHD.BluRay.x265-GRP", "genuine", size="1000"),
    ]
    assert _order(rows, sort_order=1) == ["ai", "genuine"]


# --- the hide setting ---


def _settings(**values):
    def getter(key, default=""):
        return values.get(key, default)

    return getter


def test_the_setting_is_off_by_default():
    assert filters._get_filter_settings(_settings())["hide_ai_upscales"] is False
    settings = filters._get_filter_settings(_settings(filter_hide_ai_upscales="true"))
    assert settings["hide_ai_upscales"] is True


def test_hiding_removes_tagged_releases_and_keeps_titles_with_ai():
    titles = [
        STREET_FIGHTER,
        "Movie.1994.2160p.AI.Upscale.x265-GRP",
        "A.I.Artificial.Intelligence.2001.1080p.BluRay.x264-GRP",
        "The.AI.Doc.2026.1080p.WEB-DL.x264-GRP",
        "Air.2023.2160p.WEB-DL.DDP5.1.Atmos-FLUX",
    ]
    rows = [{"title": title, "link": title, "size": SIZE} for title in titles]
    getter = _settings(filter_hide_ai_upscales="true", max_results="0")
    with patch("resources.lib.filter.telemetry"):
        filtered, all_parsed = filters.filter_results(rows, settings_getter=getter)
    assert sorted(row["title"] for row in filtered) == sorted(titles[2:])
    rejects = {row["title"]: row["_filter_reject"] for row in all_parsed}
    assert rejects[titles[0]] == "AI upscale"
    assert rejects[titles[1]] == "AI upscale"
    assert rejects[titles[2]] is None


def test_with_the_setting_off_upscales_are_listed():
    rows = [
        {"title": STREET_FIGHTER, "link": "ai", "size": SIZE},
        {"title": "Movie.1994.2160p.UHD.BluRay.x265-GRP", "link": "g", "size": SIZE},
    ]
    with patch("resources.lib.filter.telemetry"):
        filtered, _all = filters.filter_results(
            rows, settings_getter=_settings(max_results="0")
        )
    assert [row["link"] for row in filtered] == ["g", "ai"]


def test_first_rejecting_filter_names_the_upscale():
    row = _row(STREET_FIGHTER)
    settings = filters._get_filter_settings(_settings(filter_hide_ai_upscales="true"))
    assert filters.first_rejecting_filter(row, row["_meta"], settings) == "AI upscale"
    settings["hide_ai_upscales"] = False
    assert filters.first_rejecting_filter(row, row["_meta"], settings) is None


# --- same release, and the picker ---


def test_an_upscale_is_not_a_copy_of_the_release_it_was_made_from():
    genuine = _row("Movie.1994.2160p.BluRay.DTS-HD.MA.5.1.x265-GRP")
    upscale = _row("Movie.1994.2160p.Upscaled.BluRay.DTS-HD.MA.5.1.x265-GRP")
    assert same_release(genuine, _row(genuine["title"], "second")) is True
    assert same_release(upscale, _row(upscale["title"], "second")) is True
    assert same_release(genuine, upscale) is False


def test_the_picker_names_and_tags_an_upscale():
    upscale = _row("Movie.1994.2160p.AI.Upscale.BluRay.x265-GRP")
    assert "[UPSCALE]" in display_name(upscale)
    assert "UPSCALE" in _attribute_tag(upscale["_meta"])
    hybrid = _row("Movie.1994.2160p.HYBRID.BluRay.REMUX.DV.x265-GRP")
    assert "HYBRID" in _attribute_tag(hybrid["_meta"])
    assert "UPSCALE" not in _attribute_tag(hybrid["_meta"])
    plain = _row("Movie.1994.2160p.BluRay.x265-GRP")
    assert _attribute_tag(plain["_meta"]) == ""
    assert "UPSCALE" not in display_name(plain)
