# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""NZBs this add-on sent to NZBGet in the last day, so a replay skips them (#372).

Every play of a release sends the pick plus every same-release backup. Without
a record, playing the same title again re-downloads every backup from the
indexer (one grab each) and appends another copy of it to NZBGet. This ledger
remembers each NZB link that reached NZBGet -- with its NZBID and DupeKey --
for ``TTL_SECONDS``; the fleet then skips a backup whose earlier copy NZBGet
still holds under the same DupeKey -- queued, or parked in history as a
``DELETED/DUPE`` backup NZBGet can fail over to (the resolve then tracks it
as its own) -- or already failed or refused as a copy.

Links are stored with their credentials (``apikey`` and friends) stripped, so
no indexer key lands on disk. The file lives in the add-on's profile folder
(``/storage/.kodi/userdata/addon_data/...`` on CoreELEC). Fails soft
throughout: any read or write error degrades to "nothing recorded", which is
the old resubmit-everything behavior.
"""

import contextlib
import json
import os
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import xbmcvfs

from resources.lib.http_util import redact_url

# Resolved via special:// (never xbmcaddon.Addon): the RunScript context must
# not touch Addon(), and translatePath needs no handle.
_PROFILE_SPECIAL_PATH = "special://profile/addon_data/plugin.video.nzbdav"
_FILENAME = "nzbget_submitted.json"
TTL_SECONDS = 24 * 60 * 60
# A busy day of plays is a few hundred NZBs; the cap only bounds a runaway.
_MAX_ENTRIES = 5000
# Credential parameters beyond the shared ``http_util.redact_url`` set
# (NZBGeek-style ``i``/``r``, tracker ``passkey``): dropped from the identity.
_EXTRA_SECRET_PARAMS = frozenset(("i", "r", "passkey"))
# ``redact_url``'s mask: a masked parameter is dropped from the identity.
_REDACTED = "REDACTED"
_LOCK = threading.Lock()


def link_key(link):
    """``link`` without credentials (sorted query, lowercased host), or "".

    ``http_util.redact_url`` masks every credential parameter the add-on
    knows (``apikey``, ``key``, ``auth``, ``password``, ``access_token``...,
    including inside URL-valued parameters); masked parameters and the extra
    names are then dropped and any ``user:pass@`` userinfo removed, so
    nothing secret is written to disk.
    """
    text = str(link or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(redact_url(text))
        query = sorted(
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if name.lower() not in _EXTRA_SECRET_PARAMS and value != _REDACTED
        )
    except ValueError:
        return ""
    host = parts.netloc.rsplit("@", 1)[-1].lower()
    return urlunsplit((parts.scheme.lower(), host, parts.path, urlencode(query), ""))


def _path():
    profile = xbmcvfs.translatePath(_PROFILE_SPECIAL_PATH)
    os.makedirs(profile, exist_ok=True)
    return os.path.join(profile, _FILENAME)


def _load():
    try:
        with open(_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def _save(entries):
    try:
        path = _path()
        handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    except OSError:
        return
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(entries, out)
        os.replace(tmp, path)
    except OSError:
        # Best-effort: an unwritten ledger only means the next play resends.
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _age(entry, now):
    """Seconds since ``entry`` was recorded, or None when unreadable."""
    try:
        return now - float(entry.get("at", 0))
    except (TypeError, ValueError):
        return None


def _fresh(entries, now):
    fresh = []
    for entry in entries:
        age = _age(entry, now)
        if age is not None and 0 <= age < TTL_SECONDS:
            fresh.append(entry)
    return fresh


def _nzbid(value):
    try:
        nzbid = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return nzbid if nzbid > 0 else 0


def _norm_key(dupe_key):
    return str(dupe_key or "").strip().lower()


def record(rows, dupe_key, now=None):
    """Remember every row in ``rows`` that NZBGet accepted (has ``_nzbid``)."""
    now = time.time() if now is None else now
    key = _norm_key(dupe_key)
    new = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        link = link_key(row.get("link"))
        nzbid = _nzbid(row.get("_nzbid"))
        if link and nzbid and key:
            new.append(
                {
                    "link": link,
                    "nzbid": nzbid,
                    "dupe_key": key,
                    "size": str(row.get("size") or ""),
                    "pubdate": str(row.get("pubdate") or ""),
                    "_posted_epoch": row.get("_posted_epoch"),
                    "at": now,
                }
            )
    if not new:
        return
    replaced = {(entry["link"], entry["dupe_key"]) for entry in new}
    with _LOCK:
        entries = [
            entry
            for entry in _fresh(_load(), now)
            if (entry.get("link"), entry.get("dupe_key")) not in replaced
        ]
        _save((entries + new)[-_MAX_ENTRIES:])


def held(dupe_key, member_states, now=None):
    """Fresh entries for ``dupe_key`` that NZBGet still holds, with their state.

    ``member_states`` is ``nzbget_api.dupekey_member_states``; each returned
    entry is a copy carrying ``"state"`` (``"parked"`` or ``"dead"``).
    """
    now = time.time() if now is None else now
    key = _norm_key(dupe_key)
    if not key or not member_states:
        return []
    with _LOCK:
        entries = _fresh(_load(), now)
    found = []
    for entry in entries:
        state = member_states.get(_nzbid(entry.get("nzbid")))
        if entry.get("dupe_key") == key and state:
            found.append(dict(entry, state=state))
    return found


def forget(nzbids):
    """Drop the entries for ``nzbids`` (deleted from NZBGet by this add-on)."""
    gone = {_nzbid(nzbid) for nzbid in nzbids or []} - {0}
    if not gone:
        return
    with _LOCK:
        entries = _load()
        kept = [entry for entry in entries if _nzbid(entry.get("nzbid")) not in gone]
        if len(kept) != len(entries):
            _save(kept)
