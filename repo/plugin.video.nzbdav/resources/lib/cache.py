# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Simple JSON-based search result cache.

An entry holds the provider's answer and the parsed details of each release
name in it (the rows' ``_meta``). Parsing is the slow part of opening the
picker on a CoreELEC box, so a repeat open reads the parses back instead of
redoing them. They are stored once per name, not once per row: indexers list
one name several times. They are trusted only while the add-on's code is
unchanged (``_parser_stamp``).
"""

import json
import os
import time

import xbmc
import xbmcaddon
import xbmcvfs

# Raised from 50 MB when entries began to carry the parsed details of their
# release names (about 40% more per entry), so the cache still holds as many
# searches.
MAX_CACHE_SIZE_BYTES = 73400320  # 70 MB
MAX_CACHE_ENTRY_COUNT = 1000
CACHE_TTL_SETTING = "cache_ttl_minutes"
DEFAULT_CACHE_TTL_MINUTES = 30
MAX_CACHE_TTL_MINUTES = 1440
_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"


# The rows' parsed details, as ``filter.attach_metadata`` stores them.
_PARSED_KEY = "_meta"
# The entry's field naming the code that produced its parses.
_PARSER_FIELD = "parser"
# The entry's field holding the parses: release name -> parsed details.
_PARSES_FIELD = "parsed"
# path -> the timestamp of the entry last read from it, for ``keep_parsed``.
_READ_TIMESTAMPS = {}
_STAMP = []


def _parser_stamp():
    """A fingerprint of the add-on's code, or None when it cannot be read.

    A parse stored by one build must not be reused by another: a fix to the
    name parser would otherwise wait out the cache window. The fingerprint is
    the name, size and modification time of ``addon.xml`` and every Python
    file under ``resources/lib``, so any install or update changes it.
    """
    if not _STAMP:
        _STAMP.append(_compute_parser_stamp())
    return _STAMP[0]


def _compute_parser_stamp():
    import hashlib

    lib = os.path.dirname(os.path.abspath(__file__))
    addon_xml = os.path.join(os.path.dirname(os.path.dirname(lib)), "addon.xml")
    seen = []
    try:
        paths = [addon_xml]
        for folder, _dirs, names in os.walk(lib):
            paths.extend(
                os.path.join(folder, name) for name in names if name.endswith(".py")
            )
        for path in paths:
            stat = os.stat(path)
            seen.append((os.path.relpath(path, lib), stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError):
        return None
    return hashlib.sha256(repr(sorted(seen)).encode("utf-8")).hexdigest()[:20]


def _drop_parses(rows):
    """Remove the stored parses from ``rows`` (another build made them)."""
    for row in rows:
        row.pop(_PARSED_KEY, None)


def _split_parses(rows):
    """Move the rows' parses into one ``{name: parse}`` table and return it.

    ``rows`` are the copies about to be written. Rows with the same name
    carry equal parses, so the table holds each once. A row whose parse
    differs from the one already stored for its name keeps it on the row.
    """
    parses = {}
    for row in rows:
        meta = row.get(_PARSED_KEY)
        title = row.get("title")
        if not isinstance(meta, dict) or not isinstance(title, str):
            continue
        if parses.setdefault(title, meta) == meta:
            del row[_PARSED_KEY]
    return parses


def _attach_parses(rows, parses):
    """Give each row the stored parse of its name, as an object of its own.

    Rows of one name must not share a parse: later stages replace fields on
    one row's. The first row of a name takes the stored object and every
    further row a copy made by the JSON reader, which is far quicker than
    copying in Python.
    """
    if not isinstance(parses, dict):
        return
    texts = {}
    for row in rows:
        title = row.get("title")
        if _PARSED_KEY in row or not isinstance(title, str):
            continue
        meta = parses.get(title)
        if not isinstance(meta, dict):
            continue
        text = texts.get(title)
        if text is None:
            texts[title] = json.dumps(meta)
            row[_PARSED_KEY] = meta
        else:
            row[_PARSED_KEY] = json.loads(text)


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


def _cache_key(  # pylint: disable=too-many-arguments
    search_type,
    title,
    year="",
    imdb="",
    season="",
    episode="",
    tvdb="",
    tmdb_id="",
    *,
    providers="",
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

    ``providers`` names the enabled provider set: searches made with
    different providers never share an entry.
    """
    import hashlib

    parts = [search_type, title, year, imdb, season, episode, tvdb, tmdb_id, providers]
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


def _well_formed(data):
    """Whether ``results`` (required) and any ``hydra_uploads`` are lists of objects."""
    results = data.get("results")
    uploads = data.get("hydra_uploads")
    return all(
        isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        for rows in (results, [] if uploads is None else uploads)
    )


def _read_fresh_cache(path, cache_ttl, title):
    """Return cached results if present and fresh, else None.

    Deletes the file when its timestamp is missing/stale or its JSON is
    corrupt. Reuses ``get_cached``'s log line on a hit.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    timestamp = data.get("timestamp") if isinstance(data, dict) else None
    if not isinstance(timestamp, (int, float)) or not _well_formed(data):
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
    stamp = _parser_stamp()
    if stamp is None or data.get(_PARSER_FIELD) != stamp:
        _drop_parses(results)
    else:
        _attach_parses(results, data.get(_PARSES_FIELD))
    _READ_TIMESTAMPS[path] = timestamp
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
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        # Corrupt JSON, bytes that aren't UTF-8, or fields of the wrong type
        # while restoring the rows: the entry is unusable, so it is a miss.
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Discarded unreadable search cache "
            "entry for '{}': {}".format(title, exc),
            xbmc.LOGWARNING,
        )
        _try_remove(path)
        return None
    except OSError:
        return None


def set_cached(search_type, title, results, settings_getter=None, **kwargs):
    """Cache search results.

    The parsed details of rows that carry them (``_meta``) are stored too.
    """
    cache_ttl = _get_cache_ttl_seconds(settings_getter)
    if cache_ttl <= 0:
        return

    path = _cache_path(search_type, title, kwargs)
    if path is None:
        return
    _write_entry(path, title, results, time.time())
    _evict_oldest()


def keep_parsed(search_type, title, results, settings_getter=None, **kwargs):
    """Store the parses of rows just read from the cache, if they were missing.

    ``results`` is what ``get_cached`` returned. An entry written before
    parses were stored, or by another build of the add-on, comes back without
    them; this parses the rows once and rewrites the entry so the next open is
    fast. The entry keeps its age: it still expires when it would have.
    """
    path = _cache_path(search_type, title, kwargs)
    timestamp = _READ_TIMESTAMPS.get(path)
    if timestamp is None or _all_parsed(results):
        return
    if _get_cache_ttl_seconds(settings_getter) <= 0:
        return
    from resources.lib.filter import attach_metadata

    attach_metadata(results)
    _write_entry(path, title, results, timestamp)


def _all_parsed(results):
    """Whether every row with a name already carries its parsed details."""
    return all(
        isinstance(row.get(_PARSED_KEY), dict)
        for row in results
        if isinstance(row, dict) and "title" in row
    )


def _write_entry(path, title, results, timestamp):
    """Write one cache entry, dated ``timestamp``; a failed write is ignored."""
    tmp_path = path + ".tmp"
    try:
        from resources.lib.hydra import split_search_uploads

        plain_results, uploads = split_search_uploads(results)
        data = {
            "timestamp": timestamp,
            _PARSER_FIELD: _parser_stamp(),
            _PARSES_FIELD: _split_parses(plain_results),
            "results": plain_results,
            "hydra_uploads": uploads,
        }
        # Atomic write: dump to a sibling temp file then os.replace onto the
        # final path. A concurrent get_cached() sees either the old file
        # or the new file, never a half-written JSON blob that would
        # JSONDecodeError.
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: Cached {} results for '{}'").format(
                len(results), title
            ),
            xbmc.LOGDEBUG,
        )
    except (OSError, TypeError, ValueError) as exc:
        if not isinstance(exc, OSError):
            # A row that cannot be written as JSON: the search goes on
            # uncached, and the log says why.
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Search cache entry for '{}' not "
                "written: {}".format(title, exc),
                xbmc.LOGWARNING,
            )
        # Clean up the temp file if the replace didn't happen.
        try:
            os.remove(tmp_path)
        except OSError:
            # Nothing was left behind, or it cannot be removed: either way
            # the next write replaces it.
            pass


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
