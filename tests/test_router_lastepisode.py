# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Session-scoped last-picked-episode memory (router_lastepisode.py)."""

from unittest.mock import patch

from resources.lib.router_lastepisode import (
    _show_identity_key,
    mark_last_episode_completed,
    recall_next_episode,
    recall_stale_premiere,
    remember_last_episode,
)


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
        "resources.lib.router_lastepisode.xbmcgui.Window",
        return_value=_FakeWindow(),
    )


def test_show_identity_key_prefers_series_ids_over_episode_tmdb_id():
    # TMDBHelper fills {tmdb_id} with the EPISODE's id on an episode play, so
    # keying on it first would file every episode as a different show.
    params = {"tmdb_id": "11759797", "tvdb": "275274", "imdb": "tt2861424"}
    assert _show_identity_key(params) == "tvdb:275274"
    assert _show_identity_key({"imdb": "tt2861424"}) == "imdb:tt2861424"
    assert _show_identity_key({"tmdb_id": "11759797"}) == "tmdb_id:11759797"


def test_show_identity_key_is_stable_across_episodes_of_one_show():
    s09e07 = {"tmdb_id": "11759797", "tvdb": "275274", "imdb": "tt2861424"}
    s09e01 = {"tmdb_id": "11759791", "tvdb": "275274", "imdb": "tt2861424"}
    assert _show_identity_key(s09e07) == _show_identity_key(s09e01)


def test_show_identity_key_falls_back_to_casefolded_title():
    assert _show_identity_key({"title": "From"}) == "title:from"
    assert _show_identity_key({}) == ""


def test_remember_then_recall_returns_next_episode():
    with _patched_window():
        params = {"tvdb": "124364", "title": "From"}
        remember_last_episode(params, "3", "5")
        assert recall_next_episode(params) == ("3", "6")


def test_recall_returns_blank_for_a_different_show():
    with _patched_window():
        remember_last_episode({"tvdb": "124364"}, "3", "5")
        assert recall_next_episode({"tvdb": "999"}) == ("", "")


def test_recall_returns_blank_when_nothing_remembered():
    with _patched_window():
        assert recall_next_episode({"tvdb": "124364"}) == ("", "")


def test_remember_is_a_noop_for_non_numeric_season_or_episode():
    with _patched_window():
        remember_last_episode({"tvdb": "124364"}, "", "")
        assert recall_next_episode({"tvdb": "124364"}) == ("", "")


def test_remember_is_a_noop_without_a_show_identity():
    with _patched_window():
        remember_last_episode({}, "3", "5")
        assert recall_next_episode({"title": ""}) == ("", "")


# --- stale season-premiere guard -------------------------------------------


def _rick_and_morty(tmdb_id):
    return {"tmdb_id": tmdb_id, "tvdb": "275274", "imdb": "tt2861424"}


def test_stale_premiere_retargets_the_episode_after_the_one_that_finished():
    # The reported failure: S09E07 finishes, a play auto-fires for S09E01.
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == (
            "9",
            "8",
        )


def test_stale_premiere_is_one_shot():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1")[1] == "8"
        # Immediately asking for the premiere again is taken at face value.
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == ("", "")


def test_stale_premiere_ignores_a_stopped_episode():
    # No natural end => the user bailed out, so a premiere play is deliberate.
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == ("", "")


def test_stale_premiere_ignores_a_replay_of_the_premiere():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759791"), "9", "1")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == ("", "")


def test_stale_premiere_ignores_a_different_season():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "10", "1") == ("", "")


def test_stale_premiere_ignores_a_different_show():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere({"tvdb": "999"}, "9", "1") == ("", "")


def test_stale_premiere_ignores_episodes_other_than_one():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759798"), "9", "8") == ("", "")


def test_stale_premiere_ignores_blank_or_non_numeric_args():
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("1"), "", "") == ("", "")
        assert recall_stale_premiere(_rick_and_morty("1"), "9", "one") == ("", "")


def test_a_new_pick_clears_the_completed_flag():
    # A movie play records nothing but must still invalidate the stale flag.
    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        movie = {"tmdb_id": "1290159", "title": "A House of Dynamite"}
        remember_last_episode(movie, "", "")
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == ("", "")


# --- season rollover --------------------------------------------------------


def _with_counts(counts):
    return patch(
        "resources.lib.episode_order.season_episode_counts", return_value=counts
    )


def test_recall_rolls_over_to_the_next_season_after_a_finale():
    with _patched_window(), _with_counts({2: 10, 3: 8}):
        params = {"tvdb": "124364"}
        remember_last_episode(params, "2", "10")
        assert recall_next_episode(params) == ("3", "1")


def test_recall_stays_in_season_when_more_episodes_remain():
    with _patched_window(), _with_counts({2: 11, 3: 8}):
        params = {"tvdb": "124364"}
        remember_last_episode(params, "2", "10")
        assert recall_next_episode(params) == ("2", "11")


def test_stale_premiere_after_a_finale_targets_the_next_season():
    with _patched_window(), _with_counts({9: 7, 10: 10}):
        remember_last_episode(_rick_and_morty("11759797"), "9", "7")
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "9", "1") == (
            "10",
            "1",
        )


def test_recall_falls_back_to_plus_one_when_the_lookup_raises():
    with _patched_window(), patch(
        "resources.lib.episode_order.season_episode_counts",
        side_effect=RuntimeError("boom"),
    ):
        params = {"tvdb": "124364"}
        remember_last_episode(params, "2", "10")
        assert recall_next_episode(params) == ("2", "11")


# --- completion only for the playback the pick started ----------------------


def test_movie_ending_after_a_stopped_episode_does_not_arm_the_guard():
    from resources.lib.router_lastepisode import disarm_last_episode

    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "2", "7")
        disarm_last_episode()  # S02E07 was stopped, not finished
        remember_last_episode({"imdb": "tt0133093", "title": "The Matrix"}, "", "")
        mark_last_episode_completed()  # the movie ends naturally
        assert recall_stale_premiere(_rick_and_morty("11759791"), "2", "1") == ("", "")
        assert recall_next_episode(_rick_and_morty("")) == ("", "")


def test_stopped_episode_cannot_be_completed_by_a_later_ending():
    from resources.lib.router_lastepisode import disarm_last_episode

    with _patched_window():
        remember_last_episode(_rick_and_morty("11759797"), "2", "7")
        disarm_last_episode()
        mark_last_episode_completed()
        assert recall_stale_premiere(_rick_and_morty("11759791"), "2", "1") == ("", "")


def test_series_tmdb_id_identifies_the_show_when_tvdb_and_imdb_are_missing():
    with _patched_window():
        remember_last_episode({"tvshow_tmdb_id": "60625", "tmdb_id": "1"}, "2", "4")
        assert recall_next_episode({"tvshow_tmdb_id": "60625", "tmdb_id": "2"}) == (
            "2",
            "5",
        )
