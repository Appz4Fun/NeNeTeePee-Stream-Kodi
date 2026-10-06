# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Exercise the installed TMDb Helper 6.17.5 consumer with a fake Trakt sink.

The fixture contains verbatim upstream methods, not an independent recreation
of its gates. Kodi playback, clock, database, artwork and outbound API are stubbed;
these tests do not assert that a real Trakt account accepted a scrobble.
"""

import contextlib
import json
import sys
from functools import cached_property
from pathlib import Path
from types import ModuleType, SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest


class _InfoTag:
    def __init__(self):
        self.info = {}
        self.unique_ids = {}

    def __getattr__(self, name):
        key = name[3:].lower()
        if name.startswith("set"):
            return lambda value: self.info.__setitem__(key, value)
        if name.startswith("get"):
            return lambda: self.info.get(key, "")
        raise AttributeError(name)

    def setUniqueIDs(self, values, *args, **kwargs):
        self.unique_ids.update(values)

    def getUniqueID(self, key):
        return self.unique_ids.get(key, "")


class _ListItem:
    def __init__(self):
        self.tag = _InfoTag()
        self.properties = {}
        self.label = ""

    def getVideoInfoTag(self):
        return self.tag

    def setInfo(self, _kind, values):
        self.tag.info.update(values)

    def setUniqueIDs(self, values, *args, **kwargs):
        self.tag.setUniqueIDs(values)

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, "")

    def clearProperty(self, key):
        self.properties.pop(key, None)

    def setLabel(self, value):
        self.label = value


@pytest.fixture(name="tmdb_contract")
def _tmdb_contract_fixture(monkeypatch):
    """Load exact consumer methods with in-memory Kodi and API boundaries."""
    home = _ListItem()
    submitted = []
    invalidations = []
    actions = []
    home.setProperty("TMDbHelper.TraktIsAuth", "1")

    def get_property(key, **_kwargs):
        if key == "TraktIsAuth":
            return 1.0
        return home.getProperty("TMDbHelper." + key)

    def submit(path, postdata, **_kwargs):
        submitted.append((path.rsplit("/", 1)[-1], json.loads(json.dumps(postdata))))
        return {"action": "scrobble"}

    class Player:
        time = 0
        item = None

        def isPlayingVideo(self):
            return True

        def getPlayingFile(self):
            return "http://127.0.0.1:8765/stream/video.mkv"

        def getVideoInfoTag(self):
            return self.item.getVideoInfoTag()

        def getTime(self):
            return self.time

        def getTotalTime(self):
            return 1000

    class CommonMonitorFunctions:
        @staticmethod
        def get_identifier_details(_identifier):
            return None

        @staticmethod
        def set_identifier_details(_identifier, tmdb_id, tmdb_type):
            return SimpleNamespace(tmdb_id=tmdb_id, tmdb_type=tmdb_type)

        @staticmethod
        def get_tmdb_id_parent(**_kwargs):
            raise AssertionError("Episode identity must use explicit series TMDb ID")

        @staticmethod
        def get_tmdb_id(**_kwargs):
            raise AssertionError(
                "Known playback identity must not require title search"
            )

    class SyncInvalidator:  # pylint: disable=too-few-public-methods
        def __init__(self, kind):
            self.kind = kind

        def run(self, **kwargs):
            invalidations.append((self.kind, kwargs))

    namespace = {
        "cached_property": cached_property,
        "xbmcgui": SimpleNamespace(Window=lambda _id: home),
        "try_type": lambda value, kind: kind(value) if value else kind(),
        "executebuiltin": actions.append,
        "get_property": get_property,
        "get_setting": lambda key: key == "trakt_scrobbling",
        "set_timestamp": lambda offset: 10000 + offset,
        "kodi_log": lambda *_args: None,
        "Player": Player,
        "CommonMonitorFunctions": CommonMonitorFunctions,
    }
    fixture = Path(__file__).parent / "fixtures/tmdbhelper_6_17_5_contract.txt"
    # Execute only the reviewed repository fixture, never remote/user content.
    exec(  # pylint: disable=exec-used
        compile(fixture.read_text(), str(fixture), "exec"), namespace
    )

    get_property = namespace["get_property"]

    modules = {
        "jurialmunkey.window": {"get_property": get_property},
        "tmdbhelper.lib.player.action.playerstring": {
            "read_playerstring": namespace["read_playerstring"]
        },
        "tmdbhelper.lib.query.database.identifier": {
            "make_identifier_id": lambda **kwargs: tuple(sorted(kwargs.items()))
        },
        "tmdbhelper.lib.sync.invalidator": {"SyncInvalidator": SyncInvalidator},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)

    scrobbler_class = namespace["PlayerScrobbler"]
    scrobbler_class.is_mdblist_authorized = False
    for method in ("set_kodi_watched", "set_tmdb_ratings", "update_stats"):
        setattr(scrobbler_class, method, lambda self: None)
    namespace["PlayerItem"].get_ratings = lambda self: {}

    monitor_class = namespace["PlayerMonitor"]
    for method in (
        "clear_properties",
        "clear_artwork",
        "update_artwork",
        "refresh_mdblist_watched",
        "wait_for_seek",
        "set_ratings_properties",
        "set_properties",
    ):
        setattr(monitor_class, method, lambda self, *args: None)
    monitor_class.details = {}

    def monitor_for(item, time=0):
        monitor = monitor_class()
        monitor.reset_properties()
        monitor.item = item
        monitor.time = time
        monitor.trakt_api = SimpleNamespace(
            is_authorized=True, headers={}, get_api_request=submit
        )
        monitor.mdblist_api = None
        return monitor

    return SimpleNamespace(
        home=home,
        submitted=submitted,
        invalidations=invalidations,
        actions=actions,
        action_resolver=namespace["PlayerResolverBase"],
        monitor_for=monitor_for,
    )


def _episode_params():
    # The deliberately different episode ID proves namespace separation.
    return {
        "type": "episode",
        "title": "Shrinking",
        "episode_title": "Imposter Syndrome",
        "year": "2023",
        "season": "1",
        "episode": "6",
        "tmdb_id": "136311",
        "tvshow_tmdb_id": "136311",
        "episode_tmdb_id": "4000006",
        "imdb": "tt15677150",
        "tvdb": "411364",
    }


def _prepare(params, contract):
    from resources.lib.playback_context import metadata_from_params, prepare_playback

    item = _ListItem()
    prepare_playback(item, metadata_from_params(params), home=contract.home)
    return item


def test_original_bare_item_never_starts_scrobbler(tmdb_contract):
    monitor = tmdb_contract.monitor_for(_ListItem(), time=950)
    monitor.onAVStarted()
    monitor.on_fullscreen()
    assert monitor.dbtype == ""
    assert monitor.scrobbler.started is False
    assert tmdb_contract.submitted == []


def test_episode_metadata_without_playerstring_still_does_not_scrobble(tmdb_contract):
    item = _prepare(_episode_params(), tmdb_contract)
    tmdb_contract.home.clearProperty("TMDbHelper.PlayerInfoString")
    monitor = tmdb_contract.monitor_for(item, time=950)
    monitor.onAVStarted()
    monitor.on_fullscreen()
    assert monitor.dbtype == "episode"
    assert monitor.tmdb_id == "136311"
    assert monitor.scrobbler.started is False
    assert tmdb_contract.submitted == []


@pytest.mark.parametrize("resume_time", [0, 450, 850])
def test_episode_reaches_watched_once_through_real_monitor(tmdb_contract, resume_time):
    item = _prepare(_episode_params(), tmdb_contract)
    monitor = tmdb_contract.monitor_for(item, time=resume_time)
    monitor.onAVStarted()
    assert monitor.dbtype == "episode"
    assert monitor.query == "Shrinking"
    assert item.tag.getTitle() == "Imposter Syndrome"
    assert monitor.tmdb_id == "136311"
    assert item.tag.getUniqueID("tmdb") == "4000006"
    assert (monitor.season, monitor.episode) == (1, 6)
    assert monitor.scrobbler.content_id == "tv.136311.1.6"
    original_scrobbler = monitor.scrobbler
    monitor.onAVChange()
    assert monitor.scrobbler is original_scrobbler
    if resume_time < 800:
        monitor.time = 799
        monitor.on_fullscreen()
        assert [method for method, _ in tmdb_contract.submitted] == ["start"]
    monitor.time = 801
    monitor.on_fullscreen()
    monitor.on_fullscreen()
    monitor.onPlayBackPaused()
    monitor.onPlayBackResumed()
    monitor.onPlayBackSeek(0, 0)
    monitor.time = 990
    monitor.onPlayBackEnded()
    monitor.onPlayBackStopped()
    assert [method for method, _ in tmdb_contract.submitted] == ["start", "stop"]
    payload = tmdb_contract.submitted[-1][1]
    assert payload["show"] == {"ids": {"tmdb": "136311"}}
    assert payload["episode"] == {"season": 1, "number": 6}
    assert 80 <= payload["progress"] <= 100
    assert tmdb_contract.invalidations == [("watchedprogress", {"sync": True})]


def test_movie_reaches_watched_with_movie_identity(tmdb_contract):
    item = _prepare(
        {"type": "movie", "title": "Arrival", "year": "2016", "tmdb_id": "329865"},
        tmdb_contract,
    )
    monitor = tmdb_contract.monitor_for(item)
    monitor.onAVStarted()
    assert (monitor.dbtype, monitor.query, monitor.tmdb_id) == (
        "movie",
        "Arrival",
        "329865",
    )
    monitor.time = 850
    monitor.on_fullscreen()
    monitor.onPlayBackEnded()
    assert [method for method, _ in tmdb_contract.submitted] == ["start", "stop"]
    assert tmdb_contract.submitted[-1][1]["movie"] == {"ids": {"tmdb": "329865"}}


def test_reconnect_av_start_keeps_watched_guard_for_same_episode(tmdb_contract):
    monitor = tmdb_contract.monitor_for(_prepare(_episode_params(), tmdb_contract))
    monitor.onAVStarted()
    monitor.time = 850
    monitor.on_fullscreen()
    original_scrobbler = monitor.scrobbler
    monitor.item = _prepare(_episode_params(), tmdb_contract)
    monitor.onAVStarted()
    assert monitor.scrobbler is original_scrobbler
    monitor.on_fullscreen()
    monitor.onPlayBackEnded()
    assert [method for method, _ in tmdb_contract.submitted] == ["start", "stop"]


def test_stopped_then_resumed_before_threshold_has_one_watched_submission(
    tmdb_contract,
):
    monitor = tmdb_contract.monitor_for(_prepare(_episode_params(), tmdb_contract))
    monitor.onAVStarted()
    monitor.time = 500
    monitor.onPlayBackStopped()
    monitor.item = _prepare(_episode_params(), tmdb_contract)
    monitor.onAVStarted()
    monitor.time = 850
    monitor.on_fullscreen()
    monitor.onPlayBackEnded()
    stops = [payload for method, payload in tmdb_contract.submitted if method == "stop"]
    assert len(stops) == 2
    assert len([payload for payload in stops if payload["progress"] >= 80]) == 1


def test_installed_dictionary_expands_profile_ids_in_correct_namespaces():
    from resources.lib.player_installer import PLAYER_JSON

    namespace = {
        "cached_property": cached_property,
        "contextlib": contextlib,
        "dumps": json.dumps,
        "quote": quote,
        "quote_plus": quote_plus,
        "try_int": int,
    }
    fixture = Path(__file__).parent / "fixtures/tmdbhelper_6_17_5_dictionary.txt"
    # Execute only the reviewed repository fixture, never remote/user content.
    exec(  # pylint: disable=exec-used
        compile(fixture.read_text(), str(fixture), "exec"), namespace
    )
    details = SimpleNamespace(
        infolabels={
            "title": "Imposter Syndrome",
            "tvshowtitle": "Shrinking",
            "year": 2023,
        },
        infoproperties={"tvshow.year": 2023},
        unique_ids={
            "tmdb": 4000006,
            "tvdb": 9579997,
            "tvshow.tmdb": 136311,
            "tvshow.tvdb": 411364,
            "tvshow.imdb": "tt15677150",
        },
        art={},
    )
    dictionary = namespace["PlayerDictionaryDictEpisode"](
        136311, details, season=1, episode=6
    )
    # This reproduces the installed schema-7 failure without relying on docs:
    # language-prefix fallback makes tmdb_id resolve the episode's TVDB id.
    assert dictionary.string_format_map("{tmdb_id}") == "9579997"
    assert dictionary.string_format_map("{ep_showseason},{ep_showepisode}") == "_,_"
    rendered = dictionary.string_format_map(PLAYER_JSON["play_episode"])
    assert rendered.startswith("executebuiltin://RunScript(")
    assert ",tmdb_id=136311," in rendered
    assert ",tvshow_tmdb_id=136311," in rendered
    assert ",episode_tmdb_id=4000006" in rendered
    assert "episode_title=Imposter%20Syndrome" in rendered
    assert "season=1,episode=6" in rendered
    movie = namespace["PlayerDictionaryDictMovie"](329865, details)
    assert "tmdb_id=329865" in movie.string_format_map(PLAYER_JSON["play_movie"])


def test_predestination_native_tags_and_exact_playerstring_drive_real_scrobbler(
    tmdb_contract,
):
    item = _prepare(
        {
            "type": "movie",
            "title": "Predestination",
            "year": "2014",
            "tmdb_id": "206487",
            "imdb": "tt2397535",
        },
        tmdb_contract,
    )
    assert item.tag.getMediaType() == "movie"
    assert item.tag.getTitle() == "Predestination"
    assert item.tag.getYear() == 2014
    assert item.tag.getUniqueID("tmdb") == "206487"
    assert item.tag.getUniqueID("imdb") == "tt2397535"
    assert json.loads(
        tmdb_contract.home.getProperty("TMDbHelper.PlayerInfoString")
    ) == {"tmdb_type": "movie", "tmdb_id": "206487", "imdb_id": "tt2397535"}
    monitor = tmdb_contract.monitor_for(item, time=0)
    monitor.onAVStarted()
    monitor.time = 850
    monitor.on_fullscreen()
    monitor.onPlayBackEnded()
    assert [method for method, _data in tmdb_contract.submitted] == ["start", "stop"]
    assert all(
        data["movie"]["ids"]["tmdb"] == "206487" for _, data in tmdb_contract.submitted
    )


def test_runscript_action_contract_does_not_publish_playerstring(tmdb_contract):
    updates = []
    action = "RunScript(addon.py,tmdb_play,type=movie)"
    resolver = SimpleNamespace(
        action=action, update_playerstring=lambda: updates.append(True)
    )
    tmdb_contract.action_resolver.execute_action(resolver)
    assert tmdb_contract.actions == [action]
    assert not updates
    assert tmdb_contract.home.getProperty("TMDbHelper.PlayerInfoString") == ""


@pytest.mark.parametrize("authorization", ["property", "api"])
def test_repaired_identity_respects_real_scrobbler_auth_gate(
    tmdb_contract, authorization
):
    item = _prepare(
        {"type": "movie", "title": "Predestination", "tmdb_id": "206487"}, tmdb_contract
    )
    monitor = tmdb_contract.monitor_for(item)
    if authorization == "property":
        tmdb_contract.home.clearProperty("TMDbHelper.TraktIsAuth")
    else:
        monitor.trakt_api.is_authorized = False
    monitor.onAVStarted()
    assert tmdb_contract.submitted == []
