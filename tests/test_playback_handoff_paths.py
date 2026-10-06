# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Playback boundaries must retain the selected movie or episode identity."""

import json
from unittest.mock import MagicMock

import pytest
import service
from resources.lib import nzbget_resolver, resolver, resolver_flow

EPISODE = {
    "mediatype": "episode",
    "title": "Imposter Syndrome",
    "tvshowtitle": "Shrinking",
    "season": 1,
    "episode": 6,
    "tmdb_id": "136311",
    "episode_tmdb_id": "424242",
    "year": 2023,
    "imdb": "tt15677150",
    "tvdb": "411364",
}
MOVIE = {"mediatype": "movie", "title": "Arrival", "tmdb_id": "329865"}


@pytest.fixture(name="playback_kodi")
def _playback_kodi_fixture(monkeypatch):
    """Keep window state observable at the instant Kodi receives the item."""
    properties = {}
    home = MagicMock()
    home.getProperty.side_effect = lambda key: properties.get(key, "")
    home.setProperty.side_effect = properties.__setitem__
    home.clearProperty.side_effect = lambda key: properties.pop(key, None)
    gui = MagicMock()
    gui.Window.return_value = home

    def make_item(path="", **_kwargs):
        item = MagicMock()
        item.getPath.return_value = path
        return item

    gui.ListItem.side_effect = make_item
    player = MagicMock()
    xbmc = MagicMock()
    xbmc.Player.return_value = player
    plugin = MagicMock()
    for module in (resolver, nzbget_resolver):
        monkeypatch.setattr(module, "xbmcgui", gui)
        monkeypatch.setattr(module, "xbmc", xbmc)
        monkeypatch.setattr(module, "xbmcplugin", plugin)
    from resources.lib import playback_context

    monkeypatch.setattr(playback_context, "xbmcgui", gui)
    monkeypatch.setattr(service, "xbmcgui", gui)
    monkeypatch.setattr(service, "_HOME_WINDOW", home)
    return player, plugin, home, properties


def _assert_handoff(item, metadata, properties):
    tag = item.getVideoInfoTag()
    tag.setMediaType.assert_called_with(metadata["mediatype"])
    tag.setTitle.assert_called_with(metadata["title"])
    context = json.loads(properties["TMDbHelper.PlayerInfoString"])
    assert str(context["tmdb_id"]) == metadata["tmdb_id"]
    assert context["tmdb_type"] == metadata["mediatype"]
    if metadata["mediatype"] == "episode":
        tag.setTvShowTitle.assert_called_with("Shrinking")
        tag.setSeason.assert_called_with(1)
        tag.setEpisode.assert_called_with(6)
        assert context["season"] == 1
        assert context["episode"] == 6
        assert str(context["tmdb_id"]) != metadata["episode_tmdb_id"]
    snapshot = json.loads(properties["nzbdav.playback_metadata"])
    assert snapshot["title"] == metadata["title"]
    item.setProperty.assert_any_call("StartOffset", "137.0")


@pytest.mark.parametrize("metadata", [EPISODE, MOVIE], ids=["episode", "movie"])
@pytest.mark.parametrize("entry", ["handle", "player"])
@pytest.mark.parametrize("mode", ["no_proxy", "proxy", "faststart_direct"])
def test_resolver_final_item_and_context_precede_playback(
    playback_kodi, metadata, entry, mode
):
    player, plugin, _home, properties = playback_kodi
    prepared = {
        "stream_url": "http://webdav.test/Shrinking.S01E06.mkv",
        "stream_headers": {},
        "_playback_metadata": dict(metadata),
    }
    if mode != "no_proxy":
        prepared.update(
            service_port=57800,
            proxy_url="http://127.0.0.1:57800/stream/session",
            stream_info={"direct": mode == "faststart_direct"},
        )
    if entry == "handle":

        def resolved(handle, succeeded, item):
            assert handle == 7 and succeeded
            _assert_handoff(item, metadata, properties)

        plugin.setResolvedUrl.side_effect = resolved
        resolver._finish_direct_playback(7, prepared, resume_seconds=137.0)
        plugin.setResolvedUrl.assert_called_once()
        player.play.assert_not_called()
    else:
        player.play.side_effect = lambda _url, item: _assert_handoff(
            item, metadata, properties
        )
        resolver._finish_player_playback(prepared, resume_seconds=137.0)
        player.play.assert_called_once()
        plugin.setResolvedUrl.assert_not_called()


@pytest.mark.parametrize("entry", ["handle", "player"])
@pytest.mark.parametrize("reuse", [False, True], ids=["download", "completed"])
def test_nzbget_final_item_has_episode_identity(
    monkeypatch, playback_kodi, entry, reuse
):
    player, plugin, _home, properties = playback_kodi
    params = {
        "nzburl": "http://indexer.test/episode.nzb",
        "title": "Shrinking.S01E06.2160p.REMUX",
        "_playback_metadata": dict(EPISODE),
    }
    if reuse:
        params["_nzbget_completed_job"] = {
            "name": params["title"],
            "status": "SUCCESS/UNPACK",
            "nzbid": 42,
            "dest_dir": "/dl/tv/Shrinking",
        }
    append = MagicMock(return_value=(42, None))
    monkeypatch.setattr(nzbget_resolver.nzbget_api, "append_nzb", append)
    monkeypatch.setattr(
        nzbget_resolver.nzbget_api, "completed_base_dir", lambda **_kw: "/dl"
    )
    monkeypatch.setattr(
        nzbget_resolver,
        "poll_nzbget_job",
        lambda *_a, **_kw: {"outcome": "success", "dest_dir": "/dl/tv/Shrinking"},
    )
    monkeypatch.setattr(
        nzbget_resolver,
        "resolve_smb_video",
        lambda *_a, **_kw: "smb://host/completed/tv/Shrinking/Shrinking.S01E06.mkv",
    )
    settings = {
        "nzbget_url": "http://box:6789",
        "nzbget_smb_root": "smb://host/completed",
        "download_timeout": "600",
    }

    def getter(key, default=""):
        return settings.get(key, default)

    if entry == "handle":

        def resolved(_handle, succeeded, item):
            assert succeeded
            _assert_handoff(item, EPISODE, properties)

        plugin.setResolvedUrl.side_effect = resolved
        nzbget_resolver.resolve_and_play_nzbget(
            7, params, settings_getter=getter, resume_seconds=137.0
        )
        plugin.setResolvedUrl.assert_called_once()
    else:
        player.play.side_effect = lambda _url, item: _assert_handoff(
            item, EPISODE, properties
        )
        nzbget_resolver.play_nzbget(
            params["nzburl"],
            params["title"],
            params=params,
            settings_getter=getter,
            resume_seconds=137.0,
        )
        player.play.assert_called_once()
    assert append.call_count == (0 if reuse else 1)


def test_service_reconnect_restores_snapshot_after_ipc_is_consumed(playback_kodi):
    _player, _plugin, _home, properties = playback_kodi
    properties.update(
        {
            "nzbdav.active": "true",
            "nzbdav.stream_url": "smb://host/completed/Shrinking.S01E06.mkv",
            "nzbdav.stream_title": "Shrinking.S01E06.mkv",
            "nzbdav.resume_offset": "137.0",
            "nzbdav.playback_metadata": json.dumps(EPISODE),
        }
    )
    player = service.NzbdavPlayer()
    player._check_active()
    assert "nzbdav.playback_metadata" not in properties
    properties["TMDbHelper.PlayerInfoString"] = "{}"
    player._monitor = MagicMock()
    player._monitor.waitForAbort.return_value = False
    player._await_playback_start = MagicMock(return_value=True)
    player.play = MagicMock(
        side_effect=lambda _url, item: _assert_handoff(item, EPISODE, properties)
    )

    assert player._retry_playback(3, 0)

    player.play.assert_called_once()
    player.onPlayBackStopped()
    assert not player._playback_metadata
    assert "nzbdav.playback_metadata" not in properties


@pytest.mark.parametrize("bad_metadata", ["broken JSON", "[]", "null"])
def test_service_does_not_reuse_previous_identity_on_invalid_snapshot(
    playback_kodi, bad_metadata
):
    _player, _plugin, _home, properties = playback_kodi
    properties.update(
        {"nzbdav.active": "true", "nzbdav.playback_metadata": bad_metadata}
    )
    player = service.NzbdavPlayer()
    player._playback_metadata = dict(EPISODE)
    player._check_active()
    assert not player._playback_metadata


@pytest.mark.parametrize("entry", ["handle", "player"])
def test_resolved_episode_metadata_reaches_ready_stream_handoff(
    monkeypatch, playback_kodi, entry
):
    player, plugin, _home, properties = playback_kodi
    params = {
        "type": "episode",
        "title": "Shrinking",
        "episode_title": "Imposter Syndrome",
        "tmdb_id": "136311",
        "tvshow_tmdb_id": "136311",
        "episode_tmdb_id": "424242",
        "season": "1",
        "episode": "6",
    }
    prepared = {
        "stream_url": "http://webdav.test/Shrinking.S01E06.mkv",
        "stream_headers": {},
    }
    monkeypatch.setattr(
        resolver_flow,
        "_prepare_ready_stream_for_handoff",
        lambda *_a: (prepared, 0, None),
    )
    monkeypatch.setattr(
        resolver_flow,
        "_prepare_player_ready_stream_for_handoff",
        lambda *_a: (prepared, 0, None),
    )
    monkeypatch.setattr(
        resolver, "_resolve_resume_choice", lambda *_a, **_kw: ("release", 137.0)
    )
    monkeypatch.setattr(resolver, "_arm_live_fallback_push", MagicMock())
    monkeypatch.setattr(resolver, "_signal_fallback_playback_started", MagicMock())
    if entry == "handle":

        def resolved(_handle, succeeded, item):
            assert succeeded
            _assert_handoff(item, EPISODE, properties)

        plugin.setResolvedUrl.side_effect = resolved
        resolver_flow._resolve_play_ready_stream(
            7, params, prepared["stream_url"], {}, None, None, None, None
        )
        plugin.setResolvedUrl.assert_called_once()
    else:
        params = resolver._resume_params_with_title(params, "Shrinking.S01E06.REMUX")
        player.play.side_effect = lambda _url, item: _assert_handoff(
            item, EPISODE, properties
        )
        resolver_flow._resolve_and_play_ready_stream(
            params, prepared["stream_url"], {}, None, None, None, None, None
        )
        player.play.assert_called_once()


@pytest.mark.parametrize("entry", ["handle", "player"])
def test_nzbget_failure_does_not_replace_active_scrobbling_context(
    monkeypatch, playback_kodi, entry
):
    player, plugin, _home, properties = playback_kodi
    properties["TMDbHelper.PlayerInfoString"] = "existing playing item"

    def fail_backend(_url, _title, _settings, _success, failure, **_kwargs):
        failure(None)

    monkeypatch.setattr(nzbget_resolver, "_run_nzbget_backend", fail_backend)
    params = {"title": "failed release", "_playback_metadata": dict(EPISODE)}
    if entry == "handle":
        nzbget_resolver.resolve_and_play_nzbget(7, params)
        assert plugin.setResolvedUrl.call_args.args[1] is False
    else:
        nzbget_resolver.play_nzbget(
            "http://indexer.test/x.nzb", params["title"], params
        )
        plugin.setResolvedUrl.assert_not_called()
    player.play.assert_not_called()
    assert properties == {"TMDbHelper.PlayerInfoString": "existing playing item"}


def test_service_stop_during_reconnect_delay_does_not_republish_context(playback_kodi):
    _player, _plugin, _home, properties = playback_kodi
    player = service.NzbdavPlayer()
    player._state = service.PlaybackState.MONITORING
    player._stream_url = "smb://host/Shrinking.S01E06.mkv"
    player._playback_metadata = dict(EPISODE)
    player.play = MagicMock()
    player._monitor = MagicMock()

    def stop_during_delay(_timeout):
        player.onPlayBackStopped()
        return False

    player._monitor.waitForAbort.side_effect = stop_during_delay
    assert not player._retry_playback(3, 0)
    player.play.assert_not_called()
    assert "TMDbHelper.PlayerInfoString" not in properties


def test_release_resume_title_preserves_movie_metadata():
    params = {"type": "movie", "title": "Arrival", "tmdb_id": "329865"}

    resumed = resolver._resume_params_with_title(params, "Arrival.2016.2160p.REMUX")

    assert resumed["title"] == "Arrival.2016.2160p.REMUX"
    assert resumed["_playback_metadata"]["title"] == "Arrival"
    assert resumed["_playback_metadata"]["tmdb_id"] == "329865"
    assert params["title"] == "Arrival"


@pytest.mark.parametrize("entry", ["retry", "handler"])
def test_replaced_session_during_retry_delay_cannot_publish_old_context(
    playback_kodi, entry
):
    _player, _plugin, _home, properties = playback_kodi
    monitor = service.NzbdavPlayer()
    monitor._state = service.PlaybackState.ERROR
    monitor._playback_metadata = dict(EPISODE)
    monitor._monitor = MagicMock()
    monitor.play = MagicMock()

    def new_session(_delay):
        properties.update(
            {
                "nzbdav.active": "true",
                "nzbdav.stream_url": "new-stream",
                "nzbdav.playback_metadata": json.dumps(MOVIE),
            }
        )
        monitor._check_active()
        properties["TMDbHelper.PlayerInfoString"] = "new-session"
        return False

    monitor._monitor.waitForAbort.side_effect = new_session
    if entry == "retry":
        assert not monitor._retry_playback(3, 0)
    else:
        monitor._read_settings = MagicMock(return_value=(True, 3, 0))
        monitor._handle_error_retry(0, "old")
    monitor.play.assert_not_called()
    assert properties["TMDbHelper.PlayerInfoString"] == "new-session"
    assert monitor._playback_metadata == MOVIE


@pytest.mark.parametrize("entry", ["retry", "handler"])
def test_pending_handoff_during_retry_delay_keeps_new_identity(playback_kodi, entry):
    _player, _plugin, _home, properties = playback_kodi
    monitor = service.NzbdavPlayer()
    monitor._state = service.PlaybackState.ERROR
    monitor._stream_url = "old-stream"
    monitor._playback_metadata = dict(EPISODE)
    monitor._monitor = MagicMock()
    monitor.play = MagicMock()
    monitor._read_settings = MagicMock(return_value=(True, 3, 0))

    def pending_handoff(_delay):
        properties.update(
            {
                "nzbdav.active": "true",
                "nzbdav.stream_url": "new-stream",
                "nzbdav.playback_metadata": json.dumps(MOVIE),
                "TMDbHelper.PlayerInfoString": json.dumps(
                    {"tmdb_type": "movie", "tmdb_id": "329865"}
                ),
            }
        )
        monitor.onAVStarted()
        return False

    monitor._monitor.waitForAbort.side_effect = pending_handoff
    if entry == "retry":
        assert not monitor._retry_playback(3, 0)
    else:
        monitor._handle_error_retry(0, "old")
    monitor.play.assert_not_called()
    assert properties["nzbdav.active"] == "true"
    assert json.loads(properties["nzbdav.playback_metadata"]) == MOVIE
    assert json.loads(properties["TMDbHelper.PlayerInfoString"])["tmdb_id"] == "329865"
    monitor._check_active()
    assert monitor._stream_url == "new-stream"
    assert monitor._playback_metadata == MOVIE
