# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The metadata pass runs once per result list (``filter._attach_metadata``)."""

from unittest.mock import patch

import pytest
from resources.lib import filter as filter_module
from resources.lib import nfo_audio

NAMES = (
    "Some.Movie.2004.1080p.WEB-DL.DD+5.1.H.264-GRP",
    "Some Movie 2004 1080p WEB-DL DD+5 1 H 264-GRP",
    "Other.Movie.2004.1080p.BluRay.DTS.x264-OTH",
)


@pytest.fixture(autouse=True)
def _clean_store(tmp_path):
    nfo_audio.forget_all()
    with patch.object(nfo_audio, "_store_path", return_value=str(tmp_path / "n.json")):
        yield
    nfo_audio.forget_all()


def _rows(names=NAMES):
    return [
        {"title": name, "link": "l{}".format(number), "size": "5000000000"}
        for number, name in enumerate(names)
    ]


def _passes(rows_list):
    """How many of the calls did the whole pass."""
    with patch.object(
        filter_module, "share_atmos", wraps=filter_module.share_atmos
    ) as shared:
        for rows in rows_list:
            filter_module._attach_metadata(rows)
    return shared.call_count


def test_a_second_pass_over_the_same_rows_does_nothing():
    rows = _rows()

    assert _passes([rows, rows, list(rows), rows[:2], rows[1:]]) == 1
    assert all(isinstance(row["_meta"], dict) for row in rows)


def test_a_picker_open_makes_one_pass():
    rows = _rows()

    def getter(key, default=""):
        return {"max_results": "0"}.get(key, default)

    with patch.object(
        filter_module, "share_atmos", wraps=filter_module.share_atmos
    ) as shared:
        filter_module.attach_metadata(rows)
        kept = filter_module.drop_other_years(rows, "2004", "Some Movie")
        kept = filter_module.drop_tv_episodes(kept)
        filtered, _everything = filter_module.filter_results(kept, getter)

    assert shared.call_count == 1
    assert len(filtered) == 3


def test_a_new_row_or_two_lists_joined_need_the_pass_again():
    first, second = _rows(NAMES[:2]), _rows(NAMES[2:])

    assert _passes([first, second]) == 2
    # Spellings are unified and Atmos shared across the whole list.
    assert _passes([first + second]) == 1
    assert _passes([first + [{"title": NAMES[0], "link": "new"}]]) == 1
    assert _passes([[]]) == 1


def test_a_row_whose_parse_was_taken_away_is_parsed_again():
    rows = _rows()
    filter_module._attach_metadata(rows)
    del rows[0]["_meta"]

    assert _passes([rows]) == 1
    assert rows[0]["_meta"]["group"] == "GRP"


def test_what_an_nfo_says_later_reaches_rows_already_attached():
    rows = _rows()
    filter_module._attach_metadata(rows)
    assert "Atmos" not in rows[0]["_meta"]["audio"]

    nfo_audio._remember(
        filter_module._name_form_key(NAMES[0]), ["Atmos", "DD+"], "5.1", sure=True
    )
    filter_module._attach_metadata(rows)

    assert "Atmos" in rows[0]["_meta"]["audio"]
    # Both spellings of the name took it, and nothing is pending again.
    assert "Atmos" in rows[1]["_meta"]["audio"]
    assert _passes([rows]) == 0


def test_a_mark_from_another_run_is_not_trusted():
    rows = _rows()
    filter_module._attach_metadata(rows)
    for row in rows:
        row[filter_module.ATTACHED_KEY] = "0123456789ab-1"

    assert _passes([rows]) == 1


def test_the_search_cache_stores_rows_without_the_mark(tmp_path):
    import json

    from resources.lib import cache

    rows = _rows()
    filter_module._attach_metadata(rows)
    path = str(tmp_path / "entry.json")

    cache._write_entry(path, "Some Movie", rows, 1.0)

    with open(path, encoding="utf-8") as handle:
        stored = json.load(handle)
    assert len(stored["results"]) == 3
    assert not any(filter_module.ATTACHED_KEY in row for row in stored["results"])
    # The rows in hand keep theirs.
    assert all(filter_module.ATTACHED_KEY in row for row in rows)
