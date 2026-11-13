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
        "Six Feet Under - 2x01 - In the Game",
        "The.Daily.Show.2026.11.12.Guest.1080p.WEB.h264-GRP",
        "aBcD1234eFgH5678",
    ],
)
def test_rows_that_may_hold_the_episode_are_kept(title):
    """Multi-episode files, packs for the season, and names with no numbers."""
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
    assert drop_other_episodes([_row(title)], (2, 1)) == []


def test_seasonless_episode_number_is_judged_only_in_season_one():
    """From season 2 on a bare number may be an absolute count (anime)."""
    bare = "[Erai-raws] Frieren - 05 [1080p].mkv"
    assert drop_other_episodes([_row(bare)], (1, 5)) != []
    assert drop_other_episodes([_row(bare)], (1, 6)) == []
    assert _titles(drop_other_episodes([_row(bare)], (2, 6))) == [bare]


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
    assert drop_other_episodes([], (2, 1)) == []
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

    rows = [_row("Alien.1979.1080p.BluRay.DDP.5.1.x264-hallowed"), _row(OTHER)]
    mock_search.return_value = rows
    _script_play_search_filter_tag(
        {}, "movie", "Alien", "1979", {"season": "", "episode": ""}, MagicMock()
    )
    assert mock_tag.call_args.args[2] is rows
