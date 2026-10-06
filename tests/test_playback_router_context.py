# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Canonical identity must survive selection of a differently named release."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from resources.lib import router_play
from resources.lib.player_installer import PLAYER_JSON
from resources.lib.script_player import parse_script_args


@pytest.mark.parametrize("route", ["play_movie", "play_episode"])
def test_profile_uses_explicit_ids_and_keeps_runscript(route):
    command = PLAYER_JSON[route]
    assert command.startswith("executebuiltin://RunScript(")
    assert "{tmdb_id}" not in command
    assert "tmdb_id={tmdb}" in command
    values = {
        "title_url": "Imposter+Syndrome",
        "year": "2023",
        "imdb": "tt15677150",
        "tmdb": "136311",
        "showname_url": "Shrinking",
        "showyear": "2023",
        "season": "1",
        "episode": "6",
        "tvdb": "411364",
        "eptmdb": "424242",
        "ep_showseason": "1",
        "ep_showepisode": "6",
    }
    params = parse_script_args(command.format(**values)[:-1].split(",")[2:])
    if route == "play_episode":
        assert params["tvshow_tmdb_id"] == "136311"
        assert params["episode_tmdb_id"] == "424242"
        assert params["episode_title"] == "Imposter Syndrome"
        assert params["title"] == "Shrinking"
    shipped = (
        Path(__file__).resolve().parents[1]
        / "repo/plugin.video.nzbdav/resources/players/nzbdav.json"
    )
    assert json.loads(shipped.read_text()) == PLAYER_JSON
    assert PLAYER_JSON["schema_version"] >= 8


@pytest.mark.parametrize("auto_select", [False, True])
@pytest.mark.parametrize("media_type", ["movie", "episode"])
def test_handle_picker_retains_identity_separate_from_release(auto_select, media_type):
    params = {
        "type": media_type,
        "title": "Canonical title",
        "year": "2023",
        "tmdb_id": "136311",
        "tvshow_tmdb_id": "136311",
        "episode_tmdb_id": "424242",
        "episode_title": "Imposter Syndrome",
    }
    identity = router_play._play_identity(params, params["title"], "1", "6")
    release = {"title": "Release.Name.2160p.mkv", "link": "https://example.invalid/nzb"}
    with patch("resources.lib.router_play._attach_nzbget_dupe"), patch(
        "resources.lib.router_play._ensure_nzbget_completed_hint"
    ), patch("resources.lib.router_play._selection_fallback_loader"), patch(
        "resources.lib.resolver.resolve"
    ) as resolve:
        if auto_select:
            router_play._handle_play_auto_select(7, release, [release], identity)
        else:
            router_play._handle_play_resolve_selection(
                7, release, [release], {}, identity
            )
    passed = resolve.call_args.args[1]
    assert passed["title"] == release["title"]
    assert passed["_playback_metadata"]["title"] == (
        "Imposter Syndrome" if media_type == "episode" else "Canonical title"
    )
    if media_type == "episode":
        assert passed["_playback_metadata"]["tmdb_id"] == "136311"
        assert passed["_playback_metadata"]["episode_tmdb_id"] == "424242"


def test_direct_url_playback_publishes_movie_context_before_resolving():
    from resources.lib import router

    params = {
        "type": "movie",
        "title": "The Matrix",
        "tmdb_id": "603",
        "primary_url": "https://example.invalid/movie.mkv",
    }
    properties = {}
    with patch("resources.lib.router.xbmcgui") as gui, patch(
        "resources.lib.router._direct_play_head_length", return_value=(123, "")
    ), patch(
        "resources.lib.resolver._direct_playback_service_config",
        return_value=(54321, "token"),
    ), patch(
        "resources.lib.resolver._prepare_direct_playback",
        return_value="http://127.0.0.1:54321/stream/1",
    ), patch(
        "resources.lib.router.xbmcplugin.setResolvedUrl"
    ) as resolved:
        window = gui.Window.return_value
        window.getProperty.side_effect = lambda key: properties.get(key, "")
        window.setProperty.side_effect = properties.__setitem__
        window.clearProperty.side_effect = lambda key: properties.pop(key, None)

        def check_item(_handle, success, item):
            assert success
            item.getVideoInfoTag.return_value.setTitle.assert_called_once_with(
                "The Matrix"
            )
            assert (
                json.loads(properties["TMDbHelper.PlayerInfoString"])["tmdb_id"]
                == "603"
            )

        resolved.side_effect = check_item
        router._handle_direct_play(7, params)
        resolved.assert_called_once()


@pytest.mark.parametrize("auto_select", [False, True])
def test_season_pack_selection_retains_requested_episode_identity(auto_select):
    params = {
        "type": "episode",
        "title": "Shrinking",
        "tvshow_tmdb_id": "136311",
        "episode_tmdb_id": "424242",
        "episode_title": "Imposter Syndrome",
    }
    identity = router_play._play_identity(params, "Shrinking", "1", "6")
    pack = {
        "title": "Shrinking.S01.COMPLETE",
        "link": "",
        "_season_pack": {"job_id": "pack"},
    }
    with patch("resources.lib.router_play._attach_nzbget_dupe"), patch(
        "resources.lib.router_play._ensure_nzbget_completed_hint"
    ), patch("resources.lib.resolver.resolve") as resolve:
        if auto_select:
            router_play._handle_play_auto_select(7, pack, [pack], identity)
        else:
            router_play._handle_play_resolve_selection(7, pack, [pack], {}, identity)
    result = resolve.call_args.args[1]
    assert result["_season_pack"] == {"job_id": "pack"}
    assert result["_playback_metadata"]["title"] == "Imposter Syndrome"
    assert result["_playback_metadata"]["tmdb_id"] == "136311"
    assert (
        result["_playback_metadata"]["season"],
        result["_playback_metadata"]["episode"],
    ) == (1, 6)


@pytest.mark.parametrize("state", ["MONITORING", "ERROR"])
@pytest.mark.parametrize("native_failure", [False, True])
def test_direct_proxy_handoff_retires_old_monitor_and_keeps_new_proxy(
    state, native_failure
):
    from unittest.mock import MagicMock

    import service
    from resources.lib import router

    properties = {
        "nzbdav.playback_session": "old",
        "nzbdav.pending_playback_session": "old",
        "nzbdav.playing": "true",
        "nzbdav.proxy_port": "54321",
    }
    with patch("resources.lib.router.xbmcgui") as gui, patch(
        "resources.lib.router._direct_play_head_length", return_value=(123, "")
    ), patch(
        "resources.lib.resolver._direct_playback_service_config",
        return_value=(54321, "token"),
    ), patch(
        "resources.lib.resolver._prepare_direct_playback",
        return_value="http://127.0.0.1:54321/stream/new",
    ), patch(
        "resources.lib.router.xbmcplugin.setResolvedUrl"
    ) as resolved:

        def native_result(_handle, success, _item):
            if success and native_failure:
                raise RuntimeError("Native resolution failed")

        resolved.side_effect = native_result
        home = gui.Window.return_value
        home.getProperty.side_effect = lambda key: properties.get(key, "")
        home.setProperty.side_effect = properties.__setitem__
        home.clearProperty.side_effect = lambda key: properties.pop(key, None)
        with patch.object(service, "_HOME_WINDOW", home):
            proxy = MagicMock()
            monitor = service.NzbdavPlayer(proxy=proxy)
            monitor._state = getattr(service.PlaybackState, state)
            monitor._playback_session = "old"
            monitor._handle_error_retry = MagicMock()
            router._handle_direct_play(
                7,
                {
                    "primary_url": "https://example.invalid/movie.mkv",
                    "type": "movie",
                    "title": "The Matrix",
                    "tmdb_id": "603",
                },
            )
            assert resolved.call_args.args[1] is not native_failure
            assert not properties.get("nzbdav.playing")
            monitor.tick()
            assert monitor._state == service.PlaybackState.IDLE
            monitor._handle_error_retry.assert_not_called()
            if native_failure:
                proxy.clear_sessions.assert_called_once()
                assert not properties.get("TMDbHelper.PlayerInfoString")
                return
            proxy.clear_sessions.assert_not_called()
            assert (
                json.loads(properties["TMDbHelper.PlayerInfoString"])["tmdb_id"]
                == "603"
            )
