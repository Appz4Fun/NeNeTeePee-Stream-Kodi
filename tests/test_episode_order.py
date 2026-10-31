# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Season-aware next-episode lookup (episode_order.py)."""

import json
from unittest.mock import patch

from resources.lib.episode_order import next_episode, season_episode_counts


class _FakeWindow:
    """Dict-backed stand-in for ``xbmcgui.Window(10000)`` property storage."""

    _store = {}

    def getProperty(self, key):
        return self._store.get(key, "")

    def setProperty(self, key, value):
        self._store[key] = value

    def clearProperty(self, key):
        self._store.pop(key, None)


def _patched_window():
    _FakeWindow._store = {}
    return patch(
        "resources.lib.episode_order.xbmcgui.Window",
        return_value=_FakeWindow(),
    )


def _http(routes):
    """Return an ``http_get`` serving JSON by URL substring, recording calls."""
    calls = []

    def http_get(url, timeout=15, max_bytes=None):
        calls.append(url)
        for fragment, payload in routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return json.dumps(payload)
        raise OSError("unrouted " + url)

    http_get.calls = calls
    return http_get


def _no_key(_key, default=""):
    return default


_TVMAZE_EPISODES = [
    {"season": 1, "number": 1},
    {"season": 1, "number": 2},
    {"season": 2, "number": None},  # special
    {"season": 2, "number": 1},
    {"season": 2, "number": 10},
    {"season": 3, "number": 1},
]


# --- next_episode ------------------------------------------------------------


def test_next_episode_stays_in_season_before_the_finale():
    assert next_episode(2, 10, {2: 11, 3: 8}) == (2, 11)


def test_next_episode_rolls_over_after_the_finale():
    assert next_episode(2, 10, {2: 10, 3: 8}) == (3, 1)


def test_next_episode_skips_seasons_without_episodes():
    assert next_episode("2", "10", {2: 10, 3: 0, 4: 6}) == (4, 1)


def test_next_episode_falls_back_to_plus_one_when_counts_unknown():
    assert next_episode(2, 10, {}) == (2, 11)
    assert next_episode(2, 10, {1: 5}) == (2, 11)


def test_next_episode_falls_back_to_plus_one_after_the_latest_known_season():
    assert next_episode(3, 8, {2: 10, 3: 8}) == (3, 9)


# --- season_episode_counts ---------------------------------------------------


def test_counts_come_from_tvmaze_without_a_tmdb_key():
    http_get = _http(
        {
            "lookup/shows?thetvdb=275274": {"id": 216},
            "shows/216/episodes": _TVMAZE_EPISODES,
        }
    )
    with _patched_window():
        counts = season_episode_counts(
            {"tvdb": "275274"}, settings_getter=_no_key, http_get=http_get
        )
    assert counts == {1: 2, 2: 10, 3: 1}
    assert not any("themoviedb" in url for url in http_get.calls)


def test_tvmaze_falls_back_to_imdb_when_tvdb_is_unknown():
    http_get = _http(
        {
            "thetvdb=1": OSError("404"),
            "lookup/shows?imdb=tt2861424": {"id": 216},
            "shows/216/episodes": _TVMAZE_EPISODES,
        }
    )
    with _patched_window():
        counts = season_episode_counts(
            {"tvdb": "1", "imdb": "tt2861424"},
            settings_getter=_no_key,
            http_get=http_get,
        )
    assert counts[2] == 10


def test_tmdb_is_preferred_when_a_key_is_configured():
    http_get = _http(
        {
            "find/275274": {"tv_results": [{"id": 60625}]},
            "tv/60625?": {
                "seasons": [
                    {"season_number": 0, "episode_count": 40},
                    {"season_number": 1, "episode_count": 11},
                    {"season_number": 2, "episode_count": 10},
                ]
            },
        }
    )
    with _patched_window():
        counts = season_episode_counts(
            {"tvdb": "275274", "tmdb_id": "11759797"},
            settings_getter=lambda key, default="": "secret",
            http_get=http_get,
        )
    assert counts == {1: 11, 2: 10}
    # The episode-level {tmdb_id} is never used as the series id.
    assert not any("11759797" in url for url in http_get.calls)


def test_tmdb_failure_falls_back_to_tvmaze():
    http_get = _http(
        {
            "themoviedb": OSError("down"),
            "lookup/shows?thetvdb=275274": {"id": 216},
            "shows/216/episodes": _TVMAZE_EPISODES,
        }
    )
    with _patched_window():
        counts = season_episode_counts(
            {"tvdb": "275274"},
            settings_getter=lambda key, default="": "secret",
            http_get=http_get,
        )
    assert counts[2] == 10


def test_counts_are_cached_for_the_session():
    http_get = _http(
        {
            "lookup/shows?thetvdb=275274": {"id": 216},
            "shows/216/episodes": _TVMAZE_EPISODES,
        }
    )
    with _patched_window():
        season_episode_counts(
            {"tvdb": "275274"}, settings_getter=_no_key, http_get=http_get
        )
        first = len(http_get.calls)
        counts = season_episode_counts(
            {"tvdb": "275274"}, settings_getter=_no_key, http_get=http_get
        )
    assert counts[2] == 10
    assert len(http_get.calls) == first


def test_counts_are_empty_without_series_ids_or_network():
    with _patched_window():
        assert season_episode_counts({"title": "From"}, http_get=_http({})) == {}
        assert (
            season_episode_counts(
                {"tvdb": "275274"}, settings_getter=_no_key, http_get=_http({})
            )
            == {}
        )


def test_tmdb_uses_the_explicit_series_id_without_find():
    http_get = _http(
        {
            "tv/60625?": {
                "seasons": [
                    {"season_number": 2, "episode_count": 10},
                    {"season_number": 3, "episode_count": 8},
                ]
            },
        }
    )
    with _patched_window():
        counts = season_episode_counts(
            {"tvshow_tmdb_id": "60625", "tmdb_id": "11759797"},
            settings_getter=lambda key, default="": "secret",
            http_get=http_get,
        )
    assert counts == {2: 10, 3: 8}
    assert not any("/find/" in url for url in http_get.calls)
    assert not any("tvmaze" in url for url in http_get.calls)


def test_series_tmdb_id_alone_needs_a_tmdb_key():
    http_get = _http({})
    with _patched_window():
        assert (
            season_episode_counts(
                {"tvshow_tmdb_id": "60625"}, settings_getter=_no_key, http_get=http_get
            )
            == {}
        )
    assert not http_get.calls
