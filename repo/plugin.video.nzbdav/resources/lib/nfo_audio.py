# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The audio a release's NFO names, read for the top releases of a search.

A release name can leave its audio out ("Movie.2004.MULTi.1080p.BluRay.
x264-GRP"), and then the release ranks as "audio unknown". Its NFO usually
says: ``Audio.........: English DTS-HD MA 5.1 @ 3739 kb/s``.

Some indexers send, with a search result, a flag that the release has an NFO
and a link to it (``newznab_attrs.nfo_link``); NZBHydra2 passes both through.
For the first few releases of a search that offer one, the add-on fetches the
NFO, reads the audio formats out of it and remembers them:

* only the top releases (``nfo_audio_releases``, 3 by default, 0 for off):
  each fetch is an API call on the user's account with that indexer;
* an indexer that refuses a read (a client it blocks, a limit reached) is
  left alone for a day, not asked again for every new release;
* each release is asked for once. The answer, or that there was none, is
  kept in ``nfo_audio.json`` in the add-on's data folder and applies to every
  listing of that release name from then on, whichever indexer lists it;
* the picker waits a short while for the answers (``_WAIT_SECONDS``) and
  opens without them when an indexer is slow. They are stored when they
  arrive and used the next time.

What the NFO names is added to what the name says, never taken away: a name
that tags ``TrueHD.Atmos`` keeps it when the NFO only writes ``TrueHD``.

The link carries the user's API key for that indexer, so it is never logged.
"""

import json
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

import xbmc
import xbmcvfs

from resources.lib import mediainfo_audio
from resources.lib.filter_audio import main_audio
from resources.lib.filter_tags import detect_audio, detect_channels
from resources.lib.http_util import http_get, redact_text
from resources.lib.mediainfo_audio import (
    CHANNEL_COUNT,
    merge_tracks,
    readable_formats,
)

SETTING = "nfo_audio_releases"
DEFAULT_RELEASES = 3
MAX_RELEASES = 5
# On a row's metadata: the audio formats its NFO added to the name's (an
# empty list when the NFO only supplied the channel count).
FROM_NFO_KEY = "audio_from_nfo"

_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"
_STORE_NAME = "nfo_audio.json"
_STORE_VERSION = 1
_MAX_STORED = 3000
# A fetch that failed is not repeated for this long.
_RETRY_AFTER_SECONDS = 24 * 3600
_FETCH_TIMEOUT = 6
_MAX_NFO_BYTES = 262144
_MAX_MEDIAINFO_BYTES = 2097152
# An indexer found to have no MediaInfo call is not asked again for this long.
_NO_API_SECONDS = 30 * 24 * 3600
# How long the picker waits for the answers before opening without them.
_WAIT_SECONDS = 2.0

# A line labelled as the audio ("Audio.........:", "AUDiO #1 :", "Ton:"), in
# the languages NFOs are written in. The word must be the label, ended by a
# colon, an equals sign or a row of dots ("AUDiO 1........ German EAC3 5.1"):
# a sentence that merely mentions audio is not read. Box-drawing art before
# the label is skipped.
_AUDIO_LABEL = re.compile(
    r"^\W*(?:audio|sound|tonspur|ton|son|lyd|ljud|geluid)\b[\w #/-]{0,20}?"
    r"\s*(?:[:=]|\.{3,})",
    re.I,
)
# MediaInfo's layout: a line that is just "Audio" or "Audio #2" heads a block
# of labelled lines that ends at the next blank line.
_AUDIO_SECTION = re.compile(r"^\W*audio(?:\s*#?\s*\d+)?\W*$", re.I)
_SECTION_FIELD = re.compile(
    r"^\W*(?:format|commercial name|codec(?: id)?|channel)", re.I
)
# A line with a label of its own: an audio line's continuation ends here.
_LABELLED = re.compile(r"^\W*[A-Za-z][\w #/()-]{0,24}?[ .]*[:=]")
_CONTINUATION_LINES = 3
_PADDING = re.compile(r"[._]{2,}")
# A channel count that stands by itself on an audio line ("AUDiO CHANNEL: 2.0").
_BARE_COUNT = re.compile(r"(?<![\d.])([1-9]\.[01])(?![\d.])")
_CHANNEL_WORDS = re.compile(r"\b([1-9])\s*(?:ch\b|channels?\b)", re.I)

_LOCK = threading.Lock()
# Held for a whole save, so saves land in the order they were taken.
_SAVE_LOCK = threading.Lock()
_STORE = {}
# host -> when it last refused a read: it is left alone for a while.
_PAUSED_HOSTS = {}
# host -> whether it answers the MediaInfo call, and when that was learned.
_REST_HOSTS = {}
_LOADED = []


def _audio_tracks(text):
    """``(track, line)`` for the lines of an NFO that describe its audio.

    A line labelled as the audio is a track, and so is each unlabelled line
    right under it (a second track). The format lines of a MediaInfo "Audio"
    block are one track together. ``track`` numbers them in order.
    """
    continuation = 0
    in_section = False
    track = 0
    # splitlines: a CRLF is one line ending, not a line and a blank line.
    for line in str(text or "").splitlines():
        if not line.strip():
            continuation = 0
            in_section = False
        elif _AUDIO_SECTION.match(line):
            continuation = 0
            in_section = True
            track += 1
        elif _AUDIO_LABEL.match(line):
            continuation = _CONTINUATION_LINES
            in_section = False
            track += 1
            yield track, line
        elif in_section:
            if _SECTION_FIELD.match(line):
                yield track, line
        elif continuation and not _LABELLED.match(line):
            continuation -= 1
            track += 1
            yield track, line
        else:
            continuation = 0


def _line_channels(readable):
    """The channel count on an audio line: by a format, in words, or bare."""
    counts = detect_channels(readable)
    if counts:
        return counts[0]
    words = _CHANNEL_WORDS.search(readable)
    if words:
        return CHANNEL_COUNT.get(words.group(1), "")
    bare = _BARE_COUNT.search(readable)
    return bare.group(1) if bare else ""


def _join_bare_counts(entries):
    """Give a count that stands alone to the track named before it.

    "AUDiO CHANNEL....: 2.0" on its own line belongs to the track on the
    line above, when that one named a format and no count.
    """
    named = [entry for entry in entries if entry["audio"]]
    for index, entry in enumerate(entries):
        if entry["audio"] or not entry["channels"]:
            continue
        before = [e for e in entries[:index] if e["audio"]]
        if before and before[-1] in named and not before[-1]["channels"]:
            before[-1]["channels"] = entry["channels"]


def audio_from_nfo(text):
    """``{"audio": [...], "channels": "5.1"}`` read from an NFO's text.

    Only the lines that describe the audio tracks are read
    (``_audio_tracks``); the tracks are put together by
    ``mediainfo_audio.merge_tracks``. The audio list is empty when the NFO
    names no format the add-on knows.
    """
    tracks = {}
    for track, line in _audio_tracks(text):
        # A label's row of dots is padding. Left in, the tag reader takes the
        # dot for the name's word separator and reads "DD 5.1" as a "DD+5.1"
        # whose plus was lost, which only happens in release names.
        readable = readable_formats(_PADDING.sub(" ", line))
        entry = tracks.setdefault(track, {"audio": [], "channels": ""})
        for name in detect_audio(readable):
            if name not in entry["audio"]:
                entry["audio"].append(name)
        if not entry["channels"]:
            entry["channels"] = _line_channels(readable)
    entries = list(tracks.values())
    _join_bare_counts(entries)
    return merge_tracks(entries)


# --- what is known, on disk -------------------------------------------------


def _store_path():
    return os.path.join(xbmcvfs.translatePath(_PROFILE_PATH), _STORE_NAME)


def _load():
    """Read the store once per run; an unreadable file is an empty store."""
    with _LOCK:
        if _LOADED:
            return
        _LOADED.append(True)
        try:
            with open(_store_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            releases = data.get("releases") if isinstance(data, dict) else None
            if isinstance(releases, dict) and data.get("v") == _STORE_VERSION:
                for key, entry in releases.items():
                    if isinstance(key, str) and _valid_entry(entry):
                        _STORE[key] = entry
                hosts = data.get("hosts")
                for host, when in (hosts if isinstance(hosts, dict) else {}).items():
                    if isinstance(host, str) and isinstance(when, (int, float)):
                        _PAUSED_HOSTS[host] = when
                rest = data.get("rest")
                for host, state in (rest if isinstance(rest, dict) else {}).items():
                    if (
                        isinstance(host, str)
                        and isinstance(state, dict)
                        and isinstance(state.get("ok"), bool)
                        and isinstance(state.get("at"), (int, float))
                    ):
                        _REST_HOSTS[host] = {"ok": state["ok"], "at": state["at"]}
        except (OSError, TypeError, ValueError):
            return


def _valid_entry(entry):
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("audio"), list)
        and all(isinstance(name, str) for name in entry["audio"])
        and isinstance(entry.get("channels", ""), str)
        and isinstance(entry.get("at", 0), (int, float))
    )


def _save():
    """Write the store; a failed write only costs the next run a fetch.

    One writer at a time, from the snapshot to the replace: two fetches that
    finish together must not let the older snapshot land on the newer one.
    """
    with _SAVE_LOCK:
        with _LOCK:
            entries = sorted(_STORE.items(), key=lambda item: item[1].get("at", 0))
            for key, _entry in entries[: max(0, len(entries) - _MAX_STORED)]:
                del _STORE[key]
            data = {
                "v": _STORE_VERSION,
                "releases": dict(_STORE),
                "hosts": dict(_PAUSED_HOSTS),
                "rest": dict(_REST_HOSTS),
            }
        tmp_path = ""
        try:
            path = _store_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = "{}.{}.tmp".format(path, threading.get_ident())
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp_path, path)
        except (OSError, TypeError, ValueError, RuntimeError):
            try:
                if tmp_path:
                    os.remove(tmp_path)
            except OSError:
                # Nothing was left behind, or it cannot be removed.
                pass


def _known(key):
    _load()
    with _LOCK:
        return _STORE.get(key)


def _remember(key, audio, channels, failed=False):
    _load()
    with _LOCK:
        _STORE[key] = {
            "audio": list(audio),
            "channels": channels,
            "at": int(time.time()),
            "failed": bool(failed),
        }


def _settled(key):
    """Whether ``key`` needs no fetch: answered, or failed a short while ago."""
    entry = _known(key)
    if entry is None:
        return False
    if not entry.get("failed"):
        return True
    return time.time() - entry.get("at", 0) < _RETRY_AFTER_SECONDS


def _host(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _pause_host(host):
    """Leave ``host`` alone for a while: it refused a read."""
    if host:
        _load()
        with _LOCK:
            _PAUSED_HOSTS[host] = int(time.time())


def _host_paused(host):
    _load()
    with _LOCK:
        when = _PAUSED_HOSTS.get(host)
    return when is not None and time.time() - when < _RETRY_AFTER_SECONDS


def forget_all():
    """Drop what was read this run (for tests)."""
    with _LOCK:
        _STORE.clear()
        _PAUSED_HOSTS.clear()
        _REST_HOSTS.clear()
        del _LOADED[:]


# --- putting it on the rows -------------------------------------------------


def apply_known(rows, key_of, derived_keys=()):
    """Add the audio a stored NFO names to every listing of its release.

    ``key_of`` gives a release name's key (``filter._name_form_key``): every
    spelling of a name, from any indexer, takes the same additions. A row
    whose metadata changes loses its ``derived_keys`` (per-row caches built
    from it). Returns how many rows changed.
    """
    changed = 0
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        title = row.get("title") if isinstance(row, dict) else None
        if not isinstance(meta, dict) or not isinstance(title, str):
            continue
        entry = _known(key_of(title))
        if not entry or not entry["audio"]:
            continue
        audio = meta.get("audio")
        audio = list(audio) if isinstance(audio, list) else []
        added = [name for name in entry["audio"] if name not in audio]
        channels = entry.get("channels", "")
        # The NFO's count is its main track's. It replaces the name's when
        # what the NFO added becomes the release's main track ("DD5.1" in
        # the name, "TrueHD 7.1" in the NFO), and fills in a missing one.
        quality = meta.get("quality", "")
        new_main = main_audio(audio + added, quality) != main_audio(audio, quality)
        use_channels = bool(channels) and (
            not meta.get("channels") or (bool(added) and new_main)
        )
        if use_channels and meta.get("channels") == channels:
            use_channels = False
        if not added and not use_channels:
            continue
        if added:
            meta["audio"] = audio + added
        if use_channels:
            meta["channels"] = channels
        # What the NFO added, so a later pass can tell it from the name's.
        earlier = meta.get(FROM_NFO_KEY)
        earlier = earlier if isinstance(earlier, list) else []
        meta[FROM_NFO_KEY] = earlier + [n for n in added if n not in earlier]
        for name in derived_keys:
            row.pop(name, None)
        changed += 1
    return changed


# --- asking the indexers ----------------------------------------------------


def _releases_setting(settings_getter):
    try:
        raw = settings_getter(SETTING, str(DEFAULT_RELEASES))
        value = int(str(raw).strip())
    except Exception:  # pylint: disable=broad-except
        return DEFAULT_RELEASES
    return max(0, min(value, MAX_RELEASES))


def _candidates(rows, key_of, limit):
    """``[(key, url, title)]`` to fetch among the first ``limit`` releases.

    A release is the rows that share a name key, in the order given (the
    picker's order). Only the first ``limit`` releases are looked at, so a
    search never asks for more however often it is opened: one of them that
    is already settled, or that no listing offers an NFO for, is not
    replaced by a release further down.
    """
    seen = set()
    found = []
    for row in rows:
        title = row.get("title") if isinstance(row, dict) else None
        if not isinstance(title, str) or not title:
            continue
        key = key_of(title)
        if not key or key in seen:
            continue
        if len(seen) >= limit:
            break
        seen.add(key)
        if _settled(key):
            continue
        url = _nfo_url(key, rows, key_of)
        if url:
            found.append((key, url, title))
    return found


def _nfo_url(key, rows, key_of):
    """The NFO link of a listing of ``key``, from an indexer not paused."""
    for row in rows:
        url = row.get("nfo_url") if isinstance(row, dict) else None
        title = row.get("title") if isinstance(row, dict) else None
        if isinstance(url, str) and url and isinstance(title, str):
            if key_of(title) == key and not _host_paused(_host(url)):
                return url
    return ""


# HTTP statuses, and newznab error codes, that mean "not for you, not now":
# a wrong or blocked key, a client the indexer refuses, a limit reached.
_REFUSAL_STATUSES = frozenset((401, 403, 429))


def _is_refusal_code(code):
    return 100 <= code < 200 or 500 <= code < 600


# A newznab error reply, sent with HTTP 200 in place of the NFO.
_ERROR_REPLY = re.compile(
    r"^\s*(?:<\?xml[^>]*\?>)?\s*<error\b[^>]*\bcode=\"(\d+)\"", re.I
)
# The codes that say the release has no NFO to give: nothing to ask for again.
_NO_NFO_CODES = frozenset((300,))


def _error_code(text):
    """The code of a newznab error reply, or None when ``text`` is not one."""
    match = _ERROR_REPLY.match(str(text or "")[:512])
    return int(match.group(1)) if match else None


def _may_ask_mediainfo(host):
    """Whether ``host`` is worth asking for MediaInfo: it has the call, or might."""
    _load()
    with _LOCK:
        state = _REST_HOSTS.get(host)
    if state is None or state["ok"]:
        return True
    return time.time() - state["at"] >= _NO_API_SECONDS


def _has_mediainfo_call(host):
    """Whether ``host`` has been seen to answer the MediaInfo call."""
    _load()
    with _LOCK:
        state = _REST_HOSTS.get(host)
    return bool(state and state["ok"])


def _note_rest(host, has_call):
    _load()
    with _LOCK:
        _REST_HOSTS[host] = {"ok": bool(has_call), "at": int(time.time())}


def _get_with_status(url, headers):
    """``(status, body)`` of a GET; the status is None when nothing answered."""
    try:
        body = http_get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers=headers,
            max_bytes=_MAX_MEDIAINFO_BYTES,
        )
        return 200, body
    except HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:  # pylint: disable=broad-except
            body = ""
        return exc.code, body
    except Exception:  # pylint: disable=broad-except
        return None, ""


def _fetch_mediainfo(key, url, title):
    """Ask the indexer for the release's MediaInfo, where it may have the call.

    True or False when that settled the release (audio found, or the indexer
    refused), None when the NFO should be read: no such call there, no
    MediaInfo for this release, none of its tracks recognised, or no answer.
    """
    host = _host(url)
    request = mediainfo_audio.request_for(url)
    if request is None or not _may_ask_mediainfo(host):
        return None
    status, body = _get_with_status(*request)
    what, tracks = mediainfo_audio.read_reply(status, body)
    if (
        what == mediainfo_audio.REFUSED
        and status != 429
        and not _has_mediainfo_call(host)
    ):
        # An indexer never seen to answer this call, refusing the path: that
        # is how one without the call may answer. Its NFO is still read.
        what = mediainfo_audio.NO_API
    if what == mediainfo_audio.REFUSED:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: MediaInfo for '{}' was refused by the "
            "indexer".format(title),
            xbmc.LOGINFO,
        )
        _remember(key, [], "", failed=True)
        _pause_host(host)
        return False
    if what == mediainfo_audio.NO_API:
        _note_rest(host, False)
        return None
    if what == mediainfo_audio.FAILED:
        return None
    _note_rest(host, True)
    if what != mediainfo_audio.TRACKS:
        return None
    found = mediainfo_audio.merge_tracks(tracks)
    if not found["audio"]:
        return None
    _remember(key, found["audio"], found["channels"])
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: MediaInfo for '{}' names audio: {}".format(
            title, ", ".join(found["audio"])
        ),
        xbmc.LOGINFO,
    )
    return True


def _fetch(key, url, title):
    """Learn one release's audio: MediaInfo first, then its NFO.

    True when audio was found. The answer, or that there was none, is
    remembered either way.
    """
    answer = _fetch_mediainfo(key, url, title)
    if answer is not None:
        return answer
    return _fetch_nfo(key, url, title)


def _fetch_nfo(key, url, title):
    """Fetch one NFO, read it and remember the answer; True when it has audio."""
    try:
        text = http_get(url, timeout=_FETCH_TIMEOUT, max_bytes=_MAX_NFO_BYTES)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' could not be read: {}".format(
                title, redact_text(str(exc))
            ),
            xbmc.LOGINFO,
        )
        _remember(key, [], "", failed=True)
        if getattr(exc, "code", None) in _REFUSAL_STATUSES:
            _pause_host(_host(url))
        return False
    error = _error_code(text)
    if error is not None and error not in _NO_NFO_CODES:
        # The indexer answered with an error, not an NFO (a bad key, a limit
        # reached): ask again another day instead of settling on "no audio".
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' was refused by the indexer "
            "(error {})".format(title, error),
            xbmc.LOGINFO,
        )
        _remember(key, [], "", failed=True)
        if _is_refusal_code(error):
            _pause_host(_host(url))
        return False
    found = audio_from_nfo(text)
    _remember(key, found["audio"], found["channels"])
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: NFO for '{}' names audio: {}".format(
            title, ", ".join(found["audio"]) or "none"
        ),
        xbmc.LOGINFO,
    )
    return bool(found["audio"])


def learn_for_top(rows, key_of, settings_getter, wait=_WAIT_SECONDS):
    """Read the NFOs of the top releases of ``rows``; True if any named audio.

    ``rows`` are the picker's rows in its order. The fetches run on daemon
    threads, one per indexer, and this waits ``wait`` seconds for them. One
    that is slower still finishes and is stored for the next search. True
    means the rows should be parsed again to take the new facts.
    """
    try:
        limit = _releases_setting(settings_getter)
        if limit <= 0:
            return False
        candidates = _candidates(rows, key_of, limit)
    except Exception:  # pylint: disable=broad-except
        return False
    if not candidates:
        return False
    results = []

    def _work(batch):
        # One indexer's reads, one after another: when it refuses one, the
        # rest are not sent.
        try:
            for key, url, title in batch:
                if _host_paused(_host(url)):
                    break
                results.append(_fetch(key, url, title))
        finally:
            _save()

    by_host = {}
    for candidate in candidates:
        by_host.setdefault(_host(candidate[1]), []).append(candidate)
    threads = [
        threading.Thread(target=_work, args=(batch,), daemon=True)
        for batch in by_host.values()
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.0, wait)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return any(results)
