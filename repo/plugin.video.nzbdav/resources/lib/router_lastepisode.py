# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Session-scoped "last picked episode" memory for the RunScript player.

TMDBHelper re-invokes the player for the "next" episode in two broken shapes,
and this module carries the state needed to correct both:

* Next-Up / widget plays arrive with blank season/episode (only the series
  ids), so an episode search broadens to the whole show. The pre-existing
  fallback recovers the numbers from whatever Kodi ListItem has focus, which
  goes stale once focus resets to the top of the season folder.
* After an episode finishes, a play can auto-fire for that season's
  *premiere* with season/episode explicitly populated (S09E01 landing right
  after S09E07 ended). The wrong episode is chosen upstream of this addon, so
  nothing about the incoming params looks blank or malformed -- only the fact
  that we just watched a later episode of the same season gives it away.

State lives in ``xbmcgui.Window(10000)`` properties, the same IPC pattern
already used by ``resolver_resume.py`` / ``stream_proxy_serviceprops.py`` for
cross-invocation state within a single Kodi session.
"""

from functools import wraps

import xbmc
import xbmcgui

_HOME_WINDOW_ID = 10000
# One "season:episode:started" value per show, so alternating between shows
# keeps each show's place. The four properties below track only the latest
# pick, for the "watched to the end" guard.
_PROP_SHOW_PICK = "nzbdav.last_ep_pick."
_PROP_SHOW_ID = "nzbdav.last_ep_show_id"
_PROP_SEASON = "nzbdav.last_ep_season"
_PROP_EPISODE = "nzbdav.last_ep_episode"
_PROP_COMPLETED = "nzbdav.last_ep_completed"

# Preference order for building a per-show identity key. Season/episode are
# deliberately excluded -- this identifies the *show*, not the episode.
#
# ``tvdb``/``imdb`` come first because TMDBHelper substitutes series-level ids
# for both, then the schema-8 ``tvshow_tmdb_id`` series id. ``tmdb_id`` is
# last because on an *episode* play it carries the EPISODE's TMDB id, not
# the show's (Rick and Morty S09E07 -> 11759797, S09E01 -> 11759791): keying
# on it first would file every episode of a show under a different identity
# and no recall could ever match.
_ID_FIELDS = ("tvdb", "imdb", "tvshow_tmdb_id", "tmdb_id")


def _fail_soft(default):
    """Keep this optional memory from ever turning into a playback failure."""

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # pylint: disable=broad-except
                xbmc.log(
                    "NeNeTeePee-Stream-Kodi: episode memory {} failed: {}".format(
                        func.__name__, type(exc).__name__
                    ),
                    xbmc.LOGWARNING,
                )
                return default

        return wrapper

    return decorate


def _show_identity_key(params):
    """Stable per-show key from ``params``, or "" if the show is unknown."""
    params = params if isinstance(params, dict) else {}
    for field in _ID_FIELDS:
        value = str(params.get(field) or "").strip()
        if value:
            return "{}:{}".format(field, value)
    title = str(params.get("title") or "").strip().casefold()
    if not title:
        return ""
    # Remakes and regional versions can share a title but not a year.
    year = str(params.get("year") or "").strip()
    return "title:{}:{}".format(title, year) if year else "title:{}".format(title)


def _home_window():
    return xbmcgui.Window(_HOME_WINDOW_ID)


def _remembered_pick(params):
    """Return ``(season, episode)`` ints for this show's stored pick, or None."""
    show_id = _show_identity_key(params)
    if not show_id:
        return None
    home = _home_window()
    if home.getProperty(_PROP_SHOW_ID) != show_id:
        return None
    season = home.getProperty(_PROP_SEASON) or ""
    episode = home.getProperty(_PROP_EPISODE) or ""
    if not (season.isdigit() and episode.isdigit()):
        return None
    return int(season), int(episode)


def _show_pick(show_id):
    """Return ``(season, episode, started)`` stored for this show, or None."""
    fields = (_home_window().getProperty(_PROP_SHOW_PICK + show_id) or "").split(":")
    if len(fields) != 3 or not (fields[0].isdigit() and fields[1].isdigit()):
        return None
    return int(fields[0]), int(fields[1]), fields[2] == "1"


def _store_show_pick(show_id, season, episode, started):
    _home_window().setProperty(
        _PROP_SHOW_PICK + show_id,
        "{}:{}:{}".format(season, episode, "1" if started else "0"),
    )


@_fail_soft(None)
def remember_last_episode(params, season, episode):
    """Record ``season``/``episode`` as the last pick for this show.

    Any new play supersedes the previous one, so the "watched to the end" flag
    is always cleared. A play without a show identity and numeric
    season/episode (a movie, say) also forgets the previous pick, so a movie
    that later ends can never mark an older episode as completed.
    """
    home = _home_window()
    home.clearProperty(_PROP_COMPLETED)
    show_id = _show_identity_key(params)
    season = str(season or "")
    episode = str(episode or "")
    if not (show_id and season.isdigit() and episode.isdigit()):
        for prop in (_PROP_SHOW_ID, _PROP_SEASON, _PROP_EPISODE):
            home.clearProperty(prop)
        return
    home.setProperty(_PROP_SHOW_ID, show_id)
    home.setProperty(_PROP_SEASON, season)
    home.setProperty(_PROP_EPISODE, episode)
    _store_show_pick(show_id, season, episode, started=False)


def _metadata_identity(metadata):
    """Return ``(show_id, season, episode)`` for an ended playback, or None.

    ``metadata`` is the service's ``playback_context`` snapshot of what was
    actually playing; its ``tmdb_id`` is the series id (``tvshow_tmdb_id``)
    and ``tvshowtitle`` the show title, so it maps onto the same identity key
    the pick was remembered under.
    """
    if not isinstance(metadata, dict) or metadata.get("mediatype") != "episode":
        return None
    show_id = _show_identity_key(
        {
            "tvdb": metadata.get("tvdb"),
            "imdb": metadata.get("imdb"),
            "tvshow_tmdb_id": metadata.get("tmdb_id"),
            "title": metadata.get("tvshowtitle"),
            "year": metadata.get("year"),
        }
    )
    season = str(metadata.get("season", ""))
    episode = str(metadata.get("episode", ""))
    if not (show_id and season.isdigit() and episode.isdigit()):
        return None
    return show_id, season, episode


@_fail_soft(None)
def mark_last_episode_started(metadata):
    """Note that the remembered pick really started playing.

    Called from the service's ``onAVStarted``. Until then a pick might still
    fail or be canceled, and the next Next-Up play has to retry it rather than
    skip past it.
    """
    identity = _metadata_identity(metadata)
    if identity is None:
        return
    show_id, season, episode = identity
    pick = _show_pick(show_id)
    if pick is not None and pick[:2] == (int(season), int(episode)):
        _store_show_pick(show_id, season, episode, started=True)


@_fail_soft(None)
def mark_last_episode_completed(metadata):
    """Flag the remembered pick as played to its natural end by ``metadata``.

    Called from the service's ``onPlayBackEnded`` only, never from
    ``onPlayBackStopped`` -- a user-initiated stop must leave this clear so
    that deliberately replaying the premiere after bailing out of a later
    episode is left alone by ``recall_stale_premiere``.

    ``metadata`` describes the playback that just ended. It only counts when
    it is the remembered pick itself (same show, season and episode): an
    older episode ending while a newer pick is still resolving, or a movie
    ending after an episode was stopped, flags nothing.
    """
    identity = _metadata_identity(metadata)
    if identity is None:
        return
    home = _home_window()
    remembered = (
        home.getProperty(_PROP_SHOW_ID) or "",
        home.getProperty(_PROP_SEASON) or "",
        home.getProperty(_PROP_EPISODE) or "",
    )
    if identity == remembered:
        home.setProperty(_PROP_COMPLETED, "1")


def _episode_after(params, season, episode):
    """Return ``(season, episode)`` strings for the episode after this one.

    Rolls over into the next season at a season finale (S02E10 -> S03E01)
    when ``episode_order`` can find the show's per-season episode counts, and
    falls back to ``episode + 1`` otherwise.
    """
    from resources.lib import episode_order

    try:
        counts = episode_order.season_episode_counts(params)
    except Exception:  # pylint: disable=broad-except
        counts = {}
    next_season, next_episode = episode_order.next_episode(season, episode, counts)
    return str(next_season), str(next_episode)


@_fail_soft(("", ""))
def recall_next_episode(params):
    """Return ``(season, episode)`` for the episode after this show's last pick.

    A pick that never started playing is returned as is, so a failed or
    canceled play is retried instead of skipped. Returns ``("", "")`` when
    nothing is remembered for this show.
    """
    show_id = _show_identity_key(params)
    pick = _show_pick(show_id) if show_id else None
    if pick is None:
        return "", ""
    season, episode, started = pick
    if not started:
        return str(season), str(episode)
    return _episode_after(params, season, episode)


@_fail_soft(("", ""))
def recall_stale_premiere(params, season, episode):
    """Return ``(season, episode)`` correcting a stale season-premiere play.

    A play auto-fired after an episode ends can point at episode 1 of the
    season that just finished, with the numbers explicitly populated -- the
    wrong episode is chosen upstream, so the params alone look legitimate.
    Treat it as that misfire only when every one of these holds:

    * the incoming play is episode 1,
    * of the same show and season as the pick we remember,
    * that remembered pick was episode 2 or later (so this is "premiere after
      a later episode", not a straight replay of episode 1), and
    * that episode was watched through to its natural end this session.

    The completed flag is consumed on a hit, making the guard one-shot: if the
    premiere really was what the user wanted, backing out and playing it again
    goes straight through untouched.
    """
    season = str(season or "")
    episode = str(episode or "")
    if not (season.isdigit() and episode.isdigit()) or int(episode) != 1:
        return "", ""
    home = _home_window()
    if (home.getProperty(_PROP_COMPLETED) or "") != "1":
        return "", ""
    pick = _remembered_pick(params)
    if pick is None:
        return "", ""
    last_season, last_episode = pick
    if last_season != int(season) or last_episode < 2:
        return "", ""
    home.clearProperty(_PROP_COMPLETED)
    return _episode_after(params, last_season, last_episode)
