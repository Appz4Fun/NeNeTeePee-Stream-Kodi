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

import xbmc
import xbmcvfs

from resources.lib.filter_tags import detect_audio, detect_channels
from resources.lib.http_util import http_get, redact_text

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
# How long the picker waits for the answers before opening without them.
_WAIT_SECONDS = 2.0

# MediaInfo's names for formats, as release names write them.
_MEDIAINFO_NAMES = (
    (
        re.compile(r"dolby digital plus with dolby atmos|e-?ac-?3[ /+]*joc", re.I),
        "DDP Atmos",
    ),
    (re.compile(r"dolby truehd with dolby atmos|mlp fba 16-ch", re.I), "TrueHD Atmos"),
    (re.compile(r"dts[ :-]?x\b|dts xll x", re.I), "DTS-X DTS-HD MA"),
    (re.compile(r"dts-hd master audio|dts xll", re.I), "DTS-HD MA"),
    (re.compile(r"dts-hd high resolution(?: audio)?|dts xbr", re.I), "DTS-HD HR"),
    (re.compile(r"dolby digital plus|e-?ac-?3", re.I), "DDP"),
    (re.compile(r"mlp fba|dolby truehd", re.I), "TrueHD"),
    (re.compile(r"dolby digital|\bac-?3\b", re.I), "DD"),
)
# A line labelled as the audio ("Audio.........:", "AUDiO #1 :", "Ton:"), in
# the languages NFOs are written in. The word must be the label: a sentence
# that merely mentions audio is not read.
_AUDIO_LABEL = re.compile(
    r"^\W*(?:audio|sound|tonspur|ton|son|lyd|ljud|geluid)\b[\w #./-]{0,20}?[:=]",
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
_CHANNEL_WORDS = re.compile(r"\b([1-9])\s*(?:ch\b|channels?\b)", re.I)
_CHANNEL_COUNT = {"1": "1.0", "2": "2.0", "6": "5.1", "8": "7.1"}

_LOCK = threading.Lock()
_STORE = {}
_LOADED = []


def _audio_lines(text):
    """The lines of an NFO that describe its audio tracks.

    A line labelled as the audio and the unlabelled lines right under it (a
    second track), or the format lines of a MediaInfo "Audio" block.
    """
    continuation = 0
    in_section = False
    for line in str(text or "").replace("\r", "\n").split("\n"):
        if not line.strip():
            continuation = 0
            in_section = False
        elif _AUDIO_SECTION.match(line):
            continuation = 0
            in_section = True
        elif _AUDIO_LABEL.match(line):
            continuation = _CONTINUATION_LINES
            yield line
        elif in_section:
            if _SECTION_FIELD.match(line):
                yield line
        elif continuation and not _LABELLED.match(line):
            continuation -= 1
            yield line
        else:
            continuation = 0


def audio_from_nfo(text):
    """``{"audio": [...], "channels": "5.1"}`` read from an NFO's text.

    The audio list is empty when the NFO names no format the add-on knows.
    Only the lines that describe the audio tracks are read (``_audio_lines``).
    """
    formats = []
    channels = ""
    for line in _audio_lines(text):
        readable = line
        for pattern, name in _MEDIAINFO_NAMES:
            readable = pattern.sub(" {} ".format(name), readable)
        for name in detect_audio(readable):
            if name not in formats:
                formats.append(name)
        if not channels:
            counts = detect_channels(readable)
            words = _CHANNEL_WORDS.search(readable)
            if counts:
                channels = counts[0]
            elif words:
                channels = _CHANNEL_COUNT.get(words.group(1), "")
    return {"audio": formats, "channels": channels if formats else ""}


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
    """Write the store; a failed write only costs the next run a fetch."""
    with _LOCK:
        entries = sorted(_STORE.items(), key=lambda item: item[1].get("at", 0))
        for key, _entry in entries[: max(0, len(entries) - _MAX_STORED)]:
            del _STORE[key]
        data = {"v": _STORE_VERSION, "releases": dict(_STORE)}
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


def forget_all():
    """Drop what was read this run (for tests)."""
    with _LOCK:
        _STORE.clear()
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
        fill_channels = bool(channels) and not meta.get("channels")
        if not added and not fill_channels:
            continue
        if added:
            meta["audio"] = audio + added
        if fill_channels:
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
    for row in rows:
        url = row.get("nfo_url") if isinstance(row, dict) else None
        title = row.get("title") if isinstance(row, dict) else None
        if isinstance(url, str) and url and isinstance(title, str):
            if key_of(title) == key:
                return url
    return ""


def _fetch(key, url, title):
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

    ``rows`` are the picker's rows in its order. The fetches run side by
    side on daemon threads and this waits ``wait`` seconds for them. One
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

    def _work(key, url, title):
        try:
            results.append(_fetch(key, url, title))
        finally:
            _save()

    threads = [
        threading.Thread(target=_work, args=candidate, daemon=True)
        for candidate in candidates
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.0, wait)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return any(results)
