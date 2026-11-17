# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The last parts of the grouping work: a cached open skips the metadata pass,
a release's copies are counted once, and a backup that is another post under
the release's name is not sent."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import resources.lib.cache as cache_module
from resources.lib import filter as filter_module
from resources.lib import nfo_audio, nzbget_progress, release_index, results_groups
from resources.lib.nzbget_fleet_dedup import FleetDedup

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


@pytest.fixture(name="store", autouse=True)
def _store():
    """An NFO store of this test's own: nothing read from or written to disk."""
    with patch.object(nfo_audio, "_STORE", {}), patch.object(
        nfo_audio, "_LOADED", [True]
    ), patch.object(nfo_audio, "_CHANGES", [1]):
        yield nfo_audio._STORE


def _stored_and_read():
    rows = _rows()
    filter_module.attach_metadata(rows)
    cache_module.set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    return rows, cache_module.get_cached("movie", "Some Movie", settings_getter=_ttl)


def test_rows_read_back_skip_the_metadata_pass():
    rows, cached = _stored_and_read()

    assert all(row["_attached"] for row in cached)
    assert cached[0]["_attached"] != rows[0]["_attached"]
    with patch.object(filter_module, "_unify_name_forms") as unify:
        filter_module.attach_metadata(cached)
        filter_module.filter_results(cached, settings_getter=_ttl)
    unify.assert_not_called()
    assert [row["_meta"] for row in cached] == [row["_meta"] for row in rows]


def test_rows_read_back_redo_the_pass_once_an_answer_for_a_release_changed(store):
    rows = _rows()
    filter_module.attach_metadata(rows)
    cache_module.set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    key = filter_module._name_form_key(_TITLES[1])
    store[key] = {"audio": ["DD+", "Atmos"], "channels": "5.1", "at": 1}

    cached = cache_module.get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert not any(row.get("_attached") for row in cached)
    filter_module.attach_metadata(cached)
    assert "Atmos" in cached[1]["_meta"]["audio"]


def test_an_answer_for_a_release_not_in_the_list_changes_nothing(store):
    rows = _rows()
    filter_module.attach_metadata(rows)
    cache_module.set_cached("movie", "Some Movie", rows, settings_getter=_ttl)
    store["another movie 2024 1080p web dl flux"] = {"audio": [], "at": 1}

    cached = cache_module.get_cached("movie", "Some Movie", settings_getter=_ttl)

    assert all(row["_attached"] for row in cached)


def test_a_list_not_attached_whole_is_stored_without_the_record(cache_dir):
    rows = _rows()
    filter_module.attach_metadata(rows)
    rows.append({"title": "Some.Movie.2024.720p.WEB-DL-NEW", "link": "http://hydra/9"})
    cache_module.set_cached("movie", "Some Movie", rows, settings_getter=_ttl)

    (path,) = [p for p in cache_dir.iterdir() if p.suffix == ".json"]
    assert json.loads(path.read_text(encoding="utf-8"))["attached"] is None
    cached = cache_module.get_cached("movie", "Some Movie", settings_getter=_ttl)
    assert not any(row.get("_attached") for row in cached)


@pytest.mark.parametrize(
    "record",
    [None, "x", {"keys": "x", "nfo": "y"}, {"keys": [1], "nfo": "y"}, {"keys": []}],
)
def test_a_damaged_record_marks_nothing(record):
    rows = _rows()
    filter_module.attach_metadata(rows)
    for row in rows:
        row.pop("_attached")

    assert filter_module.resume_attached(rows, record) is False
    assert not any(row.get("_attached") for row in rows)


def test_the_stamp_follows_only_the_answers_for_the_named_releases(store):
    before = nfo_audio.stamp_for(["a", "b"])
    store["c"] = {"audio": [], "at": 1}
    assert nfo_audio.stamp_for(["b", "a", "a"]) == before
    store["a"] = {"audio": ["DTS"], "at": 2}
    assert nfo_audio.stamp_for(["a", "b"]) != before


def _copies_of(title, sizes):
    return [
        {
            "title": title,
            "link": "http://hydra/{}-{}".format(title, at),
            "size": str(size),
            "pubdate": "Mon, 0{} Jan 2024 10:00:00 +0000".format(at + 1),
        }
        for at, size in enumerate(sizes)
    ]


def test_a_release_is_counted_once_and_the_picker_shows_the_ranked_count():
    rows = _copies_of(
        "Some.Movie.2024.1080p.WEB-DL.DDP5.1.H.264-FLUX", [10**9 + n for n in range(4)]
    )
    filter_module.attach_metadata(rows)
    with patch(
        "resources.lib.release_index.count_copies", wraps=release_index.count_copies
    ) as count:
        filtered, _all = filter_module.filter_results(rows, settings_getter=_ttl)
        groups = results_groups.release_groups(filtered)
        shown = [release_index.copies_of(group) for group in groups]

    assert shown == [4]
    assert count.call_count == 1


def test_a_group_no_recent_grouping_holds_is_counted_there():
    rows = _copies_of("Other.2024.1080p.WEB-DL-GRP", [10**9, 10**9 + 1])
    assert release_index.copies_of(rows) == 2
    assert release_index.copies_of(rows[:1]) == 1
    assert release_index.copies_of([]) == 1


def _payload(total, files=None):
    return (total, files if files is not None else {("own", total): total}, frozenset())


def _fingerprint(payload):
    return SimpleNamespace(payload=payload)


def _fleet(pick_size, sizes):
    dedup = FleetDedup()
    dedup.note_listed_size({"size": str(pick_size), "_is_pick": True})
    for size in sizes:
        dedup.note_listed_size({"size": str(size)})
    dedup.note_pick(_fingerprint(_payload(pick_size, {("pick", 1): pick_size})))
    return dedup


def test_other_files_far_off_the_release_size_are_another_post():
    dedup = _fleet(6 * 10**9, [6 * 10**9 + 1, 30 * 10**9])
    stray = {"size": str(30 * 10**9)}

    assert dedup.stray_from_pick(_fingerprint(_payload(30 * 10**9)), stray)


def test_an_ordinary_copy_with_files_of_its_own_is_kept():
    dedup = _fleet(6 * 10**9, [int(6.3 * 10**9), int(5.9 * 10**9)])
    copy = {"size": str(int(6.3 * 10**9))}

    assert not dedup.stray_from_pick(_fingerprint(_payload(int(6.3 * 10**9))), copy)


def test_the_pick_files_inside_a_larger_post_are_not_a_stray():
    dedup = _fleet(6 * 10**9, [6 * 10**9 + 1, 30 * 10**9])
    holds = {("pick", 1): 6 * 10**9, ("more", 1): 24 * 10**9}

    assert not dedup.stray_from_pick(
        _fingerprint(_payload(30 * 10**9, holds)), {"size": str(30 * 10**9)}
    )


def test_nothing_is_decided_with_too_few_sizes_or_an_odd_pick():
    few = _fleet(6 * 10**9, [30 * 10**9])
    assert not few.stray_from_pick(
        _fingerprint(_payload(30 * 10**9)), {"size": str(30 * 10**9)}
    )
    # The pick is the odd one: the copies at the usual size stay.
    odd_pick = _fleet(10**9, [6 * 10**9, 6 * 10**9 + 1, 6 * 10**9 + 2])
    assert not odd_pick.stray_from_pick(
        _fingerprint(_payload(6 * 10**9)), {"size": str(6 * 10**9)}
    )
    unknown = _fleet(6 * 10**9, [6 * 10**9 + 1, 30 * 10**9])
    assert not unknown.stray_from_pick(_fingerprint(None), {"size": str(30 * 10**9)})


def test_strays_are_counted_and_named_in_the_summary():
    report = nzbget_progress.FleetReport()
    report.stray()
    report.stray()

    assert report.not_sent() == 2
    assert "2 posts that are not this release" in nzbget_progress.not_sent_line(report)
