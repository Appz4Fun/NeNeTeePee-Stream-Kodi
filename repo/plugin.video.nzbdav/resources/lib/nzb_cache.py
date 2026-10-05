# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Picked NZBs kept on the Kodi box for a day after a canceled NZBGet play.

Canceling a play deletes its pick from NZBGet (its backups stay parked, see
``nzbget_submit_ledger``). The pick's NZB file was already downloaded from the
indexer, so instead of deleting it the resolve moves it here; replaying the
same release within ``TTL_SECONDS`` re-sends that file to NZBGet instead of
grabbing it from the indexer again. Files are named by a hash of the link
with its credentials stripped (``nzbget_submit_ledger.link_key``), so no
indexer key lands on disk. Fails soft: any error means "not cached".
"""

import contextlib
import hashlib
import os
import time

import xbmcvfs

from resources.lib.nzbget_submit_ledger import link_key

_CACHE_SPECIAL_PATH = "special://profile/addon_data/plugin.video.nzbdav/nzb_cache"
TTL_SECONDS = 24 * 60 * 60
# A handful of canceled picks a day; the cap only bounds a runaway.
_MAX_FILES = 20
# The fleet's pick ceiling (nzbget_api): never read back anything larger.
_MAX_BYTES = 100 * 1024 * 1024


def _cache_dir():
    path = xbmcvfs.translatePath(_CACHE_SPECIAL_PATH)
    os.makedirs(path, exist_ok=True)
    return path


def _cache_path(link):
    key = link_key(link)
    if not key:
        return None
    name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".nzb"
    return os.path.join(_cache_dir(), name)


def _prune(directory, now):
    """Drop expired files, then the oldest past ``_MAX_FILES``."""
    entries = []
    for name in os.listdir(directory):
        if not name.endswith(".nzb"):
            continue
        path = os.path.join(directory, name)
        with contextlib.suppress(OSError):
            entries.append((os.path.getmtime(path), path))
    entries.sort(reverse=True)
    for index, (mtime, path) in enumerate(entries):
        if index >= _MAX_FILES or now - mtime >= TTL_SECONDS:
            with contextlib.suppress(OSError):
                os.remove(path)


def keep(link, source_path, now=None):
    """Move the pick's NZB at ``source_path`` into the cache; True on success."""
    now = time.time() if now is None else now
    try:
        target = _cache_path(link)
        if target is None or not source_path or not os.path.isfile(source_path):
            return False
        # Rename only: atomic, and instant on the resolve thread. A parked
        # pick on another filesystem (system temp) is simply not cached --
        # never a slow, possibly partial cross-device copy.
        os.replace(source_path, target)
        os.utime(target, (now, now))
        _prune(os.path.dirname(target), now)
        return True
    except (OSError, ValueError):
        return False


def has(link, now=None):
    """Whether a fresh cached NZB exists for ``link`` (a stat, no read)."""
    now = time.time() if now is None else now
    try:
        path = _cache_path(link)
        return bool(
            path
            and os.path.isfile(path)
            and now - os.path.getmtime(path) < TTL_SECONDS
            and 0 < os.path.getsize(path) <= _MAX_BYTES
        )
    except (OSError, ValueError):
        return False


def load(link, now=None):
    """The cached NZB bytes for ``link`` (kept less than a day ago), or None."""
    now = time.time() if now is None else now
    try:
        path = _cache_path(link)
        if path is None or not os.path.isfile(path):
            return None
        if now - os.path.getmtime(path) >= TTL_SECONDS:
            return None
        if os.path.getsize(path) > _MAX_BYTES:
            return None
        with open(path, "rb") as handle:
            return handle.read() or None
    except (OSError, ValueError):
        return None
