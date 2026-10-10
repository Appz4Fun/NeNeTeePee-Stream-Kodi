# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Simple JSON-based search result cache."""

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

MAX_CACHE_SIZE_BYTES = 52428800  # 50 MB
MAX_CACHE_ENTRY_COUNT = 1000
CACHE_TTL_SETTING = "cache_ttl_minutes"
DEFAULT_CACHE_TTL_MINUTES = 30
MAX_CACHE_TTL_MINUTES = 1440
_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"


def _get_cache_dir():
    # The add-on profile path, resolved without the Kodi add-on info API: that
    # lookup can crash CoreELEC inside the TMDBHelper RunScript context.
    profile = xbmcvfs.translatePath(_PROFILE_PATH)
    cache_dir = os.path.join(profile, "cache")
    # `exist_ok=True` rather than the exists-then-makedirs pattern, which
    # races a concurrent first-call: two callers can both observe "not
    # exists" and the second hits FileExistsError. TODO.md §H.2-L24.
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _cache_path(search_type, title, kwargs):
    """The cache file for a search, or None when the cache folder is unusable.

    A read-only profile, or a ``cache`` file where the folder should be, makes
    the cache a miss instead of stopping the search that called it.
    """
    try:
        cache_dir = _get_cache_dir()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Search cache unavailable: {}".format(exc),
            xbmc.LOGWARNING,
        )
        return None
    return os.path.join(cache_dir, _cache_key(search_type, title, **kwargs) + ".json")


def _cache_key(
    search_type, title, year="", imdb="", season="", episode="", tvdb="", tmdb_id=""
):
    """Generate a filesystem-safe, collision-resistant cache key.

    Previous implementation collapsed non-alphanumeric characters to ``_``
    and truncated at 200 chars. That meant "Spider-Man: No Way Home" and
    "Spider_Man_ No Way Home" collapsed to the same filename, and any two
    distinct titles sharing a 200-char prefix aliased to the same cache
    file. Stale results would be served across searches.

    Switch to SHA-256 of the joined parts: deterministic, 64-char hex
    filename, no collisions in practice. Prefix the ``search_type`` so
    a glance at the cache dir still shows which bucket a file belongs
    to; the readable ``_make_legible_slug`` tail is cosmetic.
    """
    import hashlib

    parts = [search_type, title, year, imdb, season, episode, tvdb, tmdb_id]
    joined = "\x1f".join(str(p) for p in parts)  # unit-separator—can't appear in inputs
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    legible = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
    return "{}_{}_{}".format(search_type, legible or "untitled", digest)


def _get_cache_ttl_seconds(settings_getter=None):
    """Return the configured cache TTL in seconds (the setting is in minutes).

    ``settings_getter`` (``(key, default) -> str``) lets the RunScript player
    read the pure-XML settings snapshot instead of Kodi's settings API.
    """
    default_ttl = DEFAULT_CACHE_TTL_MINUTES * 60
    try:
        if settings_getter is None:
            addon = xbmcaddon.Addon("plugin.video.nzbdav")
            raw_ttl = addon.getSetting(CACHE_TTL_SETTING)
        else:
            raw_ttl = settings_getter(CACHE_TTL_SETTING, "")
    except RuntimeError as exc:
        xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: {} setting unavailable; " "using default: {}"
            ).format(CACHE_TTL_SETTING, exc),
            xbmc.LOGWARNING,
        )
        return default_ttl

    try:
        minutes = int(raw_ttl or DEFAULT_CACHE_TTL_MINUTES)
    except (TypeError, ValueError):
        return default_ttl
    return max(0, min(minutes, MAX_CACHE_TTL_MINUTES)) * 60


def _try_remove(path):
    """Best-effort removal of a stale/corrupt/expired cache file.

    A failed delete (perms/race/already-gone) is non-fatal: the caller returns
    None and the stale file is simply ignored until the next write or eviction,
    so any OSError is swallowed rather than propagated.
    """
    try:
        os.remove(path)
    except OSError:
        # Best-effort delete; a perms/race/already-gone error is non-fatal.
        pass


def _read_fresh_cache(path, cache_ttl, title):
    """Return cached results if present and fresh, else None.

    Deletes the file when its timestamp is missing/stale or its JSON is
    corrupt. Reuses ``get_cached``'s log line on a hit.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    timestamp = data.get("timestamp")
    if not isinstance(timestamp, (int, float)):
        _try_remove(path)
        return None
    if time.time() - timestamp > cache_ttl:
        _try_remove(path)
        return None
    try:
        os.utime(path, None)
    except OSError:
        # Best-effort mtime update; still return the cache hit if it fails.
        pass
    xbmc.log("NeNeTeePee-Stream-Kodi: Cache hit for '{}'".format(title), xbmc.LOGDEBUG)
    results = data.get("results", [])
    uploads = data.get("hydra_uploads")
    if isinstance(results, list) and isinstance(uploads, list):
        from resources.lib.hydra import _cache_search_uploads

        _cache_search_uploads(results, uploads)
    return results


def get_cached(search_type, title, settings_getter=None, **kwargs):
    """Get cached results if fresh enough. Returns list or None."""
    cache_ttl = _get_cache_ttl_seconds(settings_getter)
    if cache_ttl <= 0:
        return None

    path = _cache_path(search_type, title, kwargs)
    if path is None or not os.path.exists(path):
        return None

    try:
        return _read_fresh_cache(path, cache_ttl, title)
    except json.JSONDecodeError:
        _try_remove(path)
        return None
    except OSError:
        return None


def set_cached(search_type, title, results, settings_getter=None, **kwargs):
    """Cache search results."""
    cache_ttl = _get_cache_ttl_seconds(settings_getter)
    if cache_ttl <= 0:
        return

    path = _cache_path(search_type, title, kwargs)
    if path is None:
        return

    try:
        from resources.lib.hydra import split_search_uploads

        plain_results, uploads = split_search_uploads(results)
        data = {
            "timestamp": time.time(),
            "results": plain_results,
            "hydra_uploads": uploads,
        }
        # Atomic write: dump to a sibling temp file then os.replace onto the
        # final path. A concurrent get_cached() sees either the old file
        # or the new file, never a half-written JSON blob that would
        # JSONDecodeError.
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: Cached {} results for '{}'").format(
                len(results), title
            ),
            xbmc.LOGDEBUG,
        )
    except OSError:
        # Clean up the temp file if the replace didn't happen.
        try:
            os.remove(tmp_path)
        except (OSError, NameError):
            pass
    _evict_oldest()


def _cache_file_size(path):
    """Return allocated bytes for cache eviction, falling back to logical size."""
    return _cache_size_from_stat(os.stat(path))


def _cache_size_from_stat(stat):
    """Return allocated bytes from a stat result, falling back to logical size."""
    blocks = getattr(stat, "st_blocks", None)
    if isinstance(blocks, int) and blocks > 0:
        return blocks * 512
    return stat.st_size


def _scan_cache_entries(cache_dir):
    """Return (total_bytes, [(mtime, path, size)]) for cache JSON files."""
    total = 0
    entries = []
    for f in os.listdir(cache_dir):
        if not f.endswith(".json"):
            continue
        path = os.path.join(cache_dir, f)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        size = _cache_size_from_stat(stat)
        total += size
        entries.append((getattr(stat, "st_mtime", 0), path, size))
    return total, entries


def _over_limit(total, count):
    """True while either the byte or entry-count cap is still exceeded."""
    return total > MAX_CACHE_SIZE_BYTES or count > MAX_CACHE_ENTRY_COUNT


def _evict_entries(entries, total):
    """Remove oldest cache files until both limits are satisfied."""
    current_count = len(entries)
    while entries and _over_limit(total, current_count):
        _mtime, path, size = entries.pop(0)
        try:
            os.remove(path)
        except OSError:
            if not os.path.exists(path):
                total -= size
                current_count -= 1
            continue
        total -= size
        current_count -= 1
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Cache evicted '{}'".format(os.path.basename(path)),
            xbmc.LOGDEBUG,
        )


def _evict_oldest():
    """Delete oldest cache files until size and entry-count limits are met."""
    try:
        cache_dir = _get_cache_dir()
        total, entries = _scan_cache_entries(cache_dir)

        if not _over_limit(total, len(entries)):
            return
        # Sort by mtime ascending (oldest first)
        entries.sort(key=lambda entry: entry[0])
        _evict_entries(entries, total)
    except OSError:
        pass


def clear_cache():
    """Delete all cached results.

    Tolerate a missing cache directory—`clear_cache` is exposed via
    the addon's settings menu and a user can hit it on a fresh install
    where the directory was never created. The previous unguarded
    ``os.listdir`` raised FileNotFoundError that bubbled up to the
    settings handler. TODO.md §H.2-M42.
    """
    cache_dir = _get_cache_dir()
    try:
        entries = os.listdir(cache_dir)
    except FileNotFoundError:
        return
    except OSError:
        return
    for f in entries:
        if f.endswith(".json"):
            try:
                os.remove(os.path.join(cache_dir, f))
            except OSError:
                pass
