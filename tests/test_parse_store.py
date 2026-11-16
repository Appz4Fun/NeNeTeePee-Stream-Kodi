# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Release names already parsed, kept from one search to the next."""

import json
import os
import time
from unittest.mock import patch

from resources.lib import cache, parse_store
from resources.lib import filter as filter_module

NAME = "Some.Movie.2004.1080p.WEB-DL.DD+5.1.H.264-GRP"


def _wait_for_file(path):
    for _ in range(200):
        if os.path.exists(path):
            return True
        time.sleep(0.01)
    return False


def _store_file():
    return parse_store._store_path()


def _attach(names):
    rows = [{"title": name, "link": str(n)} for n, name in enumerate(names)]
    filter_module.attach_metadata(rows)
    return rows


def test_a_name_parsed_once_is_not_parsed_again_in_a_later_run():
    rows = _attach([NAME])
    assert _wait_for_file(_store_file())

    parse_store.forget()  # a new run: only the file is left
    with patch.object(
        filter_module, "parse_title_metadata", side_effect=AssertionError
    ):
        again = _attach([NAME])

    assert again[0]["_meta"] == rows[0]["_meta"]


def test_each_row_gets_a_parse_of_its_own():
    first = parse_store.parsed(NAME, filter_module.parse_title_metadata)
    first["group"] = "CHANGED"
    second = parse_store.parsed(NAME, filter_module.parse_title_metadata)

    assert second["group"] == "GRP"


def test_a_file_from_another_build_is_ignored():
    _attach([NAME])
    path = _store_file()
    assert _wait_for_file(path)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    data["parser"] = "another build"
    data["names"][NAME][1]["group"] = "STALE"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)

    parse_store.forget()
    assert parse_store.parsed(NAME, filter_module.parse_title_metadata)["group"] == (
        "GRP"
    )


def test_an_unreadable_file_or_folder_only_means_parsing_as_before():
    path = _store_file()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    parse_store.forget()
    assert _attach([NAME])[0]["_meta"]["group"] == "GRP"

    parse_store.forget()
    with patch.object(parse_store, "_store_path", return_value=None):
        assert _attach([NAME + ".mkv"])[0]["_meta"]["group"] == "GRP"
        parse_store.save_soon()


def test_the_store_keeps_the_names_used_most_recently():
    parse = filter_module.parse_title_metadata
    with patch.object(parse_store, "MAX_NAMES", 2):
        for name in ("A.2001.1080p.WEB-GRP", "B.2002.1080p.WEB-GRP"):
            parse_store.parsed(name, parse)
        parse_store.parsed("A.2001.1080p.WEB-GRP", parse)  # used again
        parse_store.parsed("C.2003.1080p.WEB-GRP", parse)  # new, the same day
        with patch.object(parse_store, "_write") as write:
            parse_store.save_soon()
            for _ in range(100):
                if write.called:
                    break
                time.sleep(0.01)

    _path, (_stamp, entries), _number = write.call_args.args
    assert [name for name, _entry in entries] == [
        "A.2001.1080p.WEB-GRP",
        "C.2003.1080p.WEB-GRP",
    ]


def test_an_older_snapshot_never_replaces_a_newer_one(tmp_path):
    path = str(tmp_path / "parsed_names.json")
    first = parse_store._STATE["written"] + 1
    parse_store._write(path, ("stamp", [("new", [1, {}])]), first + 1)
    parse_store._write(path, ("stamp", [("old", [1, {}])]), first)

    with open(path, encoding="utf-8") as handle:
        assert list(json.load(handle)["names"]) == ["new"]
    assert sorted(os.listdir(tmp_path)) == ["parsed_names.json"]


def test_the_file_sits_in_the_profile_folder_not_in_the_search_cache():
    with patch.object(
        parse_store.xbmcvfs, "translatePath", return_value="/profile"
    ), patch.object(cache, "_get_cache_dir", return_value="/profile/cache"):
        assert parse_store._profile_store_path() == os.path.join(
            "/profile", "parsed_names.json"
        )
    with patch.object(
        parse_store.xbmcvfs, "translatePath", side_effect=RuntimeError("no profile")
    ):
        assert parse_store._profile_store_path() is None


def test_a_repeat_search_keeps_its_names_ahead_of_the_next_ones():
    parse = filter_module.parse_title_metadata
    with patch.object(parse_store, "MAX_NAMES", 2):
        for name in ("X.2001.1080p.WEB-GRP", "Y.2002.1080p.WEB-GRP"):
            parse_store.parsed(name, parse)
        parse_store._STATE["changed"] = False  # as if just written
        parse_store.parsed("X.2001.1080p.WEB-GRP", parse)  # a hit, same day
        assert parse_store._STATE["changed"]


def test_a_temporary_file_left_by_a_write_cut_short_is_removed(tmp_path):
    stale = tmp_path / ".parsed_names-old.tmp"
    stale.write_text("{")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    fresh = tmp_path / ".parsed_names-busy.tmp"
    fresh.write_text("{")

    parse_store._write(str(tmp_path / "parsed_names.json"), ("s", []), 10**9)

    assert sorted(os.listdir(tmp_path)) == [
        ".parsed_names-busy.tmp",
        "parsed_names.json",
    ]
