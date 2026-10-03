# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Kodi release selection and direct handoff for StreamNZB.

Uses the shared release parser, filters, sorting and XML results picker.
No proxy, NZB submission, probes or fallback workers are used.
The non-modal progress dialog follows the existing CoreELEC-safe pattern.
"""

from urllib.parse import quote

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib.filter import filter_results
from resources.lib.http_util import notify
from resources.lib.i18n import string
from resources.lib.results_dialog import show_results_dialog
from resources.lib.router_settings import _close_loading_dialog, _open_loading_dialog
from resources.lib.streamnzb import (
    StreamNZBCancelled,
    StreamNZBError,
    content_identity,
    fetch_streams,
    safe_display,
)


def _matching_number(supplied, focused):
    """Missing coordinates may be filled; supplied coordinates must agree."""
    if not supplied:
        return True
    supplied, focused = str(supplied), str(focused)
    return supplied.isdigit() and focused.isdigit() and int(supplied) == int(focused)


def _focused_episode_matches(title, season, episode):
    from resources.lib.router_episodeinfo import _episode_info_from_listitem

    show, recovered_season, recovered_episode = _episode_info_from_listitem(title)
    matches = all(
        (
            show.strip().casefold() == title.strip().casefold(),
            _matching_number(season, recovered_season),
            _matching_number(episode, recovered_episode),
        )
    )
    if matches:
        return season or recovered_season, episode or recovered_episode
    return season, episode


def _needs_episode_recovery(params, title, season, episode):
    return (
        params.get("type") in ("episode", "series")
        and bool(title)
        and not (season and episode)
    )


def _recover_episode_numbers(params):
    """Use the existing same-show focused-item recovery for missing numbers.

    Never substitute an unrelated focused show when only an id was supplied.
    ep_* are fallback aliases, as on the existing NZB-DAV paths.
    """
    clean = {key: ("" if value == "_" else value) for key, value in params.items()}
    season = clean.get("season") or clean.get("ep_season", "")
    episode = clean.get("episode") or clean.get("ep_episode", "")
    title = clean.get("title", "")
    if _needs_episode_recovery(clean, title, season, episode):
        season, episode = _focused_episode_matches(title, season, episode)
    clean["season"], clean["episode"] = season, episode
    return clean


def _playback_listitem(entry, params):
    """Preserve the URL verbatim; encode Kodi pipe-header values losslessly."""
    path = entry.url
    if entry.headers:
        path += "|" + "&".join(
            "{}={}".format(quote(key, safe=""), quote(value, safe=""))
            for key, value in entry.headers.items()
        )
    item = xbmcgui.ListItem(label=entry.name, path=path)
    item.setProperty("IsPlayable", "true")
    # Do not force a guessed MIME type for extension-less StreamNZB slots.
    item.setContentLookup(False)
    info = item.getVideoInfoTag()
    title = params.get("title", "")
    info.setTitle(title or entry.name)
    info.setPlot(entry.description)
    content_type, _identity = content_identity(params)
    info.setMediaType("movie" if content_type == "movie" else "episode")
    if content_type == "series":
        info.setTvShowTitle(title)
        info.setSeason(int(params.get("season") or params.get("ep_season")))
        info.setEpisode(int(params.get("episode") or params.get("ep_episode")))
    return item, path


def _select_stream(params, settings_getter, monitor):
    """Fetch releases and show the shared picker after closing progress."""
    token = settings_getter("streamnzb_token", "")
    title = safe_display(params.get("title", ""), token)
    loading = _open_loading_dialog(title)
    try:
        entries = fetch_streams(
            settings_getter("streamnzb_url", ""),
            token,
            params,
            cancelled=monitor.abortRequested,
        )
    finally:
        _close_loading_dialog(loading)
    if not entries:
        raise StreamNZBError(string(30608))
    if monitor.abortRequested():
        raise StreamNZBCancelled()
    rows = [
        {
            "title": entry.name,
            "size": entry.size,
            "indexer": "StreamNZB",
            "_stream_entry": entry,
        }
        for entry in entries
    ]
    filtered, all_rows = filter_results(rows, settings_getter=settings_getter)
    selected = show_results_dialog(
        filtered,
        title=title,
        year=params.get("year", ""),
        total_count=len(rows),
        all_results=all_rows,
    )
    if selected is None or monitor.abortRequested():
        raise StreamNZBCancelled()
    return selected["_stream_entry"]


def _finish_resume_state(key, captured, succeeded):
    """Keep a consumed bookmark on cancellation, without a playback retry worker.

    Cancellation records use a token-free content key and are consumed on the
    next successful handoff, so they cannot resurrect an old finished position.
    Normal playback remains under Kodi's own bookmark management.
    """
    if not key:
        return
    from resources.lib import resolver, resume_store

    try:
        if succeeded:
            resume_store.clear_resume(key)
        else:
            resolver._preserve_resume_on_cancel(key, captured)
    except Exception:  # pylint: disable=broad-except
        xbmc.log("NZB-DAV: StreamNZB resume cleanup failed", xbmc.LOGWARNING)


def _capture_resume(params):
    """Reuse native resume choice, without arming NZB-DAV's retry monitor."""
    from resources.lib import resolver

    kind, identity = content_identity(params)
    key = "streamnzb:{}:{}".format(kind, identity)
    captured = resolver._coerce_resume_seconds(
        resolver._clear_kodi_playback_state(params)
    )
    return key, captured


def _choose_resume(item, key, captured):
    from resources.lib import resolver, resume_choice

    offset = max(captured, resolver._read_stored_resume(key))
    chosen = resume_choice.choose_resume_seconds(key, offset)
    if chosen is not None:
        resolver._apply_resume_start_offset(item, chosen)
    return chosen


def _complete_playback(handle, succeeded, item, resume_key, captured):
    handed_off = succeeded and handle is None
    try:
        if handle is not None:
            xbmcplugin.setResolvedUrl(
                handle, succeeded, item if succeeded else xbmcgui.ListItem()
            )
            handed_off = succeeded
    finally:
        _finish_resume_state(resume_key, captured, handed_off)


def _ensure_resume_not_cancelled(chosen, monitor):
    if chosen is None or monitor.abortRequested():
        raise StreamNZBCancelled()


def play_streamnzb(params, settings_getter, handle=None):
    """Complete every plugin handle; handle-less errors/cancellation notify.

    The synchronous request is bounded and no background work is started, so
    failure cleanup consists of closing the owned progress dialog. StreamNZB
    owns its playback slots and any server-side retry/failover decisions.
    """
    succeeded = False
    item = None
    resume_key = ""
    captured = 0.0
    try:
        params = _recover_episode_numbers(params)
        monitor = xbmc.Monitor()
        entry = _select_stream(params, settings_getter, monitor)
        item, path = _playback_listitem(entry, params)
        resume_key, captured = _capture_resume(params)
        chosen = _choose_resume(item, resume_key, captured)
        _ensure_resume_not_cancelled(chosen, monitor)
        if handle is None:
            xbmc.Player().play(path, item)
        succeeded = True
    except StreamNZBCancelled:
        if handle is None:
            notify("StreamNZB", string(30609))
    except StreamNZBError as error:
        notify("StreamNZB", str(error))
    except Exception:  # pylint: disable=broad-except
        # Kodi/urllib errors may embed a token-bearing URL: never echo them.
        notify("StreamNZB", string(30610))
        xbmc.log("NZB-DAV: StreamNZB playback failed (details redacted)", xbmc.LOGERROR)
    finally:
        _complete_playback(handle, succeeded, item, resume_key, captured)
