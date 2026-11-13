# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""An episode search shows the requested season and episode only.

A raw-subject indexer matches the show name alone, so a Six Feet Under
S02E01 search came back with 74 other episodes from S01 through S05.
"""

from unittest.mock import MagicMock, patch

import pytest
from resources.lib import filter as flt
from resources.lib.filter import drop_other_episodes
from resources.lib.router_play import _prepare_picker_rows


def _row(title):
    return {
        "title": title,
        "link": "http://example.com/{}".format(len(title)),
        "size": "5000000000",
        "indexer": "test",
        "pubdate": "",
        "age": "1 day",
    }


def _titles(rows):
    return [row["title"] for row in rows]


WANTED = "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX"


def test_other_seasons_and_episodes_are_dropped():
    rows = [
        _row(WANTED),
        _row("Six.Feet.Under.S01E01.Pilot.1080p.AMZN.WEB-DL.DDP5.1.H.264-ASiANLAD"),
        _row("Six.Feet.Under.S02E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-ASiANLAD"),
        _row("Six.Feet.Under.S03E13.1080p.AMZN.WEB-DL.DDP5.1.H.264-ASiANLAD"),
        _row("Six Feet Under - 2x04 - Driving Mr Mossback"),
    ]
    assert _titles(drop_other_episodes(rows, (2, 1))) == [WANTED]


def test_raw_yenc_subjects_for_other_episodes_are_dropped():
    """The live case: NZBIndex lists raw subjects for the whole show."""
    wanted = (
        '[1/3] - "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1'
        '.H.264-ASiANLAD.mkv" yEnc  4400443301'
    )
    other = (
        '[1/3] - "Six.Feet.Under.S03E13.Im.Sorry.Im.Lost.1080p.AMZN.WEB-DL'
        '.DDP5.1.H.264-ASiANLAD.mkv" yEnc  4571184071'
    )
    assert _titles(drop_other_episodes([_row(other), _row(wanted)], (2, 1))) == [wanted]


@pytest.mark.parametrize(
    "title",
    [
        "Six.Feet.Under.S02E01E02.1080p.WEB-DL.DDP5.1.H.264-GRP",
        "Six.Feet.Under.S02E01-E03.1080p.WEB-DL.DDP5.1.H.264-GRP",
        "Six.Feet.Under.S02.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX",
        "Six.Feet.Under.S01-S05.Complete.1080p.WEB-DL.H.264-GRP",
        "Six.Feet.Under.Complete.Series.1080p.WEB-DL.H.264-GRP",
        "Six.Feet.Under.S02.COMPLETE.1080p.WEB-DL.H.264-GRP",
        "Six Feet Under - 2x01 - In the Game",
        "The.Daily.Show.2026.11.12.Guest.1080p.WEB.h264-GRP",
        "WWE.Raw.2026.11.09.1080p.WEB.h264-HEEL",
        # PTT misses these episode numbers; a video name is given the benefit.
        "[Moozzi2] Sousou no Frieren-05 [BD 1920x1080 x265-10Bit Flac]",
        "[Beatrice-Raws].Sousou.no.Frieren.05.[BDRip.1920x1080.HEVC.TrueHD]",
    ],
)
def test_rows_that_may_hold_the_episode_are_kept(title):
    """Multi-episode files, packs, dated shows, and unnumbered video names."""
    assert _titles(drop_other_episodes([_row(title)], (2, 1))) == [title]


@pytest.mark.parametrize(
    "title",
    [
        "Six.Feet.Under.S02E02E03.1080p.WEB-DL.DDP5.1.H.264-GRP",
        "Six.Feet.Under.S03.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX",
        "Six.Feet.Under.S03-S05.1080p.WEB-DL.H.264-GRP",
        "Six.Feet.Under.S00E01.Special.1080p.WEB-DL.H.264-GRP",
    ],
)
def test_packs_and_multi_episode_files_without_the_episode_are_dropped(title):
    assert not drop_other_episodes([_row(title)], (2, 1))


ALBUM_SUBJECT = (
    '[2/6] - "Six_Feet_Under-Next_to_Die-CD-FLAC-2026-MOONBLOOD.part1.rar"'
    " yEnc  104857600"
)
DISC_SUBJECT = (
    'picasa benelux team + www.d4d.cc [002/167] - "six feet under seizoen4'
    ' disk5 DVD9 cave10233.part001.rar" yEnc'
)


@pytest.mark.parametrize(
    "title",
    [
        # The rows a raw-subject indexer returned for the show's name alone.
        "09-six_feet_under-human_target.flac",
        "09_human8_-_six_feet_under_(original_mix)-idc.mp3",
        ALBUM_SUBJECT,
        '[3/3] - "Six_Feet_Under-Next_to_Die-2026-BERC.vol00-01.par2" yEnc  33555012',
        'Metallica - Six Feet Down Under - "scans and support.rar"  14.0 MBytes yEnc',
        DISC_SUBJECT,
        # No season, episode, air date or video tag: not an episode.
        "Six Feet Under Original Television Soundtrack",
        "aBcD1234eFgH5678",
    ],
)
def test_rows_that_are_not_an_episode_are_dropped(title):
    assert not drop_other_episodes([_row(title)], (2, 1))


def test_numbered_track_is_dropped_even_when_the_number_matches():
    """A bare number only counts on a name that also parses as video."""
    track = "09-six_feet_under-human_target.flac"
    assert not drop_other_episodes([_row(track)], (1, 9))


def test_seasonless_episode_number_must_fit_the_request():
    """Season 1 needs the same number; later seasons an absolute count."""
    bare = "[Erai-raws] Frieren - 05 [1080p].mkv"
    assert _titles(drop_other_episodes([_row(bare)], (1, 5))) == [bare]
    assert not drop_other_episodes([_row(bare)], (1, 6))
    # S02E03 is at least the 4th episode overall, so "05" may be it.
    assert _titles(drop_other_episodes([_row(bare)], (2, 3))) == [bare]
    # S02E06 is at least the 7th, so "05" cannot be it.
    assert not drop_other_episodes([_row(bare)], (2, 6))
    # A later season may number its own episodes without naming the season.
    assert _titles(drop_other_episodes([_row(bare)], (2, 5))) == [bare]


def test_season_and_episode_given_as_text_are_understood():
    rows = [_row(WANTED), _row("Six.Feet.Under.S02E02.1080p.WEB-DL.H.264-GRP")]
    assert _titles(drop_other_episodes(rows, ("2", "01"))) == [WANTED]


@pytest.mark.parametrize(
    "requested",
    [None, ("", ""), ("2", ""), ("", "1"), (None, None), ("two", "one"), (2,), "21"],
)
def test_unknown_request_leaves_results_untouched(requested):
    rows = [_row(WANTED), _row("Six.Feet.Under.S03E13.1080p.WEB-DL.H.264-GRP")]
    assert drop_other_episodes(rows, requested) is rows
    assert "_meta" not in rows[0]


def test_empty_results_are_returned_as_given():
    assert not drop_other_episodes([], (2, 1))
    assert drop_other_episodes(None, (2, 1)) is None


def test_metadata_is_parsed_once_and_reused_by_filter_results():
    rows = [_row(WANTED), _row("Six.Feet.Under.S03E13.1080p.WEB-DL.H.264-GRP")]
    real = flt.parse_title_metadata
    with patch.object(flt, "parse_title_metadata", side_effect=real) as parse:
        kept = drop_other_episodes(rows, (2, 1))
        filtered, all_parsed = flt.filter_results(
            kept, settings_getter=lambda _key, default="": default
        )
    assert parse.call_count == 2
    assert _titles(all_parsed) == [WANTED]
    assert all(row in all_parsed for row in filtered)


def test_drop_is_logged_with_the_requested_episode():
    rows = [_row(WANTED), _row("Six.Feet.Under.S03E13.1080p.WEB-DL.H.264-GRP")]
    with patch.object(flt.xbmc, "log") as log:
        drop_other_episodes(rows, (2, 1))
    messages = [call.args[0] for call in log.call_args_list]
    assert any("dropped 1 result(s)" in m and "S02E01" in m for m in messages)


OTHER = "Six.Feet.Under.S03E13.1080p.AMZN.WEB-DL.DDP5.1.H.264-ASiANLAD"


def test_router_drops_other_episodes_on_an_episode_search():
    from resources.lib.router import _requested_episode_rows

    rows = [_row(WANTED), _row(OTHER)]
    assert _titles(_requested_episode_rows(rows, "episode", "2", "1")) == [WANTED]


def test_router_leaves_movie_searches_untouched():
    from resources.lib.router import _requested_episode_rows

    rows = [_row("Alien.1979.1080p.BluRay.DDP.5.1.x264-hallowed"), _row(OTHER)]
    assert _requested_episode_rows(rows, "movie", "", "") is rows
    assert _requested_episode_rows(None, "episode", "2", "1") is None


def test_picker_views_and_total_come_from_the_kept_rows():
    """Neither picker view offers the dropped rows, nor does the count."""
    from resources.lib.router import _requested_episode_rows

    kept = _requested_episode_rows([_row(WANTED), _row(OTHER)], "episode", 2, 1)
    picker_rows, all_rows, total_count = _prepare_picker_rows(
        kept, "Six Feet Under", MagicMock(), None
    )
    assert _titles(picker_rows) == [WANTED]
    assert _titles(all_rows) == [WANTED]
    assert total_count == 1


def test_only_other_episodes_means_no_results():
    from resources.lib.router import _requested_episode_rows

    notify = MagicMock()
    kept = _requested_episode_rows([_row(OTHER)], "episode", 2, 1)
    assert kept == []
    assert _prepare_picker_rows(kept, "Six Feet Under", notify, None) is None
    notify.assert_called_once()


@patch("resources.lib.router_scriptplay._script_play_filter_autoselect_tag")
@patch("resources.lib.router_scriptplay._script_play_search_results")
def test_script_play_drops_other_episodes_before_filtering(mock_search, mock_tag):
    """The RunScript flow hands the filter step the requested episode only."""
    from resources.lib.router_scriptplay import _script_play_search_filter_tag

    mock_search.return_value = [_row(WANTED), _row(OTHER)]
    _script_play_search_filter_tag(
        {},
        "episode",
        "Six Feet Under",
        "",
        {"season": "2", "episode": "1"},
        MagicMock(),
    )
    assert _titles(mock_tag.call_args.args[2]) == [WANTED]


@patch("resources.lib.router_scriptplay._script_play_filter_autoselect_tag")
@patch("resources.lib.router_scriptplay._script_play_search_results")
def test_script_play_movie_search_keeps_every_row(mock_search, mock_tag):
    from resources.lib.router_scriptplay import _script_play_search_filter_tag

    rows = [
        _row("Alien.1979.1080p.BluRay.DDP.5.1.x264-hallowed"),
        _row("Alien.1979.Directors.Cut.2160p.UHD.BluRay.x265-GRP"),
    ]
    mock_search.return_value = rows
    _script_play_search_filter_tag(
        {}, "movie", "Alien", "1979", {"season": "", "episode": ""}, MagicMock()
    )
    assert mock_tag.call_args.args[2] is rows


# --- daily shows: releases named by air date -------------------------------

NIGHT = "The.Daily.Show.2026.11.12.Ron.Howard.1080p.WEB.h264-EDITH"
NIGHT_BEFORE = "The.Daily.Show.2026.11.11.Kathryn.Hahn.1080p.WEB.h264-EDITH"
OLD_NIGHT = "The.Daily.Show.2010.02.11.Lee.Daniels.HDTV.XviD-FQM"
NUMBERED = "The.Daily.Show.S31E20.1080p.WEB.h264-EDITH"


def _daily_rows():
    return [_row(NIGHT), _row(NIGHT_BEFORE), _row(OLD_NIGHT), _row(NUMBERED)]


def test_air_date_keeps_only_the_requested_night():
    kept = drop_other_episodes(_daily_rows(), (31, 20), air_date="2026-11-12")
    assert _titles(kept) == [NIGHT, NUMBERED]


def test_air_date_with_a_time_part_is_understood():
    kept = drop_other_episodes(_daily_rows(), (31, 20), air_date="2026-11-12T04:00:00Z")
    assert _titles(kept) == [NIGHT, NUMBERED]


def test_air_date_one_day_off_keeps_the_neighbouring_night():
    """The listing and the release names can disagree by a calendar day."""
    rows = [_row(NIGHT_BEFORE), _row(OLD_NIGHT), _row(NUMBERED)]
    kept = drop_other_episodes(rows, (31, 20), air_date="2026-11-12")
    assert _titles(kept) == [NIGHT_BEFORE, NUMBERED]


def test_air_date_matching_no_row_keeps_every_night():
    """A metadata mismatch must not leave an empty list."""
    rows = [_row(NIGHT_BEFORE), _row(OLD_NIGHT)]
    kept = drop_other_episodes(rows, (31, 20), air_date="2026-06-01")
    assert _titles(kept) == [NIGHT_BEFORE, OLD_NIGHT]


@pytest.mark.parametrize("air_date", [None, "", "_", "2026", "12/11/2026", "2026-13-4"])
def test_unknown_air_date_keeps_every_night(air_date):
    kept = drop_other_episodes(_daily_rows(), (31, 20), air_date=air_date)
    assert _titles(kept) == [NIGHT, NIGHT_BEFORE, OLD_NIGHT, NUMBERED]


def test_air_date_does_not_touch_numbered_or_undated_rows():
    rows = [_row(WANTED), _row("Six.Feet.Under.S02.1080p.AMZN.WEB-DL.H.264-FLUX")]
    kept = drop_other_episodes(rows, (2, 1), air_date="2002-03-03")
    assert len(kept) == 2


def test_router_passes_the_air_date_on():
    from resources.lib.router import _requested_episode_rows

    kept = _requested_episode_rows(
        _daily_rows(), "episode", "31", "20", air_date="2026-11-12"
    )
    assert _titles(kept) == [NIGHT, NUMBERED]


@patch("resources.lib.router_scriptplay._script_play_filter_autoselect_tag")
@patch("resources.lib.router_scriptplay._script_play_search_results")
def test_script_play_uses_the_players_premiered_token(mock_search, mock_tag):
    from resources.lib.router_scriptplay import _script_play_search_filter_tag

    mock_search.return_value = _daily_rows()
    _script_play_search_filter_tag(
        {"premiered": "2026-11-12"},
        "episode",
        "The Daily Show",
        "",
        {"season": "31", "episode": "20"},
        MagicMock(),
    )
    assert _titles(mock_tag.call_args.args[2]) == [NIGHT, NUMBERED]


def test_player_sends_the_episode_air_date():
    from resources.lib.player_installer import PLAYER_JSON

    assert "premiered={premiered}" in PLAYER_JSON["play_episode"]
    assert "premiered" not in PLAYER_JSON["play_movie"]


def test_retargeted_play_forgets_the_old_air_date():
    """A next-episode retarget must not pick the previous night's date."""
    from resources.lib.router_scriptplay import _retarget_episode_params

    params = {"season": "31", "episode": "20", "premiered": "2026-11-12"}
    _retarget_episode_params(params, "31", "21")
    assert "premiered" not in params
    assert params["episode"] == "21"
