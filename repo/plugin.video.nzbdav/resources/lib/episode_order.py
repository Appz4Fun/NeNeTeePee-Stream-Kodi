# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Season-aware "next episode" for the RunScript player's episode memory.

``router_lastepisode`` remembers the last episode picked for a show and, when
TMDBHelper fires a broken "next" play, retargets it one past that pick. A
plain ``episode + 1`` is wrong at a season boundary: after the S02E10 finale
the next episode is S03E01, not S02E11. This module looks up how many
episodes each season has so the step can roll over into the next season.

Sources, in order:

* TMDB ``/tv/{id}`` season list, when the user configured a TMDB API key
  (the numbering TMDBHelper itself uses);
* TVmaze, which needs no key, looked up by the series TVDB or IMDb id.

Everything is *fail-soft*: no ids, no network, or an odd payload yields an
empty map and the caller falls back to ``episode + 1``. Successful lookups are
cached in a Home-window property for the rest of the Kodi session, the same
cross-invocation pattern ``router_lastepisode`` uses for its own state.
"""

import json

import xbmc
import xbmcgui

_HOME_WINDOW_ID = 10000
_PROP_PREFIX = "nzbdav.season_counts."
_TMDB_BASE = "https://api.themoviedb.org/3"
_TVMAZE_BASE = "https://api.tvmaze.com"
_TIMEOUT = 8
_MAX_BYTES = 4 * 1024 * 1024


def _get_json(http_get, url):
    return json.loads(http_get(url, timeout=_TIMEOUT, max_bytes=_MAX_BYTES))


def _series_ids(params):
    """Return ``(tvdb, imdb)`` series-level ids from player params."""
    params = params if isinstance(params, dict) else {}
    tvdb = str(params.get("tvdb") or "").strip()
    imdb = str(params.get("imdb") or "").strip()
    return (tvdb if tvdb.isdigit() else ""), (imdb if imdb.startswith("tt") else "")


def _tmdb_season_counts(http_get, key, tvdb, imdb):
    """Episode count per season from TMDB, or ``{}``.

    The ``{tmdb_id}`` player token is the EPISODE's id on an episode play, so
    the series is found through ``/find`` with the series TVDB/IMDb id instead.
    """
    series_id = None
    for value, source in ((tvdb, "tvdb_id"), (imdb, "imdb_id")):
        if not value:
            continue
        found = _get_json(
            http_get,
            "{}/find/{}?api_key={}&external_source={}".format(
                _TMDB_BASE, value, key, source
            ),
        )
        results = found.get("tv_results") or []
        if results and results[0].get("id"):
            series_id = results[0]["id"]
            break
    if series_id is None:
        return {}
    show = _get_json(http_get, "{}/tv/{}?api_key={}".format(_TMDB_BASE, series_id, key))
    counts = {}
    for season in show.get("seasons") or []:
        number = season.get("season_number")
        count = season.get("episode_count")
        if isinstance(number, int) and isinstance(count, int) and number > 0:
            counts[number] = count
    return counts


def _tvmaze_season_counts(http_get, tvdb, imdb):
    """Highest regular episode number per season from TVmaze, or ``{}``."""
    show = None
    for value, field in ((tvdb, "thetvdb"), (imdb, "imdb")):
        if not value:
            continue
        try:
            show = _get_json(
                http_get, "{}/lookup/shows?{}={}".format(_TVMAZE_BASE, field, value)
            )
        except Exception:  # pylint: disable=broad-except
            # TVmaze answers an unknown id with a 404; try the other id.
            continue
        if show and show.get("id"):
            break
        show = None
    if show is None:
        return {}
    episodes = _get_json(
        http_get, "{}/shows/{}/episodes".format(_TVMAZE_BASE, show["id"])
    )
    counts = {}
    for item in episodes if isinstance(episodes, list) else []:
        number = item.get("season")
        episode = item.get("number")
        # Specials carry a null episode number; season 0 is never "next".
        if isinstance(number, int) and isinstance(episode, int) and number > 0:
            counts[number] = max(counts.get(number, 0), episode)
    return counts


def _cache_key(tvdb, imdb):
    return _PROP_PREFIX + (tvdb or imdb)


def _cached_counts(home, cache_key):
    try:
        raw = json.loads(home.getProperty(cache_key) or "{}")
        return {int(season): int(count) for season, count in raw.items()}
    except (TypeError, ValueError, AttributeError):
        return {}


def _default_http_get():
    from resources.lib.http_util import http_get

    return http_get


def _default_settings_getter():
    # RunScript path: read settings off disk, never through xbmcaddon.Addon
    # (see router._get_script_setting and tvdb_resolver._get_tmdb_api_key).
    import resources.lib.router as _router

    return _router._get_script_setting


def season_episode_counts(params, settings_getter=None, http_get=None):
    """Return ``{season_number: episode_count}`` for the show in ``params``.

    ``settings_getter`` and ``http_get`` are injectable for tests. Returns
    ``{}`` when the show can't be identified or every source fails.
    """
    tvdb, imdb = _series_ids(params)
    if not (tvdb or imdb):
        return {}
    home = xbmcgui.Window(_HOME_WINDOW_ID)
    cache_key = _cache_key(tvdb, imdb)
    counts = _cached_counts(home, cache_key)
    if counts:
        return counts
    if http_get is None:
        http_get = _default_http_get()
    try:
        getter = settings_getter or _default_settings_getter()
        key = (getter("tmdb_api_key", "") or "").strip()
    except Exception:  # pylint: disable=broad-except
        key = ""
    sources = []
    if key:
        sources.append(lambda: _tmdb_season_counts(http_get, key, tvdb, imdb))
    sources.append(lambda: _tvmaze_season_counts(http_get, tvdb, imdb))
    for source in sources:
        try:
            counts = source()
        except Exception as exc:  # pylint: disable=broad-except
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Season episode-count lookup failed: "
                "{}".format(type(exc).__name__),
                xbmc.LOGDEBUG,
            )
            counts = {}
        if counts:
            home.setProperty(cache_key, json.dumps(counts))
            return counts
    return {}


def next_episode(season, episode, counts):
    """Return ``(season, episode)`` ints for the episode after ``season``/``episode``.

    Rolls over to episode 1 of the next season that has episodes once
    ``episode`` reaches that season's count. When ``counts`` doesn't know the
    season, or no later season is listed yet, it falls back to
    ``episode + 1`` in the same season.
    """
    season, episode = int(season), int(episode)
    count = counts.get(season) if counts else None
    if not count or episode < count:
        return season, episode + 1
    later = sorted(n for n, c in counts.items() if n > season and c > 0)
    if later:
        return later[0], 1
    return season, episode + 1
