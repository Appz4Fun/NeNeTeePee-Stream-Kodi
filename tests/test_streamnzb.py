# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""StreamNZB API contracts, secret handling, Kodi completion and routing."""

import json
import xml.etree.ElementTree as ET
from http.client import HTTPException
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest
from resources.lib import playback_backend, router, streamnzb, streamnzb_player

MOVIE = {"type": "movie", "imdb": "tt0133093", "title": "The Matrix"}
EPISODE = {"type": "episode", "tmdb_id": "1399", "season": "0", "episode": "1"}
TOKEN = "private/token+secret"
URL = "https://stream.example/private-token/play/movie:tt0133093:0?next=1&x=%2B"
SETTINGS = {
    "playback_backend": "2",
    "streamnzb_url": "https://stream.example",
    "streamnzb_token": TOKEN,
}


def settings(key, default=""):
    """A settings reader with explicit defaults, like the router's readers."""
    return SETTINGS.get(key, default)


def test_episode_metadata_uses_kodi_api_methods():
    info = MagicMock(
        spec=[
            "setTitle",
            "setPlot",
            "setMediaType",
            "setTvShowTitle",
            "setSeason",
            "setEpisode",
        ]
    )
    with patch.object(streamnzb_player.xbmcgui, "ListItem") as item:
        item.return_value.getVideoInfoTag.return_value = info
        streamnzb_player._playback_listitem(
            streamnzb.StreamEntry("Release", "", URL), dict(EPISODE, title="Show")
        )
    info.setTvShowTitle.assert_called_once_with("Show")
    info.setSeason.assert_called_once_with(0)
    info.setEpisode.assert_called_once_with(1)


def test_tmdbhelper_episode_template_uses_show_tmdb_placeholder():
    from resources.lib.player_installer import PLAYER_JSON

    assert "tmdb_id={tmdb}," in PLAYER_JSON["play_episode"]
    assert "tmdb_id={tmdb_id}" in PLAYER_JSON["play_movie"]


@pytest.mark.parametrize(
    "values,expected",
    [
        ({}, "nzbdav"),
        ({"nzbget_enabled": "true"}, "nzbget"),
        ({"playback_backend": "0", "nzbget_enabled": "true"}, "nzbdav"),
        ({"playback_backend": "1"}, "nzbget"),
        ({"playback_backend": "2"}, "streamnzb"),
    ],
)
def test_backend_values(values, expected):
    assert (
        playback_backend.get_backend(lambda key, default="": values.get(key, default))
        == expected
    )


@pytest.mark.parametrize("legacy,expected", [("true", "1"), ("false", "0")])
def test_migrate_legacy_once(legacy, expected):
    with patch.object(
        router,
        "_get_script_setting",
        side_effect=lambda key, default="": (
            legacy if key == "nzbget_enabled" else default
        ),
    ), patch.object(playback_backend.xbmcaddon, "Addon") as addon:
        playback_backend.migrate_backend()
        addon.return_value.setSetting.assert_called_once_with(
            "playback_backend", expected
        )
    with patch.object(router, "_get_script_setting", return_value="2"), patch.object(
        playback_backend.xbmcaddon, "Addon"
    ) as addon:
        playback_backend.migrate_backend()
        addon.assert_not_called()


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"playback_backend": "1"}, 5),
        ({"playback_backend": "1", "nzbget_enabled": "false"}, 5),
        ({"playback_backend": "0", "nzbget_enabled": "true"}, None),
        ({"playback_backend": "2", "nzbget_enabled": "true"}, None),
        ({"nzbget_enabled": "true"}, 5),
        ({"playback_backend": "1", "fallback_streams_enabled": "false"}, None),
    ],
)
def test_nzbget_backup_gate_uses_dropdown(values, expected):
    from resources.lib.router_play import _dupe_max_backups

    assert _dupe_max_backups(values.get) == expected


def test_settings_dropdown_and_masked_token():
    path = Path(__file__).parents[1] / "repo/plugin.video.nzbdav/resources/settings.xml"
    root = ET.parse(path).getroot()
    backend = root.find(".//setting[@id='playback_backend']")
    assert [node.text for node in backend.findall("constraints/options/option")] == [
        "0",
        "1",
        "2",
    ]
    assert backend.findtext("default") == "0"
    assert backend.find("control").get("type") == "list"
    assert root.find(".//setting[@id='nzbget_enabled']") is None
    assert root.findtext(".//setting[@id='streamnzb_token']/control/hidden") == "true"
    assert not root.findtext(".//setting[@id='streamnzb_url']/default")


@pytest.mark.parametrize(
    "params,expected",
    [
        (MOVIE, ("movie", "tt0133093")),
        ({"tmdb_id": "603"}, ("movie", "tmdb:603")),
        ({"tvdb": "123"}, ("movie", "tvdb:123")),
        (EPISODE, ("series", "tmdb:1399:0:1")),
        (
            {"type": "episode", "tvdb": "121361", "ep_season": "2", "ep_episode": "7"},
            ("series", "tvdb:121361:2:7"),
        ),
        (
            {
                "type": "episode",
                "imdb": "tt0944947",
                "season": "3",
                "episode": "2",
                "ep_season": "1",
                "ep_episode": "26",
            },
            ("series", "tt0944947:3:2"),
        ),
        (
            {"type": "episode", "imdb": "tt0944947", "season": "0", "episode": "0"},
            ("series", "tt0944947:0:0"),
        ),
    ],
)
def test_content_identity(params, expected):
    assert streamnzb.content_identity(params) == expected


@pytest.mark.parametrize(
    "params",
    [
        {"title": "The Matrix"},
        {"imdb": "tt1"},
        {"tmdb_id": "12/3"},
        {"type": "episode", "imdb": "tt0944947"},
        dict(EPISODE, season="-1"),
        dict(EPISODE, episode="1.5"),
    ],
)
def test_missing_or_invalid_identity(params):
    with pytest.raises(streamnzb.StreamNZBError):
        streamnzb.content_identity(params)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "localhost:7000",
        "file:///tmp/movie",
        "https://user:pass@stream.example",
        "http://stream.example:bad",
        "https://stream.example/manifest.json",
        "https://stream.example?token=secret",
        "http://stream.example|Authorization=secret",
    ],
)
def test_invalid_base(value):
    with pytest.raises(streamnzb.StreamNZBError):
        streamnzb.normalize_base_url(value)


def test_fetch_constructs_encoded_request_and_preserves_order():
    response = {
        "streams": [
            {"name": "Second", "url": URL},
            {"name": "First", "url": "http://stream.example/play/other"},
        ]
    }
    with patch.object(streamnzb, "http_get", return_value=json.dumps(response)) as get:
        entries = streamnzb.fetch_streams(
            " https://stream.example/prefix/ ", TOKEN, EPISODE
        )
    get.assert_called_once_with(
        "https://stream.example/prefix/private%2Ftoken%2Bsecret/stream/series/tmdb%3A1399%3A0%3A1.json",
        timeout=35,
        max_bytes=4194304,
    )
    assert [entry.name for entry in entries] == ["Second", "First"]
    assert entries[0].url == URL
    assert "private-token" not in repr(entries)


def test_movie_request_path():
    with patch.object(streamnzb, "http_get", return_value='{"streams": []}') as get:
        assert not streamnzb.fetch_streams("http://stream.example/", "secret", MOVIE)
    assert (
        get.call_args.args[0]
        == "http://stream.example/secret/stream/movie/tt0133093.json"
    )


@pytest.mark.parametrize("payload", [[], {}, {"streams": {}}, {"streams": None}])
def test_invalid_response_schema(payload):
    with pytest.raises(streamnzb.StreamNZBError):
        streamnzb.parse_streams(payload)


def test_unsupported_entries_and_headers():
    rows = [
        None,
        {"externalUrl": "http://stream.example/debug"},
        {"url": "magnet:?xt=anything"},
        {"url": "file:///tmp/movie"},
        {"url": "http://stream.example/play|X=1"},
        {"url": "http://stream.example:bad/play"},
        {"url": URL, "behaviorHints": "bad"},
        {"url": URL, "behaviorHints": {"proxyHeaders": {"request": {"X": "bad\r\n"}}}},
        {
            "url": URL,
            "behaviorHints": {
                "proxyHeaders": {
                    "request": {
                        "Authorization": "Bearer secret+=&",
                        "User-Agent": "Kodi test",
                    }
                }
            },
        },
    ]
    entries = streamnzb.parse_streams({"streams": rows})
    assert len(entries) == 1
    assert entries[0].headers == {
        "Authorization": "Bearer secret+=&",
        "User-Agent": "Kodi test",
    }


def test_redact_display_text_and_kodi_markup():
    text = (
        TOKEN
        + " "
        + "private%2Ftoken%2Bsecret"
        + " [COLOR red]hello[/COLOR] https://stream.example/other-secret/play/slot?token=another-secret"
    )
    display = streamnzb.safe_display(text, TOKEN)
    for secret in (
        TOKEN,
        "private%2Ftoken%2Bsecret",
        "other-secret",
        "another-secret",
        "[COLOR",
    ):
        assert secret not in display


@pytest.mark.parametrize(
    "error",
    [
        HTTPError(URL, 401, TOKEN, {}, None),
        HTTPError(URL, 403, TOKEN, {}, None),
        HTTPError(URL, 500, TOKEN, {}, None),
        URLError(TOKEN),
        TimeoutError(TOKEN),
        OSError(TOKEN),
        HTTPException(TOKEN),
    ],
)
def test_http_errors_are_safe(error):
    with patch.object(streamnzb, "http_get", side_effect=error), pytest.raises(
        streamnzb.StreamNZBError
    ) as raised:
        streamnzb.fetch_streams("http://stream.example", TOKEN, MOVIE)
    assert TOKEN not in str(raised.value)
    assert URL not in str(raised.value)


def test_bad_json():
    with patch.object(
        streamnzb, "http_get", return_value="<html>secret</html>"
    ), pytest.raises(streamnzb.StreamNZBError, match="malformed JSON"):
        streamnzb.fetch_streams("http://stream.example", TOKEN, MOVIE)


@pytest.mark.parametrize("cancel_results", [[True], [False, True]])
def test_abort_before_and_after_request(cancel_results):
    cancelled = MagicMock(side_effect=cancel_results)
    with patch.object(
        streamnzb, "http_get", return_value='{"streams": []}'
    ) as get, pytest.raises(streamnzb.StreamNZBCancelled):
        streamnzb.fetch_streams("http://stream.example", TOKEN, MOVIE, cancelled)
    assert get.call_count == (len(cancel_results) - 1)


def test_cancel_after_response_takes_precedence_over_malformed_json():
    cancelled = MagicMock(side_effect=[False, True])
    with patch.object(streamnzb, "http_get", return_value="invalid"), pytest.raises(
        streamnzb.StreamNZBCancelled
    ):
        streamnzb.fetch_streams("http://stream.example", TOKEN, MOVIE, cancelled)


@pytest.fixture(name="player_mocks")
def _player_mocks():
    """Module-bound Kodi mocks; ensure the player never enters NZB machinery."""
    with patch.object(streamnzb_player, "xbmc") as kodi, patch.object(
        streamnzb_player, "xbmcgui"
    ) as gui, patch.object(streamnzb_player, "xbmcplugin") as plugin, patch.object(
        streamnzb_player, "fetch_streams"
    ) as fetch, patch.object(
        streamnzb_player, "_open_loading_dialog"
    ) as opened, patch.object(
        streamnzb_player, "_close_loading_dialog"
    ) as closed, patch.object(
        streamnzb_player, "notify"
    ) as notify_mock, patch(
        "resources.lib.resolver._clear_kodi_playback_state"
    ) as clear, patch(
        "resources.lib.resolver._read_stored_resume", return_value=0.0
    ) as stored, patch(
        "resources.lib.resume_choice.choose_resume_seconds",
        side_effect=lambda _key, seconds: seconds,
    ) as choose, patch(
        "resources.lib.resolver._preserve_resume_on_cancel"
    ) as preserve, patch(
        "resources.lib.resume_store.clear_resume"
    ) as consumed, patch.object(
        streamnzb_player, "show_results_dialog"
    ) as picker:
        clear.return_value = 0.0
        kodi.Monitor.return_value.abortRequested.return_value = False
        gui.Dialog.return_value.select.return_value = 0
        picker.side_effect = lambda filtered, **kw: (
            filtered[gui.Dialog.return_value.select.return_value]
            if gui.Dialog.return_value.select.return_value >= 0
            else None
        )
        fetch.return_value = [
            streamnzb.StreamEntry(
                "Stream", "Returned description", URL, {"X-Test": "a+b &c/=d"}
            )
        ]
        yield {
            "kodi": kodi,
            "gui": gui,
            "plugin": plugin,
            "fetch": fetch,
            "opened": opened,
            "closed": closed,
            "notify": notify_mock,
            "clear": clear,
            "stored": stored,
            "choose": choose,
            "preserve": preserve,
            "consumed": consumed,
            "picker": picker,
        }


@pytest.mark.parametrize("handle", [None, 7])
def test_success_and_exact_url_headers(player_mocks, handle):
    mocks = player_mocks
    streamnzb_player.play_streamnzb(MOVIE, settings, handle)
    path = URL + "|X-Test=a%2Bb%20%26c%2F%3Dd"
    mocks["gui"].ListItem.assert_any_call(label="Stream", path=path)
    mocks["closed"].assert_called_once_with(mocks["opened"].return_value)
    mocks["clear"].assert_called_once()
    mocks["notify"].assert_not_called()
    if handle is None:
        mocks["kodi"].Player.return_value.play.assert_called_once_with(
            path, mocks["gui"].ListItem.return_value
        )
        mocks["plugin"].setResolvedUrl.assert_not_called()
    else:
        mocks["plugin"].setResolvedUrl.assert_called_once_with(
            7, True, mocks["gui"].ListItem.return_value
        )
        mocks["kodi"].Player.assert_not_called()


@pytest.mark.parametrize("handle", [None, 7])
@pytest.mark.parametrize("cancel", [False, True])
def test_captured_bookmark_resume_and_cancel(player_mocks, handle, cancel):
    mocks = player_mocks
    mocks["clear"].return_value = 300.0
    if cancel:
        mocks["choose"].side_effect = None
        mocks["choose"].return_value = None
    streamnzb_player.play_streamnzb(MOVIE, settings, handle)
    mocks["choose"].assert_called_once_with("streamnzb:movie:tt0133093", 300.0)
    if cancel:
        mocks["preserve"].assert_called_once_with("streamnzb:movie:tt0133093", 300.0)
        mocks["consumed"].assert_not_called()
        mocks["kodi"].Player.assert_not_called()
        if handle is not None:
            assert mocks["plugin"].setResolvedUrl.call_args.args[1] is False
    else:
        mocks["gui"].ListItem.return_value.setProperty.assert_any_call(
            "StartOffset", "300.0"
        )
        mocks["preserve"].assert_not_called()
        mocks["consumed"].assert_called_once_with("streamnzb:movie:tt0133093")


def test_cancelled_resume_is_read_and_consumed_on_next_handoff(player_mocks):
    mocks = player_mocks
    mocks["stored"].return_value = 420.0
    streamnzb_player.play_streamnzb(EPISODE, settings)
    mocks["choose"].assert_called_once_with("streamnzb:series:tmdb:1399:0:1", 420.0)
    mocks["gui"].ListItem.return_value.setProperty.assert_any_call(
        "StartOffset", "420.0"
    )
    mocks["consumed"].assert_called_once_with("streamnzb:series:tmdb:1399:0:1")


def test_failed_plugin_handoff_preserves_captured_resume(player_mocks):
    mocks = player_mocks
    mocks["clear"].return_value = 300.0
    mocks["plugin"].setResolvedUrl.side_effect = RuntimeError("Kodi shutting down")
    with pytest.raises(RuntimeError):
        streamnzb_player.play_streamnzb(MOVIE, settings, 7)
    mocks["preserve"].assert_called_once_with("streamnzb:movie:tt0133093", 300.0)
    mocks["consumed"].assert_not_called()


@pytest.mark.parametrize("handle", [None, 7])
def test_resume_dialog_failure_preserves_captured_bookmark(player_mocks, handle):
    mocks = player_mocks
    mocks["clear"].return_value = 300.0
    mocks["choose"].side_effect = RuntimeError("Kodi shutting down")
    streamnzb_player.play_streamnzb(MOVIE, settings, handle)
    mocks["preserve"].assert_called_once_with("streamnzb:movie:tt0133093", 300.0)
    mocks["consumed"].assert_not_called()
    mocks["notify"].assert_called_once()
    mocks["kodi"].Player.assert_not_called()
    if handle is not None:
        assert mocks["plugin"].setResolvedUrl.call_args.args[1] is False


def test_abort_after_bookmark_capture_preserves_resume(player_mocks):
    mocks = player_mocks
    mocks["clear"].return_value = 300.0
    mocks["kodi"].Monitor.return_value.abortRequested.side_effect = [False, False, True]
    streamnzb_player.play_streamnzb(MOVIE, settings, 7)
    mocks["preserve"].assert_called_once_with("streamnzb:movie:tt0133093", 300.0)
    assert mocks["plugin"].setResolvedUrl.call_args.args[1] is False
    mocks["consumed"].assert_not_called()


@pytest.mark.parametrize(
    "supplied,focused,expected",
    [
        ({"season": "1"}, ("Show", "2", "7"), ("1", "")),
        ({"episode": "3"}, ("Show", "2", "7"), ("", "3")),
        ({"season": "01"}, ("Show", "1", "7"), ("01", "7")),
        ({"episode": "7"}, ("Show", "2", "7"), ("2", "7")),
    ],
)
def test_partial_episode_recovery_requires_matching_coordinates(
    supplied, focused, expected
):
    with patch(
        "resources.lib.router_episodeinfo._episode_info_from_listitem",
        return_value=focused,
    ):
        recovered = streamnzb_player._recover_episode_numbers(
            dict(supplied, type="episode", title="Show", tmdb_id="123")
        )
    assert (recovered["season"], recovered["episode"]) == expected
    if not all(expected):
        with pytest.raises(streamnzb.StreamNZBError, match="season and episode"):
            streamnzb.content_identity(recovered)


@pytest.mark.parametrize("handle", [None, 7])
@pytest.mark.parametrize("failure", ["cancel", "empty", "auth", "unexpected", "abort"])
def test_failure_completion_and_cleanup(player_mocks, handle, failure):
    mocks = player_mocks
    if failure == "cancel":
        mocks["gui"].Dialog.return_value.select.return_value = -1
    elif failure == "empty":
        mocks["fetch"].return_value = []
    elif failure == "auth":
        mocks["fetch"].side_effect = streamnzb.StreamNZBError("Invalid token")
    elif failure == "unexpected":
        mocks["fetch"].side_effect = RuntimeError(TOKEN)
    else:
        mocks["kodi"].Monitor.return_value.abortRequested.return_value = True
    streamnzb_player.play_streamnzb(MOVIE, settings, handle)
    mocks["closed"].assert_called_once()
    mocks["kodi"].Player.assert_not_called()
    if handle is not None:
        mocks["plugin"].setResolvedUrl.assert_called_once_with(
            handle, False, mocks["gui"].ListItem.return_value
        )
    if handle is None or failure not in ("cancel", "abort"):
        mocks["notify"].assert_called_once()
    assert TOKEN not in str(mocks["notify"].call_args)
    assert TOKEN not in str(mocks["kodi"].log.call_args)


def test_picker_uses_shared_xml_dialog(player_mocks):
    mocks = player_mocks
    mocks["fetch"].return_value = [
        streamnzb.StreamEntry("B", "desc B", URL),
        streamnzb.StreamEntry("A", "desc A", URL + "&a=1"),
    ]
    mocks["gui"].Dialog.return_value.select.return_value = 1
    streamnzb_player.play_streamnzb(MOVIE, settings)
    mocks["gui"].Dialog.return_value.select.assert_not_called()
    rows = mocks["picker"].call_args.kwargs["all_results"]
    assert {row["title"] for row in rows} == {"A", "B"}
    assert all("_meta" in row and "_filter_reject" in row for row in rows)
    assert mocks["picker"].call_args.kwargs["total_count"] == 2
    assert mocks["kodi"].Player.return_value.play.call_args.args[0] == URL + "&a=1"


def test_episode_aliases_and_recovery():
    with patch(
        "resources.lib.router_episodeinfo._episode_info_from_listitem",
        return_value=("Show", "0", "2"),
    ):
        clean = streamnzb_player._recover_episode_numbers(
            {"type": "episode", "title": "Show", "season": "_"}
        )
        assert (clean["season"], clean["episode"]) == ("0", "2")
        explicit = streamnzb_player._recover_episode_numbers(
            dict(EPISODE, ep_season="3", ep_episode="20")
        )
        assert (explicit["season"], explicit["episode"]) == ("0", "1")
    with patch(
        "resources.lib.router_episodeinfo._episode_info_from_listitem",
        return_value=("Other Show", "1", "2"),
    ):
        assert (
            streamnzb_player._recover_episode_numbers(
                {"type": "episode", "title": "Show"}
            )["season"]
            == ""
        )


@pytest.mark.parametrize(
    "route_name", ["_handle_play", "_handle_search", "_handle_script_play"]
)
def test_router_bypasses_all_nzb_providers(route_name):
    with patch.object(playback_backend.xbmcaddon, "Addon") as addon, patch.object(
        router, "_get_script_setting", side_effect=settings
    ), patch.object(router, "play_streamnzb") as play, patch.object(
        router, "_search_with_cache"
    ) as search, patch.object(
        router, "_script_play_search_filter_tag"
    ) as script_search, patch.object(
        router, "xbmcplugin"
    ) as plugin:
        addon.return_value.getSetting.side_effect = settings
        args = (MOVIE,) if route_name == "_handle_script_play" else (7, MOVIE)
        getattr(router, route_name)(*args)
        play.assert_called_once()
        search.assert_not_called()
        script_search.assert_not_called()
        if route_name == "_handle_play":
            assert play.call_args.kwargs == {"handle": 7}
        elif route_name == "_handle_search":
            plugin.endOfDirectory.assert_called_once_with(7)


def test_resolver_entry_points_bypass_submission():
    from resources.lib import resolver, resolver_entry

    with patch.object(playback_backend.xbmcaddon, "Addon") as addon, patch.object(
        streamnzb_player, "play_streamnzb"
    ) as play, patch.object(resolver, "submit_nzb") as submit:
        addon.return_value.getSetting.side_effect = settings
        resolver_entry.resolve(7, MOVIE)
        resolver_entry.resolve_and_play(
            "unused", "The Matrix", dict(MOVIE, _settings_getter=settings)
        )
        resolver_entry.resolve_and_play("unused", "The Matrix", dict(MOVIE))
        assert play.call_count == 3
        submit.assert_not_called()


def test_manifest_route_rejects_without_fetching_nzb():
    from resources.lib import router_dispatch

    with patch.object(playback_backend.xbmcaddon, "Addon") as addon, patch.object(
        streamnzb_player, "play_streamnzb"
    ) as play, patch("resources.lib.source_manifest.load_source_manifest") as load:
        addon.return_value.getSetting.side_effect = settings
        router_dispatch._route_resolve_v2(
            {"manifest_url": "https://indexer.example/nzb"}
        )
        assert play.call_count == 1
        assert play.call_args.args[0] == {}
        load.assert_not_called()


def test_route_redacts_tokens():
    assert router._redact_route_params(
        {"streamnzb_token": TOKEN, "title": "Movie"}
    ) == {"streamnzb_token": "***", "title": "Movie"}


def test_diagnostic_slot_is_not_a_playable_release():
    entries = streamnzb.parse_streams(
        {
            "streams": [
                {
                    "url": URL + "&src=debug",
                    "behaviorHints": {"bingeGroup": "streamnzb-debug"},
                },
                {"url": URL, "name": "Actual release"},
            ]
        }
    )
    assert [entry.name for entry in entries] == ["Actual release"]


@pytest.mark.parametrize("backend,expected", [("0", False), ("1", True), ("2", False)])
def test_nzbget_delegation_respects_dropdown(backend, expected):
    from resources.lib.resolver_flow import _nzbget_enabled

    values = {"playback_backend": backend, "nzbget_enabled": "true"}
    assert _nzbget_enabled(lambda key, default="": values.get(key, default)) is expected


def test_episode_listitem_uses_canonical_numbers(player_mocks):
    streamnzb_player.play_streamnzb(EPISODE, settings, handle=7)
    info = player_mocks["gui"].ListItem.return_value.getVideoInfoTag.return_value
    info.setSeason.assert_called_once_with(0)
    info.setEpisode.assert_called_once_with(1)
    info.setMediaType.assert_called_once_with("episode")


def test_stream_label_prefers_release_filename():
    entries = streamnzb.parse_streams(
        {
            "streams": [
                {
                    "name": "StreamNZB",
                    "description": "Release details",
                    "url": URL,
                    "behaviorHints": {"filename": "Movie.2026.2160p.REMUX.mkv"},
                }
            ]
        }
    )
    assert entries[0].name == "Movie.2026.2160p.REMUX.mkv"
    assert entries[0].description == "Release details"


def test_stream_label_ignores_non_string_filename():
    entries = streamnzb.parse_streams(
        {
            "streams": [
                {
                    "name": "Fallback label",
                    "url": URL,
                    "behaviorHints": {"filename": {"unexpected": "object"}},
                }
            ]
        }
    )
    assert entries[0].name == "Fallback label"


def test_picker_applies_configured_filters_and_size_sort(player_mocks):
    mocks = player_mocks
    mocks["fetch"].return_value = [
        streamnzb.StreamEntry("Small.1080p.WEB-DL.x264-GRP", "", URL, size=100),
        streamnzb.StreamEntry("Excluded.2160p.REMUX.x265-GRP", "", URL, size=900),
        streamnzb.StreamEntry("Large.2160p.REMUX.x265-GRP", "", URL, size=500),
    ]

    def configured(key, default=""):
        return {"filter_exclude_keywords": "Excluded", "sort_order": "1"}.get(
            key, settings(key, default)
        )

    streamnzb_player.play_streamnzb(MOVIE, configured)
    filtered = mocks["picker"].call_args.args[0]
    assert [row["size"] for row in filtered] == [500, 100]
    assert filtered[0]["_meta"]["resolution"] == "2160p"
    assert filtered[0]["_meta"]["codec"]
    all_rows = mocks["picker"].call_args.kwargs["all_results"]
    assert len(all_rows) == 3
    assert all_rows[0]["_filter_reject"]
    assert mocks["gui"].ListItem.call_args.kwargs["label"] == (
        "Large.2160p.REMUX.x265-GRP"
    )
