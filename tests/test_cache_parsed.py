# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The search cache keeps each release name's parsed details.

Parsing every name is the slow part of opening the picker on a CoreELEC box;
a repeat open of a cached search must not redo it.
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
import resources.lib.cache as cache_module
from resources.lib import filter as filter_module
from resources.lib.cache import get_cached, keep_parsed, set_cached

_TITLES = (
    "Some.Movie.2024.2160p.UHD.BluRay.REMUX.DV.HDR.TrueHD.7.1.Atmos-FraMeSToR",
    "Some.Movie.2024.2160p.WEB-DL.DDP5.1.Atmos.DV.H.265-FLUX",
    "Some.Movie.2024.1080p.BluRay.x264.DTS-HD.MA.5.1-GROUP",
)


def _rows():
    return [
        {"title": title, "link": "http://hydra/{}".format(n), "size": str(10**9 * n)}
        for n, title in enumerate(_TITLES, 1)
    ]


def _ttl(key, default=""):
    return "1440" if key == "cache_ttl_minutes" else default


@pytest.fixture(name="cache_dir", autouse=True)
def _cache_dir(tmp_path):
    cache_module._READ_TIMESTAMPS.clear()
    with patch("resources.lib.cache._get_cache_dir", return_value=str(tmp_path)):
        yield tmp_path


def _entry(cache_dir):
    (path,) = [p for p in cache_dir.iterdir() if p.suffix == ".json"]
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_rows_stored_with_their_parses_come_back_with_them():
    rows = _rows()
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    with patch.object(filter_module, "parse_title_metadata") as parse:
        cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
        filtered, all_rows = filter_module.filter_results(cached, settings_getter=_ttl)

    parse.assert_not_called()
    assert [row["_meta"] for row in cached] == [row["_meta"] for row in rows]
    assert len(all_rows) == 3
    assert filtered[0]["title"] == _TITLES[0]


def test_stored_parses_give_the_same_list_as_fresh_parses():
    fresh = _rows()
    expected = filter_module.filter_results(fresh, settings_getter=_ttl)

    rows = _rows()
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
    got = filter_module.filter_results(cached, settings_getter=_ttl)

    def view(rows_):
        return [(row["title"], row["_meta"], row["_filter_reject"]) for row in rows_]

    assert view(got[0]) == view(expected[0])
    assert view(got[1]) == view(expected[1])


def test_parses_from_another_build_are_not_reused(cache_dir):
    rows = _rows()
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    path, data = _entry(cache_dir)
    assert data["parser"] == cache_module._parser_stamp()
    assert len(data["parsed"]) == 3
    data["parser"] = "another-build"
    path.write_text(json.dumps(data), encoding="utf-8")

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert [row["title"] for row in cached] == list(_TITLES)
    assert not any("_meta" in row for row in cached)


def test_an_entry_written_before_parses_were_stored_reads_as_unparsed(cache_dir):
    # The older format: no "parser" field. A "_meta" in it is not trusted,
    # on a row or in a table.
    set_cached("movie", "Some Movie", _rows(), settings_getter=_ttl)
    path, data = _entry(cache_dir)
    del data["parser"]
    data["parsed"] = {_TITLES[0]: {"resolution": "480p"}}
    data["results"][1]["_meta"] = {"resolution": "480p"}
    path.write_text(json.dumps(data), encoding="utf-8")

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert not any("_meta" in row for row in cached)


def test_parses_are_not_reused_when_the_build_cannot_be_identified():
    rows = _rows()
    filter_module.attach_metadata(rows)
    with patch.object(cache_module, "_parser_stamp", return_value=None):
        set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
        cached = get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert not any("_meta" in row for row in cached)


def test_keep_parsed_stores_missing_parses_and_keeps_the_entry_age(cache_dir):
    set_cached("movie", "Some Movie", _rows(), settings_getter=_ttl)
    path, data = _entry(cache_dir)
    data["timestamp"] = time.time() - 3600
    path.write_text(json.dumps(data), encoding="utf-8")

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
    assert not any("_meta" in row for row in cached)
    keep_parsed("movie", "Some Movie", cached, settings_getter=_ttl)

    # The rows in hand are parsed, and so is the stored entry.
    assert all(isinstance(row["_meta"], dict) for row in cached)
    _path, stored = _entry(cache_dir)
    assert stored["timestamp"] == data["timestamp"]
    assert stored["parser"] == cache_module._parser_stamp()
    with patch.object(filter_module, "parse_title_metadata") as parse:
        again = get_cached("movie", "Some Movie", settings_getter=_ttl)
        filter_module.filter_results(again, settings_getter=_ttl)
    parse.assert_not_called()
    assert [row["_meta"] for row in again] == [row["_meta"] for row in cached]


def test_keep_parsed_does_not_extend_the_life_of_an_entry(cache_dir):
    set_cached("movie", "Some Movie", _rows(), settings_getter=_ttl)
    path, data = _entry(cache_dir)
    written = time.time() - 86000
    data["timestamp"] = written
    path.write_text(json.dumps(data), encoding="utf-8")

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
    keep_parsed("movie", "Some Movie", cached, settings_getter=_ttl)

    with patch("resources.lib.cache.time.time", return_value=written + 86401):
        assert get_cached("movie", "Some Movie", settings_getter=_ttl) is None


def test_keep_parsed_leaves_a_fully_parsed_entry_alone(cache_dir):
    rows = _rows()
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    path, _data = _entry(cache_dir)
    before = path.read_bytes()

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
    with patch.object(cache_module, "_write_entry") as write, patch.object(
        filter_module, "attach_metadata"
    ) as attach:
        keep_parsed("movie", "Some Movie", cached, settings_getter=_ttl)

    write.assert_not_called()
    attach.assert_not_called()
    assert path.read_bytes() == before


def test_keep_parsed_without_a_read_of_the_entry_writes_nothing(cache_dir):
    # Rows that did not come from this entry must never be stored under it.
    keep_parsed("movie", "Some Movie", _rows(), settings_getter=_ttl)

    assert not list(cache_dir.iterdir())


def test_a_failed_rewrite_leaves_the_entry_and_the_rows_usable(cache_dir):
    set_cached("movie", "Some Movie", _rows(), settings_getter=_ttl)
    path, _data = _entry(cache_dir)
    before = path.read_bytes()
    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)

    with patch("resources.lib.cache.os.replace", side_effect=OSError("read-only")):
        keep_parsed("movie", "Some Movie", cached, settings_getter=_ttl)

    assert all(isinstance(row["_meta"], dict) for row in cached)
    assert path.read_bytes() == before
    assert [p.name for p in cache_dir.iterdir()] == [path.name]


def test_uploads_snapshots_are_stored_without_a_second_copy_of_the_parses(cache_dir):
    from resources.lib.hydra import _cache_search_uploads

    rows = _rows()
    _cache_search_uploads(rows)
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    _path, data = _entry(cache_dir)
    assert sorted(data["parsed"]) == sorted(_TITLES)
    assert not any("_meta" in row for row in data["results"])
    assert not any("_meta" in row for row in data["hydra_uploads"])


def test_a_name_listed_several_times_is_stored_once_and_read_back_per_row(cache_dir):
    rows = _rows() + [dict(row, indexer="Second") for row in _rows()]
    filter_module.attach_metadata(rows)
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    # Storing does not strip the parses from the rows in hand.
    assert all(isinstance(row["_meta"], dict) for row in rows)
    _path, data = _entry(cache_dir)
    assert len(data["results"]) == 6 and len(data["parsed"]) == 3

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)
    assert [row["_meta"] for row in cached] == [row["_meta"] for row in rows]
    # Each row has a parse of its own, lists included: later stages replace
    # fields on one row's parse.
    first, twin = cached[0]["_meta"], cached[3]["_meta"]
    assert first == twin and first is not twin
    assert first["hdr"] is not twin["hdr"]
    assert first["ptt_identity"] is not twin["ptt_identity"]


def test_a_row_whose_parse_differs_from_its_names_keeps_it(cache_dir):
    rows = _rows() + [dict(_rows()[0], indexer="Second")]
    filter_module.attach_metadata(rows)
    rows[3]["_meta"] = dict(rows[3]["_meta"], container="mp4")
    set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    cached = get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert cached[0]["_meta"]["container"] == rows[0]["_meta"]["container"]
    assert cached[3]["_meta"]["container"] == "mp4"
    assert [row["_meta"] for row in cached] == [row["_meta"] for row in rows]


def _addon_tree(tmp_path):
    lib = tmp_path / "addon" / "resources" / "lib"
    (lib / "ptt").mkdir(parents=True)
    (tmp_path / "addon" / "addon.xml").write_text("<addon version='1'/>")
    (lib / "cache.py").write_text("# cache\n")
    (lib / "filter.py").write_text("# filter\n")
    (lib / "ptt" / "parse.py").write_text("# parse\n")
    return lib


def test_the_build_fingerprint_changes_with_any_code_file(tmp_path):
    lib = _addon_tree(tmp_path)
    with patch.object(cache_module, "__file__", str(lib / "cache.py")):
        first = cache_module._compute_parser_stamp()
        assert first and first == cache_module._compute_parser_stamp()

        (lib / "ptt" / "parse.py").write_text("# parse, fixed\n")
        second = cache_module._compute_parser_stamp()
        assert second != first

        (tmp_path / "addon" / "addon.xml").write_text("<addon version='2'/>")
        third = cache_module._compute_parser_stamp()
        assert third not in (first, second)

        (lib / "new_module.py").write_text("")
        assert cache_module._compute_parser_stamp() not in (first, second, third)

        # A file that is not code does not count.
        before = cache_module._compute_parser_stamp()
        (lib / "notes.txt").write_text("x")
        assert cache_module._compute_parser_stamp() == before


def test_the_build_fingerprint_is_none_when_the_code_cannot_be_read(tmp_path):
    lib = _addon_tree(tmp_path)
    os.remove(str(tmp_path / "addon" / "addon.xml"))
    with patch.object(cache_module, "__file__", str(lib / "cache.py")):
        assert cache_module._compute_parser_stamp() is None


@patch("resources.lib.router._search_all_providers")
def test_a_tmdbhelper_play_parses_once_and_the_next_open_reads_it_back(mock_search):
    from resources.lib.router_scriptplay import _script_play_search_results

    mock_search.return_value = (_rows(), None)
    kwargs = dict(year="2024", imdb="tt1", season="", episode="", tvdb="", tmdb_id="")
    parse = MagicMock(side_effect=filter_module.parse_title_metadata)
    with patch(
        "resources.lib.router._get_script_setting", side_effect=_ttl
    ), patch.object(filter_module, "parse_title_metadata", parse):
        first = _script_play_search_results("movie", "Some Movie", kwargs, MagicMock())
        assert parse.call_count == 3
        assert all(isinstance(row["_meta"], dict) for row in first)

        second = _script_play_search_results("movie", "Some Movie", kwargs, MagicMock())
        filter_module.filter_results(second, settings_getter=_ttl)

    assert mock_search.call_count == 1
    assert parse.call_count == 3
    assert [row["_meta"] for row in second] == [row["_meta"] for row in first]


@patch("resources.lib.router_play._play_search_getter")
@patch("resources.lib.router._search_all_providers")
def test_a_plugin_route_search_parses_once_and_reads_it_back(mock_search, mock_getter):
    from resources.lib.router_play import _search_with_cache

    mock_getter.return_value = _ttl
    mock_search.return_value = (_rows(), None)
    kwargs = dict(year="2024", imdb="tt1", season="", episode="", tvdb="", tmdb_id="")
    parse = MagicMock(side_effect=filter_module.parse_title_metadata)
    with patch(
        "resources.lib.cache._get_cache_ttl_seconds", return_value=1800
    ), patch.object(filter_module, "parse_title_metadata", parse):
        first, error = _search_with_cache("movie", "Some Movie", kwargs)
        assert error is None and parse.call_count == 3

        second, _error = _search_with_cache("movie", "Some Movie", kwargs)
        filter_module.filter_results(second, settings_getter=_ttl)

    assert mock_search.call_count == 1
    assert parse.call_count == 3
    assert [row["_meta"] for row in second] == [row["_meta"] for row in first]


@patch("resources.lib.router._search_all_providers")
def test_an_entry_from_another_build_is_parsed_once_then_stored(mock_search, cache_dir):
    from resources.lib.router_scriptplay import _script_play_search_results

    mock_search.return_value = (_rows(), None)
    kwargs = dict(year="2024", imdb="tt1", season="", episode="", tvdb="", tmdb_id="")
    parse = MagicMock(side_effect=filter_module.parse_title_metadata)
    with patch(
        "resources.lib.router._get_script_setting", side_effect=_ttl
    ), patch.object(filter_module, "parse_title_metadata", parse):
        _script_play_search_results("movie", "Some Movie", kwargs, MagicMock())
        path, data = _entry(cache_dir)
        data["parser"] = "another-build"
        path.write_text(json.dumps(data), encoding="utf-8")

        # The first open after the update parses again and stores the result.
        _script_play_search_results("movie", "Some Movie", kwargs, MagicMock())
        assert parse.call_count == 6
        # Every later open reads it back.
        _script_play_search_results("movie", "Some Movie", kwargs, MagicMock())
        assert parse.call_count == 6

    assert mock_search.call_count == 1


def test_the_list_is_sorted_once_when_no_row_is_rejected():
    rows = _rows()
    with patch.object(
        filter_module, "_sort_results", side_effect=filter_module._sort_results
    ) as sort:
        filtered, all_rows = filter_module.filter_results(rows, settings_getter=_ttl)

    assert sort.call_count == 1
    assert filtered == all_rows
    assert filtered is not all_rows


def test_both_views_are_sorted_when_a_row_is_rejected():
    def getter(key, default=""):
        return "FLUX" if key == "filter_exclude_release_group" else _ttl(key, default)

    rows = _rows()
    with patch.object(
        filter_module, "_sort_results", side_effect=filter_module._sort_results
    ) as sort:
        filtered, all_rows = filter_module.filter_results(rows, settings_getter=getter)

    assert sort.call_count == 2
    assert len(filtered) == 2 and len(all_rows) == 3


def test_rows_that_cannot_be_written_leave_no_entry_and_do_not_raise(cache_dir):
    rows = _rows()
    rows[0]["unwritable"] = {1, 2}

    with patch("resources.lib.cache.xbmc.log") as log:
        set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    assert not list(cache_dir.iterdir())
    assert any("not written" in call.args[0] for call in log.call_args_list)
    assert get_cached("movie", "Some Movie", settings_getter=_ttl) is None
