# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The NFO and MediaInfo texts of a release, kept for the viewer.

``nfo_audio`` reads a release's NFO or MediaInfo to learn its audio and
dynamic range, and keeps only those facts. The picker's viewer
(``nfo_viewer``) shows the text itself, so the text is kept here: one small
JSON file per release name, in ``release_docs`` in the add-on's data folder.

* A text is kept per indexer host. Indexers mostly carry the very same NFO,
  so ``tabs`` puts identical texts together: one tab per unique text, with
  every indexer that carries it.
* An indexer that had nothing for the release is remembered too
  (``mark_none``), so the viewer does not ask it again.
* Only the newest ``_MAX_RELEASES`` releases are kept.

Texts hold no key: they are what the indexer returned, never the link.
"""

import hashlib
import json
import os
import threading
import time

import xbmcvfs

NFO = "nfo"
MEDIAINFO = "mediainfo"
KINDS = (NFO, MEDIAINFO)
# How a kind is named and colored, on the picker's rows and in the viewer:
# the tag, its color, and the color of the tag of a tab that is not selected.
LABELS = {NFO: "NFO", MEDIAINFO: "MEDIAINFO"}
COLORS = {NFO: "FFFDE047", MEDIAINFO: "FFA3E635"}
DIMMED_COLORS = {NFO: "FF8A7A26", MEDIAINFO: "FF65902A"}

_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"
_DIR_NAME = "release_docs"
_INDEX_NAME = "index.json"
_VERSION = 1
_MAX_RELEASES = 400
_MAX_TEXT = 200000

_LOCK = threading.Lock()
# One writer at a time, from the read of a release's file to its replace.
_SAVE_LOCK = threading.Lock()
# file name -> {"nfo": bool, "mediainfo": bool, "at": seconds, "none": [...]}
# ``none`` lists the "kind host" pairs found to have nothing.
_INDEX = {}
_LOADED = []


def _dir():
    return os.path.join(xbmcvfs.translatePath(_PROFILE_PATH), _DIR_NAME)


def _file_name(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:24] + ".json"  # nosec B324


def _load_index():
    with _LOCK:
        if _LOADED:
            return
        _LOADED.append(True)
        try:
            with open(os.path.join(_dir(), _INDEX_NAME), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, TypeError, ValueError):
            return
        entries = data.get("releases") if isinstance(data, dict) else None
        if not isinstance(entries, dict) or data.get("v") != _VERSION:
            return
        for name, entry in entries.items():
            if isinstance(name, str) and isinstance(entry, dict):
                _INDEX[name] = {
                    NFO: entry.get(NFO) is True,
                    MEDIAINFO: entry.get(MEDIAINFO) is True,
                    "at": entry.get("at") if isinstance(entry.get("at"), int) else 0,
                    "none": [
                        pair
                        for pair in entry.get("none") or ()
                        if isinstance(pair, str)
                    ],
                }


def _write_json(path, data):
    """Write ``data`` to ``path`` through a temporary file; False on failure."""
    tmp_path = "{}.{}.tmp".format(path, threading.get_ident())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp_path, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            os.remove(tmp_path)
        except OSError:
            # Nothing was left behind, or it cannot be removed.
            pass
        return False


def _valid_doc(doc):
    return (
        isinstance(doc, dict)
        and doc.get("kind") in KINDS
        and isinstance(doc.get("host"), str)
        and isinstance(doc.get("text"), str)
    )


def load(key):
    """The kept record of the release ``key``: ``{"docs": [...], "none": {}}``.

    ``docs`` are ``{"kind", "host", "text"}``; ``none`` maps ``"kind host"``
    to when that indexer was found to have nothing. An unknown or unreadable
    release is an empty record.
    """
    empty = {"docs": [], "none": {}}
    if not isinstance(key, str) or not key:
        return empty
    try:
        with open(os.path.join(_dir(), _file_name(key)), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, TypeError, ValueError):
        return empty
    if not isinstance(data, dict) or data.get("v") != _VERSION:
        return empty
    if data.get("key") != key:
        return empty
    docs = [doc for doc in data.get("docs") or () if _valid_doc(doc)]
    none = data.get("none")
    none = dict(none) if isinstance(none, dict) else {}
    return {"docs": docs, "none": none}


def _save(key, record):
    name = _file_name(key)
    data = {"v": _VERSION, "key": key, "docs": record["docs"], "none": record["none"]}
    if not _write_json(os.path.join(_dir(), name), data):
        return
    _load_index()
    with _LOCK:
        _INDEX[name] = {
            NFO: any(doc["kind"] == NFO for doc in record["docs"]),
            MEDIAINFO: any(doc["kind"] == MEDIAINFO for doc in record["docs"]),
            "at": int(time.time()),
            "none": sorted(record["none"]),
        }
        oldest = sorted(_INDEX.items(), key=lambda item: item[1]["at"])
        dropped = [n for n, _entry in oldest[: max(0, len(oldest) - _MAX_RELEASES)]]
        for old in dropped:
            del _INDEX[old]
        index = {"v": _VERSION, "releases": dict(_INDEX)}
    for old in dropped:
        try:
            os.remove(os.path.join(_dir(), old))
        except OSError:
            # Already gone.
            pass
    _write_json(os.path.join(_dir(), _INDEX_NAME), index)


def keep(key, kind, host, text):
    """Keep the ``kind`` text the indexer at ``host`` returned for ``key``."""
    if kind not in KINDS or not isinstance(text, str) or not text.strip():
        return
    if not isinstance(key, str) or not key or not isinstance(host, str) or not host:
        return
    with _SAVE_LOCK:
        record = load(key)
        record["docs"] = [
            doc
            for doc in record["docs"]
            if not (doc["kind"] == kind and doc["host"] == host)
        ]
        record["docs"].append({"kind": kind, "host": host, "text": text[:_MAX_TEXT]})
        record["none"].pop("{} {}".format(kind, host), None)
        _save(key, record)


def mark_none(key, kind, host):
    """Remember that the indexer at ``host`` has no ``kind`` text for ``key``."""
    if kind not in KINDS or not isinstance(key, str) or not key or not host:
        return
    with _SAVE_LOCK:
        record = load(key)
        if any(doc["kind"] == kind and doc["host"] == host for doc in record["docs"]):
            return
        record["none"]["{} {}".format(kind, host)] = int(time.time())
        _save(key, record)


def settled(record, kind, host):
    """Whether ``host`` was already asked for the release's ``kind`` text."""
    if any(doc["kind"] == kind and doc["host"] == host for doc in record["docs"]):
        return True
    return "{} {}".format(kind, host) in record["none"]


def kinds_known(key):
    """``(has_nfo, has_mediainfo)`` for the release ``key``, from the index.

    Cheap: the picker asks for every row, and no release file is opened.
    """
    if not isinstance(key, str) or not key:
        return False, False
    _load_index()
    with _LOCK:
        entry = _INDEX.get(_file_name(key))
    if not entry:
        return False, False
    return entry[NFO], entry[MEDIAINFO]


def asked_none(key):
    """The ``"kind host"`` pairs found to have nothing for the release ``key``.

    From the index, like ``kinds_known``: no release file is opened.
    """
    if not isinstance(key, str) or not key:
        return frozenset()
    _load_index()
    with _LOCK:
        entry = _INDEX.get(_file_name(key))
    return frozenset(entry["none"]) if entry else frozenset()


def forget_all():
    """Drop what is held in memory (for tests)."""
    with _LOCK:
        _INDEX.clear()
        del _LOADED[:]


def _same_text_key(text):
    """A key equal for two copies of one text, whatever their line endings."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def tabs(record, indexers_of=None):
    """The viewer's tabs: one per unique text, NFOs first.

    ``[{"kind", "text", "hosts": [...], "indexers": [...]}]``. Identical
    texts from several indexers are one tab naming them all. ``indexers_of``
    gives the display names of the indexers at a host (the picker's rows
    know them); without it, or for a host it does not know, the host is the
    name. Among the NFOs, the one most indexers carry comes first.
    """
    grouped = []
    for doc in record.get("docs") or ():
        same = _same_text_key(doc["text"])
        for tab in grouped:
            if tab["kind"] == doc["kind"] and tab["same"] == same:
                if doc["host"] not in tab["hosts"]:
                    tab["hosts"].append(doc["host"])
                break
        else:
            grouped.append(
                {
                    "kind": doc["kind"],
                    "same": same,
                    "text": doc["text"],
                    "hosts": [doc["host"]],
                }
            )
    result = []
    for tab in grouped:
        names = []
        for host in tab["hosts"]:
            found = list(indexers_of(host)) if indexers_of else []
            for name in found or [host]:
                if name and name not in names:
                    names.append(name)
        names.sort(key=str.casefold)
        result.append(
            {
                "kind": tab["kind"],
                "text": tab["text"],
                "hosts": list(tab["hosts"]),
                "indexers": names,
            }
        )
    result.sort(key=lambda tab: (KINDS.index(tab["kind"]), -len(tab["indexers"])))
    return result
