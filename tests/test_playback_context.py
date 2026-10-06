# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Identity presented to Kodi and TMDb Helper at the playback boundary."""

import json
from unittest.mock import MagicMock

import pytest

EPISODE_PARAMS = {
    "type": "episode",
    "title": "Shrinking",
    "year": "2023",
    "season": "1",
    "episode": "6",
    "tvshow_tmdb_id": "136311",
    "tmdb_id": "136311",
    "episode_tmdb_id": "424242",
    "episode_title": "Imposter Syndrome",
    "imdb": "tt15677150",
    "tvdb": "411364",
}


@pytest.fixture(name="home")
def home_window():
    values = {"TMDbHelper.PlayerInfoString": '{"tmdb_id":"wrong"}'}
    window = MagicMock()
    window.setProperty.side_effect = values.__setitem__
    window.getProperty.side_effect = lambda key: values.get(key, "")
    window.clearProperty.side_effect = lambda key: values.pop(key, None)
    return window


def test_episode_item_and_scrobbler_use_distinct_episode_and_series_ids(home):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    metadata = metadata_from_params(EPISODE_PARAMS)
    item = MagicMock()
    prepare_playback(item, metadata, home)
    tag = item.getVideoInfoTag.return_value
    tag.setMediaType.assert_called_once_with("episode")
    tag.setTitle.assert_called_once_with("Imposter Syndrome")
    tag.setTvShowTitle.assert_called_once_with("Shrinking")
    tag.setSeason.assert_called_once_with(1)
    tag.setEpisode.assert_called_once_with(6)
    ids = tag.setUniqueIDs.call_args.args[0]
    assert ids["tmdb"] == "424242"
    assert ids["tvshow.tmdb"] == "136311"
    assert "imdb" not in ids  # Series IMDb is never the episode's unique ID.
    assert ids["tvshow.imdb"] == "tt15677150"
    assert json.loads(home.getProperty("TMDbHelper.PlayerInfoString")) == {
        "tmdb_type": "episode",
        "tmdb_id": "136311",
        "imdb_id": "tt15677150",
        "tvdb_id": "411364",
        "season": 1,
        "episode": 6,
    }
    assert json.loads(home.getProperty("nzbdav.playback_metadata")) == metadata


def test_movie_keeps_canonical_title_and_movie_ids(home):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    metadata = metadata_from_params(
        {"type": "movie", "title": "The Matrix", "year": "1999", "tmdb_id": "603"}
    )
    item = MagicMock()
    prepare_playback(item, metadata, home)
    tag = item.getVideoInfoTag.return_value
    tag.setMediaType.assert_called_once_with("movie")
    tag.setTitle.assert_called_once_with("The Matrix")
    tag.setYear.assert_called_once_with(1999)
    tag.setUniqueIDs.assert_called_once_with({"tmdb": "603"}, "tmdb")
    assert json.loads(home.getProperty("TMDbHelper.PlayerInfoString")) == {
        "tmdb_type": "movie",
        "tmdb_id": "603",
    }


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"type": "episode", "tmdb_id": "9579997", "season": "1", "episode": "6"},
        {"type": "movie", "tmdb_id": "_"},
        {"type": "movie", "tmdb_id": "²"},
    ],
)
def test_unknown_or_legacy_identity_cannot_scrobble_the_previous_item(home, params):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    prepare_playback(MagicMock(), metadata_from_params(params), home)
    assert home.getProperty("TMDbHelper.PlayerInfoString") == ""


@pytest.mark.parametrize(
    "field,value",
    [
        ("season", "_"),
        ("episode", "{episode}"),
        ("episode", "-1"),
        ("tvshow_tmdb_id", "nonsense"),
    ],
)
def test_incomplete_episode_has_no_scrobbling_identity(home, field, value):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    params = dict(EPISODE_PARAMS, **{field: value})
    prepare_playback(MagicMock(), metadata_from_params(params), home)
    assert home.getProperty("TMDbHelper.PlayerInfoString") == ""


def test_metadata_snapshot_survives_release_title_and_strips_non_metadata():
    from resources.lib.playback_context import metadata_from_params

    metadata = metadata_from_params(EPISODE_PARAMS)
    params = {
        "title": "Release.Name.2160p.mkv",
        "_playback_metadata": metadata,
        "_settings_getter": lambda: None,
        "nzburl": "https://private.invalid/?apikey=secret",
    }
    assert metadata_from_params(params) == metadata
    assert "secret" not in json.dumps(metadata)


def test_special_retains_season_zero_metadata(home):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    item = MagicMock()
    prepare_playback(item, metadata_from_params(dict(EPISODE_PARAMS, season="0")), home)
    item.getVideoInfoTag.return_value.setSeason.assert_called_once_with(0)


@pytest.mark.parametrize(
    "bad_id", [True, False, "0", "-1", "١٢", "²", "{tmdb}", "abc", [], {}]
)
def test_invalid_movie_id_clears_previous_context(home, bad_id):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    prepare_playback(
        MagicMock(), metadata_from_params({"type": "movie", "tmdb_id": bad_id}), home
    )
    assert home.getProperty("TMDbHelper.PlayerInfoString") == ""


def test_special_clears_unsupported_scrobble_context(home):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    prepare_playback(
        MagicMock(), metadata_from_params(dict(EPISODE_PARAMS, season="0")), home
    )
    assert home.getProperty("TMDbHelper.PlayerInfoString") == ""


def test_native_metadata_failure_clears_context_without_breaking_playback_boundary(
    home,
):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    item = MagicMock()
    item.getVideoInfoTag.side_effect = RuntimeError("Kodi item is unavailable")
    prepare_playback(
        item,
        metadata_from_params(
            {"type": "movie", "title": "Predestination", "tmdb_id": "206487"}
        ),
        home,
    )
    assert home.getProperty("TMDbHelper.PlayerInfoString") == ""
    assert json.loads(home.getProperty("nzbdav.playback_metadata")) == {}
