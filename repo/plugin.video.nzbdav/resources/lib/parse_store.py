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
(``cache._parser_stamp``) and is thrown away when the add-on changes. It is
capped at ``MAX_NAMES`` names (about 600 bytes each); past that the names
used least recently go first.

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

MAX_NAMES = 8000
_FILE_NAME = "parsed_names.json"
_VERSION = 1

_LOCK = threading.Lock()
# name -> [day last used, parse], filled from the file on first use.
_NAMES = {}
_STATE = {"loaded": False, "changed": False, "stamp": None}


def _today():
    return int(time.time() // 86400)


def _store_path():
    """The file's path, or None when the profile folder cannot be used."""
    return _profile_store_path()


def _profile_store_path():
    """Where the file goes (``_store_path`` is the seam tests replace)."""
    from resources.lib import cache

    try:
        # The add-on's profile folder, beside the search cache's own folder:
        # inside it, the cache would count the file as a search and evict it.
        return os.path.join(os.path.dirname(cache._get_cache_dir()), _FILE_NAME)
    except (OSError, TypeError, ValueError):
        return None


def _load():
    """Read the file once per run; call with ``_LOCK`` held."""
    if _STATE["loaded"]:
        return
    _STATE["loaded"] = True
    from resources.lib import cache

    _STATE["stamp"] = cache._parser_stamp()
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
        entry = _NAMES.get(name)
        if entry is not None:
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
        keep = sorted(_NAMES.items(), key=lambda item: item[1][0], reverse=True)
        keep = keep[:MAX_NAMES]
        if len(keep) < len(_NAMES):
            _NAMES.clear()
            _NAMES.update(keep)
        data = {
            "v": _VERSION,
            "parser": _STATE["stamp"],
            "names": dict(keep),
        }
        text = json.dumps(data, separators=(",", ":"))
    try:
        threading.Thread(
            target=_write, args=(text,), name="nzbdav-parse-store", daemon=True
        ).start()
    except RuntimeError:
        _write(text)


def _write(text):
    path = _store_path()
    if path is None:
        return
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except OSError as exc:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: parsed names not saved: {}".format(exc),
            xbmc.LOGDEBUG,
        )
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def forget():
    """Drop what is held in memory, so the next use reads the file (tests)."""
    with _LOCK:
        _NAMES.clear()
        _STATE.update(loaded=False, changed=False, stamp=None)
