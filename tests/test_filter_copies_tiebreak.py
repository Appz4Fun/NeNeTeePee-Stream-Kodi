# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Relevance sort's final tiebreaker: more NZB copies of the release first."""

from resources.lib.filter import (
    _nzb_copy_count,
    _sort_results,
    parse_title_metadata,
)

ALPHA = "Alpha.2024.1080p.WEB-DL.DDP5.1.H264-GRPA"
BRAVO = "Bravo.2024.1080p.WEB-DL.DDP5.1.H264-GRPB"
SIZE = "5000000000"


def _row(title, link, size=SIZE, **extra):
    row = {"title": title, "link": link, "size": size, "pubdate": ""}
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _settings(sort_order=0):
    return {"sort_order": sort_order}


def _order(rows, sort_order=0):
    return [row["link"] for row in _sort_results(rows, _settings(sort_order))]


def test_tied_release_with_more_copies_sorts_first():
    rows = [
        _row(ALPHA, "a1"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2"),
    ]

    assert _order(rows) == ["b1", "b2", "a1"]


def test_copies_never_override_an_earlier_criterion():
    rows = [
        _row("Alpha.2024.2160p.WEB-DL.DDP5.1.H265-GRPA", "a1"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2"),
        _row(BRAVO, "b3"),
    ]

    assert _order(rows)[0] == "a1"


def test_size_still_beats_copies():
    rows = [
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2"),
        _row(ALPHA, "a1", size="6000000000"),
    ]

    assert _order(rows) == ["a1", "b1", "b2"]


def test_copies_count_across_the_whole_list_not_only_the_tie():
    # The second Bravo NZB is smaller, so it does not tie, but it is still an
    # available copy of Bravo and lifts Bravo above Alpha in their tie.
    rows = [
        _row(ALPHA, "a1"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2", size="4000000000"),
    ]

    assert _order(rows) == ["b1", "a1", "b2"]


def test_equal_copy_counts_keep_original_order():
    rows = [
        _row(ALPHA, "a1"),
        _row(BRAVO, "b1"),
        _row(ALPHA, "a2"),
        _row(BRAVO, "b2"),
    ]

    assert _order(rows) == ["a1", "b1", "a2", "b2"]


def test_one_link_listed_twice_counts_once():
    rows = [
        _row(ALPHA, "a1"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b1"),
    ]

    assert _nzb_copy_count(rows[1], rows) == 1
    assert _order(rows) == ["a1", "b1", "b1"]


def test_same_release_under_another_name_counts_as_a_copy():
    renamed = "Bravo 2024 1080p WEB-DL DDP5.1 H264-GRPB"
    rows = [_row(ALPHA, "a1"), _row(BRAVO, "b1"), _row(renamed, "b2")]

    assert _nzb_copy_count(rows[1], rows) == 2
    assert _order(rows) == ["b1", "b2", "a1"]


def test_other_group_is_not_a_copy():
    # BluRay encodes: the release grouping only lets another group's WEB-DL of
    # near-identical size through, as a mirror of the same file.
    rows = [
        _row("Bravo.2024.1080p.BluRay.DTS.x264-GRPB", "b1"),
        _row("Bravo.2024.1080p.BluRay.DTS.x264-OTHER", "o1"),
    ]

    assert _nzb_copy_count(rows[0], rows) == 1


def test_season_pack_is_not_a_copy_of_an_episode():
    episode = "Show.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP"
    rows = [
        _row(episode, "e1"),
        _row(episode, "p1", _season_pack=True),
    ]

    assert _nzb_copy_count(rows[0], rows) == 1


def test_rows_without_a_link_count_only_themselves():
    rows = [_row(BRAVO, ""), _row(BRAVO, "")]

    assert _nzb_copy_count(rows[0], rows) == 1


def test_non_relevance_sort_orders_ignore_copies():
    rows = [
        _row(ALPHA, "a1"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2"),
    ]

    assert _order(rows, sort_order=1) == ["a1", "b1", "b2"]
    assert _order(rows, sort_order=2) == ["a1", "b1", "b2"]
