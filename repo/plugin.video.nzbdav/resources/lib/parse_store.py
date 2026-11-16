# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Release names already parsed, kept on disk from one search to the next.

Parsing a release name is most of a first picker open: about half a
millisecond a name on a desktop, ten times that on a TV box, and a search
brings back thousands of names. The search cache keeps a search's parses
for as long as it keeps the search (``cache_ttl_minutes``, 30 by default).
A repeat search of the same film or episode after that, or a search of the
next episode of a show, brings back many names already seen.

This store keeps ``name -> filter.parse_title_metadata(name)`` in one file,
``parsed_names.json`` in the add-on's profile folder, beside the search
cache's folder. A parse depends only on the
name and on the add-on's code, so the file carries the code's fingerprint
(``code_stamp``) and is thrown away when the add-on changes. It is
capped at ``MAX_NAMES`` names (about 600 bytes each); past that the names
used least recently go first. Writing happens on a background thread, the
serializing included, so the picker does not wait for it.

What a later step adds to a row's parse (another spelling's details, the
group of the upload it is, an NFO's audio) is not stored here: only what
the name parses to.

Everything here fails soft. An unreadable or unwritable file means names
are parsed as before.
"""

import json
import os
import threading
import time

import xbmc
import xbmcvfs

from resources.lib.code_stamp import code_stamp

MAX_NAMES = 8000
_FILE_NAME = "parsed_names.json"
# The add-on profile folder, as ``cache._PROFILE_PATH``.
_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"
_VERSION = 1

_LOCK = threading.Lock()
# name -> [day last used, parse], filled from the file on first use.
_NAMES = {}
_STATE = {"loaded": False, "changed": False, "stamp": None, "writes": 0, "written": 0}


def _today():
    return int(time.time() // 86400)


def _store_path():
    """The file's path, or None when the profile folder cannot be used."""
    return _profile_store_path()


def _profile_store_path():
    """Where the file goes (``_store_path`` is the seam tests replace).

    The add-on's profile folder, beside the search cache's own folder:
    inside it, the cache would count the file as a search and evict it.
    """
    try:
        return os.path.join(xbmcvfs.translatePath(_PROFILE_PATH), _FILE_NAME)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _load():
    """Read the file once per run; call with ``_LOCK`` held."""
    if _STATE["loaded"]:
        return
    _STATE["loaded"] = True
    _STATE["stamp"] = code_stamp()
    path = _store_path()
    if path is None or _STATE["stamp"] is None:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return
    if (
        not isinstance(data, dict)
        or data.get("v") != _VERSION
        or data.get("parser") != _STATE["stamp"]
        or not isinstance(data.get("names"), dict)
    ):
        # Another build of the add-on parsed these: start again.
        _STATE["changed"] = True
        return
    for name, entry in data["names"].items():
        if (
            isinstance(name, str)
            and isinstance(entry, list)
            and len(entry) == 2
            and isinstance(entry[0], int)
            and isinstance(entry[1], dict)
        ):
            _NAMES[name] = entry


def parsed(name, parse):
    """The parse of ``name``, from the store or from ``parse(name)``.

    The caller gets an object of its own: a stored parse is copied.
    """
    with _LOCK:
        _load()
        entry = _NAMES.pop(name, None)
        if entry is not None:
            # Kept in order of use, the most recent last: that order is
            # what is kept when the store is full, and what is written.
            _NAMES[name] = entry
            if entry[0] != _today():
                entry[0] = _today()
                _STATE["changed"] = True
            text = json.dumps(entry[1])
            return json.loads(text)
    meta = parse(name)
    if isinstance(meta, dict) and isinstance(name, str):
        with _LOCK:
            _NAMES[name] = [_today(), json.loads(json.dumps(meta))]
            _STATE["changed"] = True
    return meta


def save_soon():
    """Write the store in the background, if anything changed.

    The write runs on a daemon thread so the picker does not wait for it;
    one that is cut short is written again the next time.
    """
    with _LOCK:
        if not _STATE["changed"] or _STATE["stamp"] is None:
            return
        _STATE["changed"] = False
        # Oldest use first: past the cap, those go.
        while len(_NAMES) > MAX_NAMES:
            del _NAMES[next(iter(_NAMES))]
        path = _store_path()
        if path is None:
            return
        # A snapshot of the entries; their parses are never changed in place.
        snapshot = (_STATE["stamp"], list(_NAMES.items()))
        _STATE["writes"] += 1
        number = _STATE["writes"]
    try:
        threading.Thread(
            target=_write,
            args=(path, snapshot, number),
            name="nzbdav-parse-store",
            daemon=True,
        ).start()
    except RuntimeError:
        _write(path, snapshot, number)


def _write(path, snapshot, number):
    """Write ``snapshot`` to ``path`` through a temporary file of its own.

    ``number`` orders the writes of this run: one that finds a later write
    already done leaves it, so an older snapshot never replaces a newer one.
    Another Kodi run writing at the same time may still win; its names are
    the ones kept then, and nothing is ever half written.
    """
    import tempfile

    stamp, entries = snapshot
    text = json.dumps(
        {"v": _VERSION, "parser": stamp, "names": dict(entries)},
        separators=(",", ":"),
    )
    tmp_path = None
    try:
        handle, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix=".parsed_names-", suffix=".tmp"
        )
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        with _LOCK:
            if number < _STATE["written"]:
                return
            os.replace(tmp_path, path)
            _STATE["written"] = number
            tmp_path = None
    except OSError as exc:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: parsed names not saved: {}".format(exc),
            xbmc.LOGDEBUG,
        )
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def forget():
    """Drop what is held in memory, so the next use reads the file (tests)."""
    with _LOCK:
        _NAMES.clear()
        _STATE.update(loaded=False, changed=False, stamp=None, writes=0, written=0)
