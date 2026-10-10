# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

import json
import os
import tempfile
import time
from collections import namedtuple
from unittest.mock import MagicMock, patch

import resources.lib.cache as cache_module
from resources.lib.cache import (
    _cache_key,
    _evict_oldest,
    _get_cache_dir,
    clear_cache,
    get_cached,
    set_cached,
)


def test_cache_key_basic():
    key = _cache_key("movie", "The Matrix", year="1999")
    assert "movie" in key
    assert "Matrix" in key or "matrix" in key


def test_cache_key_special_chars():
    key = _cache_key("movie", "Movie: The Sequel!")
    assert all(c.isalnum() or c in "-_" for c in key)


def test_cache_key_length_limited():
    long_title = "A" * 500
    key = _cache_key("movie", long_title)
    assert len(key) <= 200


def test_cache_key_distinguishes_titles_that_sanitize_the_same():
    assert _cache_key("movie", "Foo Bar") != _cache_key("movie", "Foo-Bar")
    assert _cache_key("movie", "Foo.Bar") != _cache_key("movie", "Foo/Bar")


def test_cache_key_distinguishes_tmdb_id_and_tvdb():
    """Two distinct shows sharing title/season/episode (TMDB-only, no imdb)
    must not collide, and a tvdb-keyed search must not be shadowed by a
    non-tvdb one (issue #318)."""
    base = dict(season="1", episode="1")
    # Distinct shows, same title+season+episode, differ only by tmdb_id.
    assert _cache_key("episode", "Echo", tmdb_id="111", **base) != _cache_key(
        "episode", "Echo", tmdb_id="222", **base
    )
    # Same show, with vs without a resolved tvdb -> distinct result sets.
    assert _cache_key("episode", "Echo", tmdb_id="111", **base) != _cache_key(
        "episode", "Echo", tmdb_id="111", tvdb="555", **base
    )


def test_cache_key_distinguishes_long_titles_with_same_prefix():
    prefix = "A" * 250
    assert _cache_key("movie", prefix + "one") != _cache_key("movie", prefix + "two")


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_set_and_get_cached(mock_addon_mod, mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        results = [{"title": "Test", "link": "http://test"}]
        set_cached("movie", "Test", results)
        cached = get_cached("movie", "Test")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0]["title"] == "Test"


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_cache_expired(mock_addon_mod, mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "1"  # 1 minute TTL
        mock_addon_mod.Addon.return_value = addon

        cache_path = os.path.join(tmpdir, _cache_key("movie", "Test") + ".json")
        with open(cache_path, "w") as f:
            json.dump(
                {"timestamp": time.time() - 61, "results": [{"title": "Test"}]}, f
            )
        assert get_cached("movie", "Test") is None


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_cache_disabled_when_ttl_zero(mock_addon_mod, mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "0"
        mock_addon_mod.Addon.return_value = addon

        set_cached("movie", "Test", [{"title": "Test"}])
        cached = get_cached("movie", "Test")
        assert cached is None


@patch("resources.lib.cache._get_cache_dir")
def test_clear_cache(mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        # Create some cache files
        for i in range(3):
            with open(os.path.join(tmpdir, "test_{}.json".format(i)), "w") as f:
                json.dump({"timestamp": time.time(), "results": []}, f)

        assert len(os.listdir(tmpdir)) == 3
        clear_cache()
        assert len(os.listdir(tmpdir)) == 0


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_cache_miss_returns_none(mock_addon_mod, mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        cached = get_cached("movie", "Nonexistent")
        assert cached is None


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_cache_read_handles_corrupted_json(mock_addon_mod, mock_cache_dir):
    """Reading a corrupted cache file should return None, not raise JSONDecodeError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        cache_path = os.path.join(tmpdir, "movie_Test.json")
        with open(cache_path, "w") as f:
            f.write("{not valid json]")

        cached = get_cached("movie", "Test")
        assert cached is None


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_handles_file_removed_after_exists(mock_addon_mod, mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        with patch.object(cache_module.os.path, "exists", return_value=True), patch(
            "builtins.open", side_effect=FileNotFoundError
        ):
            assert get_cached("movie", "Race") is None


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_deletes_entry_missing_timestamp(mock_addon_mod, mock_cache_dir):
    """A malformed cache entry with no timestamp is unusable and should
    be removed instead of left behind for every later read to re-check."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        cache_path = os.path.join(tmpdir, _cache_key("movie", "Bad") + ".json")
        with open(cache_path, "w") as f:
            json.dump({"results": [{"title": "Bad"}]}, f)

        assert get_cached("movie", "Bad") is None
        assert not os.path.exists(cache_path)


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_refreshes_mtime_on_hit(mock_addon_mod, mock_cache_dir):
    """Cache eviction is mtime-based, so a hit must refresh mtime for
    the cache to behave like LRU instead of oldest-written."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "300"
        mock_addon_mod.Addon.return_value = addon

        cache_path = os.path.join(tmpdir, _cache_key("movie", "Fresh") + ".json")
        with open(cache_path, "w") as f:
            json.dump({"timestamp": time.time(), "results": [{"title": "Fresh"}]}, f)
        os.utime(cache_path, (1000, 1000))

        assert get_cached("movie", "Fresh") == [{"title": "Fresh"}]
        assert os.path.getmtime(cache_path) > 1000


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_respects_current_lower_ttl_for_existing_entry(
    mock_addon_mod, mock_cache_dir
):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.return_value = "5"  # minutes
        mock_addon_mod.Addon.return_value = addon

        cache_path = os.path.join(tmpdir, _cache_key("movie", "Old") + ".json")
        with open(cache_path, "w") as f:
            json.dump(
                {"timestamp": time.time() - 600, "results": [{"title": "Old"}]},
                f,
            )

        assert get_cached("movie", "Old") is None
        assert not os.path.exists(cache_path)


@patch("resources.lib.cache._get_cache_dir")
def test_cache_evicts_oldest_when_over_limit(mock_cache_dir):
    """Cache should evict oldest files when total size exceeds limit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir

        # Create three cache files with identical content and distinct mtimes
        file_paths = []
        payload = {"timestamp": 0, "results": ["x" * 200]}
        for i in range(3):
            path = os.path.join(tmpdir, "test_{}.json".format(i))
            with open(path, "w") as f:
                json.dump(payload, f)
            file_paths.append(path)
            # Stagger mtimes so eviction order is deterministic
            os.utime(path, (1000 + i, 1000 + i))

        # Confirm all three files exist
        assert len([f for f in os.listdir(tmpdir) if f.endswith(".json")]) == 3

        # Set the limit to fit exactly one allocated file so the two
        # oldest get evicted.
        single_size = cache_module._cache_file_size(file_paths[0])
        limit = single_size + 1

        with patch.object(cache_module, "MAX_CACHE_SIZE_BYTES", limit):
            _evict_oldest()

        remaining = [f for f in os.listdir(tmpdir) if f.endswith(".json")]
        # At least the two oldest files should have been removed, leaving only newest
        assert len(remaining) < 3
        # The newest file (test_2.json, highest mtime) should still be present
        assert "test_2.json" in remaining


@patch("resources.lib.cache._get_cache_dir")
def test_cache_evicts_oldest_when_over_entry_limit(mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir

        for i in range(3):
            path = os.path.join(tmpdir, "test_{}.json".format(i))
            with open(path, "w") as f:
                json.dump({"timestamp": time.time(), "results": []}, f)
            os.utime(path, (1000 + i, 1000 + i))

        with patch.object(cache_module, "MAX_CACHE_ENTRY_COUNT", 2, create=True):
            _evict_oldest()

        remaining = sorted(f for f in os.listdir(tmpdir) if f.endswith(".json"))
        assert remaining == ["test_1.json", "test_2.json"]


@patch("resources.lib.cache._get_cache_dir")
def test_cache_evicts_using_allocated_block_size(mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        old_path = os.path.join(tmpdir, "old.json")
        new_path = os.path.join(tmpdir, "new.json")
        for path in (old_path, new_path):
            with open(path, "w") as f:
                json.dump({"timestamp": time.time(), "results": []}, f)

        FakeStat = namedtuple("FakeStat", "st_size st_blocks st_mtime")

        stats = {
            old_path: FakeStat(st_size=10, st_blocks=1000, st_mtime=1000),
            new_path: FakeStat(st_size=10, st_blocks=1, st_mtime=1001),
        }

        def _fake_stat(path):
            return stats[path]

        with patch.object(
            cache_module.os, "stat", side_effect=_fake_stat
        ), patch.object(cache_module, "MAX_CACHE_SIZE_BYTES", 4096), patch.object(
            cache_module, "MAX_CACHE_ENTRY_COUNT", 1000
        ):
            _evict_oldest()

        remaining = sorted(f for f in os.listdir(tmpdir) if f.endswith(".json"))
        assert remaining == ["new.json"]


@patch("resources.lib.cache._get_cache_dir")
def test_cache_eviction_continues_when_file_disappears_before_sort(mock_cache_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        old_path = os.path.join(tmpdir, "old.json")
        new_path = os.path.join(tmpdir, "new.json")
        for path, mtime in ((old_path, 1000), (new_path, 1001)):
            with open(path, "w") as f:
                json.dump({"timestamp": time.time(), "results": ["x" * 200]}, f)
            os.utime(path, (mtime, mtime))

        FakeStat = namedtuple("FakeStat", "st_size st_blocks st_mtime")
        stats = {
            old_path: FakeStat(st_size=10, st_blocks=8, st_mtime=1000),
            new_path: FakeStat(st_size=10, st_blocks=8, st_mtime=1001),
        }
        stat_calls = {old_path: 0, new_path: 0}
        real_remove = os.remove

        def _fake_stat(path):
            stat_calls[path] += 1
            if path == old_path and stat_calls[path] > 1:
                real_remove(old_path)
                raise FileNotFoundError(path)
            return stats[path]

        with patch.object(cache_module.os, "stat", side_effect=_fake_stat):
            with patch.object(cache_module, "MAX_CACHE_SIZE_BYTES", 1):
                _evict_oldest()

        remaining = sorted(f for f in os.listdir(tmpdir) if f.endswith(".json"))
        assert remaining == []


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_falls_back_to_default_when_ttl_setting_unparseable(
    mock_addon_mod, mock_cache_dir
):
    """When cache_ttl_minutes is a non-numeric string (user typo, corrupt
    settings file), get_cached must fall back to the 30-minute default
    rather than raising ValueError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        # ``int("")`` raises ValueError in the production path. The
        # test covers the except-branch fallback.
        addon.getSetting.return_value = "absolute nonsense"
        mock_addon_mod.Addon.return_value = addon

        # Write a fresh cache entry by hand to observe whether
        # the fallback TTL (30 min) treats it as live.
        fresh = {
            "timestamp": time.time() - 29 * 60,  # 29 minutes old
            "results": [{"title": "Fresh"}],
        }
        os.makedirs(tmpdir, exist_ok=True)
        key_path = os.path.join(tmpdir, _cache_key("movie", "Fresh") + ".json")
        with open(key_path, "w") as f:
            json.dump(fresh, f)

        cached = get_cached("movie", "Fresh")
        assert (
            cached is not None
        ), "Fallback TTL of 30 min must still accept a 29-minute-old entry"
        assert cached[0]["title"] == "Fresh"


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_get_cached_falls_back_to_default_when_ttl_setting_raises_runtime(
    mock_addon_mod, mock_cache_dir
):
    """Kodi can raise RuntimeError from getSetting during early /play routing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.side_effect = RuntimeError(
            'Unknown exception thrown from the call "getSetting"'
        )
        mock_addon_mod.Addon.return_value = addon

        fresh = {
            "timestamp": time.time() - 30,
            "results": [{"title": "Fresh"}],
        }
        os.makedirs(tmpdir, exist_ok=True)
        key_path = os.path.join(tmpdir, _cache_key("movie", "Fresh") + ".json")
        with open(key_path, "w") as f:
            json.dump(fresh, f)

        cached = get_cached("movie", "Fresh")
        assert cached is not None
        assert cached[0]["title"] == "Fresh"


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_set_cached_falls_back_to_default_when_ttl_setting_raises_runtime(
    mock_addon_mod, mock_cache_dir
):
    """A transient Kodi getSetting RuntimeError must not break cache writes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        addon = MagicMock()
        addon.getSetting.side_effect = RuntimeError(
            'Unknown exception thrown from the call "getSetting"'
        )
        mock_addon_mod.Addon.return_value = addon

        set_cached("movie", "Fresh", [{"title": "Fresh"}])

        cache_files = [name for name in os.listdir(tmpdir) if name.endswith(".json")]
        assert cache_files


@patch("resources.lib.cache._get_cache_dir")
def test_clear_cache_swallows_per_file_oserror(mock_cache_dir):
    """clear_cache() must not raise if one of the files can't be
    deleted (locked by Kodi, gone mid-loop, permissions). Other files
    should still be processed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        # Seed two cache files.
        for name in ("a.json", "b.json"):
            with open(os.path.join(tmpdir, name), "w") as f:
                f.write("{}")

        # Force the first os.remove call to raise; the second must
        # still land so b.json is deleted even though a.json "failed."
        real_remove = os.remove
        calls = {"n": 0}

        def _sometimes_fail(path):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("locked")
            real_remove(path)

        with patch.object(cache_module.os, "remove", side_effect=_sometimes_fail):
            clear_cache()  # must not raise

        remaining = sorted(os.listdir(tmpdir))
        # Exactly one of the two files survives the partial failure.
        assert len(remaining) == 1


@patch("resources.lib.cache.xbmcaddon")
def test_cache_ttl_setting_is_minutes_and_clamped(mock_addon_mod):
    from resources.lib.cache import _get_cache_ttl_seconds

    addon = MagicMock()
    mock_addon_mod.Addon.return_value = addon
    addon.getSetting.return_value = "2"
    assert _get_cache_ttl_seconds() == 120
    addon.getSetting.assert_called_with("cache_ttl_minutes")
    addon.getSetting.return_value = "99999"
    assert _get_cache_ttl_seconds() == 1440 * 60
    addon.getSetting.return_value = "-5"
    assert _get_cache_ttl_seconds() == 0
    addon.getSetting.return_value = ""
    assert _get_cache_ttl_seconds() == 30 * 60


@patch("resources.lib.cache._get_cache_dir")
@patch("resources.lib.cache.xbmcaddon")
def test_settings_getter_replaces_kodi_settings_api(mock_addon_mod, mock_cache_dir):
    """The RunScript player passes its pure-XML getter; Kodi's API stays unused."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_cache_dir.return_value = tmpdir
        mock_addon_mod.Addon.side_effect = AssertionError("Kodi settings API used")
        seen = []

        def getter(key, default=""):
            seen.append(key)
            return "10"

        set_cached("movie", "Test", [{"title": "Test"}], settings_getter=getter)
        assert get_cached("movie", "Test", settings_getter=getter) == [
            {"title": "Test"}
        ]
        assert set(seen) == {"cache_ttl_minutes"}


@patch("resources.lib.cache.xbmcvfs")
@patch("resources.lib.cache.xbmcaddon")
def test_cache_dir_resolves_profile_without_addon_info(mock_addon_mod, mock_vfs):
    """getAddonInfo("profile") can crash CoreELEC in the RunScript context."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_addon_mod.Addon.side_effect = AssertionError("add-on info used")
        mock_vfs.translatePath.return_value = tmpdir

        # The module-level import is the real function (conftest patches the
        # attribute per test).
        assert _get_cache_dir() == os.path.join(tmpdir, "cache")
        mock_vfs.translatePath.assert_called_once_with(
            "special://profile/addon_data/plugin.video.nzbdav/"
        )
