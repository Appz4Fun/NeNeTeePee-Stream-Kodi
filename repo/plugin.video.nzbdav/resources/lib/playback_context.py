# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Canonical playback identity, independent of release names and stream URLs.

TMDb Helper's executebuiltin player bypasses update_playerstring(). Publish
its normal PlayerInfoString contract before Kodi's onAVStarted notification;
TMDb Helper remains the sole owner of Trakt progress and watched submissions.
"""

import json
import re
import uuid

import xbmc
import xbmcgui

from resources.lib.playback_handoff import (
    PENDING_PROPERTY,
    SESSION_PROPERTY,
    handoff,
)

__all__ = ["handoff", "metadata_from_params", "prepare_playback"]

_IMDB_ID = re.compile(r"^tt[0-9]+$")


def _text(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    value = str(value).strip()
    return "" if value == "_" or "{" in value or "}" in value else value


def _number(value, minimum=0):
    value = _text(value)
    if not value.isascii() or not value.isdigit():
        return None
    number = int(value)
    return number if number >= minimum else None


def _identifier(value):
    number = _number(value, minimum=1)
    return str(number) if number is not None else ""


def _normalize(metadata):
    """Whitelist and validate an IPC snapshot; never persist URLs or settings."""
    mediatype = _text(metadata.get("mediatype"))
    if mediatype == "series":
        mediatype = "episode"
    if mediatype not in ("movie", "episode"):
        return {}
    clean = {"mediatype": mediatype, "title": _text(metadata.get("title"))}
    year = _number(metadata.get("year"), minimum=1)
    if year is not None:
        clean["year"] = year
    for key in ("tmdb_id", "episode_tmdb_id", "tvdb"):
        value = _identifier(metadata.get(key))
        if value:
            clean[key] = value
    imdb = _text(metadata.get("imdb"))
    if _IMDB_ID.fullmatch(imdb):
        clean["imdb"] = imdb
    if mediatype == "episode":
        clean["tvshowtitle"] = _text(metadata.get("tvshowtitle"))
        for key, minimum in (("season", 0), ("episode", 1)):
            value = _number(metadata.get(key), minimum)
            if value is not None:
                clean[key] = value
    return clean


def metadata_from_params(params):
    """Capture movie/episode identity before the selected release replaces title.

    Episodes require an explicit series ID: schema 7's ``{tmdb_id}`` token
    actually expanded to an episode TVDB ID in TMDb Helper 6.17.1. Never infer
    a show from that ambiguous legacy field. Schema 8 supplies
    ``tvshow_tmdb_id={tmdb}`` and ``episode_tmdb_id={eptmdb}`` separately.
    """
    if not isinstance(params, dict):
        return {}
    snapshot = params.get("_playback_metadata")
    if isinstance(snapshot, dict):
        return _normalize(snapshot)
    mediatype = _text(params.get("type"))
    if mediatype == "series":
        mediatype = "episode"
    metadata = {
        "mediatype": mediatype,
        "title": params.get("title"),
        "year": params.get("year"),
        "imdb": params.get("imdb"),
        "tmdb_id": params.get("tmdb_id"),
    }
    if mediatype == "episode":
        season = params.get("season", "")
        episode = params.get("episode", "")
        if not _text(season):
            season = params.get("ep_season")
        if not _text(episode):
            episode = params.get("ep_episode")
        metadata.update(
            tvshowtitle=params.get("title"),
            title=params.get("episode_title"),
            season=season,
            episode=episode,
            tmdb_id=params.get("tvshow_tmdb_id"),
            episode_tmdb_id=params.get("episode_tmdb_id"),
            tvdb=params.get("tvdb"),
        )
    clean = _normalize(metadata)
    if mediatype == "episode" and not clean.get("title"):
        title = clean.get("tvshowtitle", "")
        if "season" in clean and "episode" in clean:
            title = "{} S{:02d}E{:02d}".format(
                title, clean["season"], clean["episode"]
            ).strip()
        clean["title"] = title
    return clean


def _apply_metadata(listitem, metadata):
    if not metadata:
        return
    tag = listitem.getVideoInfoTag()
    mediatype = metadata["mediatype"]
    tag.setMediaType(mediatype)
    if metadata.get("title"):
        tag.setTitle(metadata["title"])
        listitem.setLabel(metadata["title"])
    if metadata.get("year"):
        tag.setYear(metadata["year"])
    ids = {}
    if mediatype == "episode":
        tag.setTvShowTitle(metadata.get("tvshowtitle", ""))
        if "season" in metadata:
            tag.setSeason(metadata["season"])
        if "episode" in metadata:
            tag.setEpisode(metadata["episode"])
        for key, namespace in (
            ("tmdb_id", "tvshow.tmdb"),
            ("imdb", "tvshow.imdb"),
            ("tvdb", "tvshow.tvdb"),
            ("episode_tmdb_id", "tmdb"),
        ):
            if metadata.get(key):
                ids[namespace] = metadata[key]
    else:
        for key, namespace in (("tmdb_id", "tmdb"), ("imdb", "imdb")):
            if metadata.get(key):
                ids[namespace] = metadata[key]
    if ids:
        default = "tmdb" if "tmdb" in ids else "imdb" if "imdb" in ids else ""
        tag.setUniqueIDs(ids, default)


def _playerstring(metadata):
    if not metadata.get("tmdb_id"):
        return {}
    context = {"tmdb_type": metadata["mediatype"], "tmdb_id": metadata["tmdb_id"]}
    if metadata["mediatype"] == "episode":
        if not metadata.get("season") or not metadata.get("episode"):
            return {}
        context.update(season=metadata["season"], episode=metadata["episode"])
    for key in ("imdb", "tvdb"):
        if metadata.get(key):
            context[key + "_id"] = metadata[key]
    return context


def prepare_playback(listitem, metadata, home=None, session_token=None):
    """Apply identity and publish scrobbler context immediately before playback.

    Leave the property in place through asynchronous AV startup. Unknown
    identity clears stale context from earlier playback. The NZB-DAV snapshot
    lets the service restore exactly this identity on a stream reconnect.
    """
    required_session = session_token is not None
    session_write_failed = False
    metadata = _normalize(metadata) if isinstance(metadata, dict) else {}
    try:
        _apply_metadata(listitem, metadata)
    except (RuntimeError, TypeError, ValueError, AttributeError, OverflowError):
        metadata = {}
        xbmc.log("NZB-DAV: Kodi playback metadata unavailable", xbmc.LOGWARNING)
    try:
        home = home if home is not None else xbmcgui.Window(10000)
        if session_token is None:
            session_token = uuid.uuid4().hex
            home.setProperty(PENDING_PROPERTY, session_token)
        try:
            home.setProperty(SESSION_PROPERTY, session_token)
        except RuntimeError:
            session_write_failed = True
            raise
        home.setProperty("nzbdav.playback_metadata", json.dumps(metadata))
        context = _playerstring(metadata)
        if context:
            home.setProperty("TMDbHelper.PlayerInfoString", json.dumps(context))
            xbmc.log(
                "NZB-DAV: Playback context {}".format(json.dumps(context)), xbmc.LOGINFO
            )
        else:
            home.clearProperty("TMDbHelper.PlayerInfoString")
    except RuntimeError:
        xbmc.log(
            "NZB-DAV: Kodi playback context publication unavailable", xbmc.LOGWARNING
        )
        if home is not None:
            for key in ("TMDbHelper.PlayerInfoString", "nzbdav.playback_metadata"):
                try:
                    home.clearProperty(key)
                except RuntimeError:
                    # The GUI is unavailable; cleanup must not stop playback.
                    pass
        if required_session and session_write_failed:
            # Final handoffs require a committed owner before arming monitoring.
            raise
