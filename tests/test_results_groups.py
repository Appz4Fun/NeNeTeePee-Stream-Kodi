# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""List compacted: picker rows grouped by release."""

from resources.lib.filter import parse_title_metadata
from resources.lib.results_groups import release_groups

ALPHA = "Alpha.2024.1080p.WEB-DL.DDP5.1.H264-GRPA"
BRAVO = "Bravo.2024.1080p.WEB-DL.DDP5.1.H264-GRPB"


def _row(title, link, **extra):
    row = {"title": title, "link": link, "size": "5000000000", "pubdate": ""}
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _links(groups):
    return [[row["link"] for row in group] for group in groups]


def test_same_name_rows_share_a_group_in_rank_order():
    rows = [_row(ALPHA, "a1"), _row(BRAVO, "b1"), _row(ALPHA, "a2")]

    assert _links(release_groups(rows)) == [["a1", "a2"], ["b1"]]


def test_name_match_ignores_case_and_whitespace():
    rows = [_row(ALPHA, "a1"), _row(" " + ALPHA.lower() + " ", "a2")]

    assert _links(release_groups(rows)) == [["a1", "a2"]]


def test_renamed_copy_joins_its_release_through_same_release():
    renamed = ALPHA.replace(".", " ")
    rows = [_row(ALPHA, "a1"), _row(renamed, "a2")]

    assert _links(release_groups(rows)) == [["a1", "a2"]]


def test_different_resolution_is_a_different_release():
    rows = [_row(ALPHA, "a1"), _row(ALPHA.replace("1080p", "720p"), "a2")]

    assert _links(release_groups(rows)) == [["a1"], ["a2"]]


def test_groups_follow_the_lead_row_order():
    rows = [_row(BRAVO, "b1"), _row(ALPHA, "a1"), _row(BRAVO, "b2")]

    assert _links(release_groups(rows)) == [["b1", "b2"], ["a1"]]


def test_season_pack_rows_never_group():
    pack = {"title": ALPHA, "link": "", "_meta": {}, "_season_pack": {"job_id": "1"}}
    rows = [pack, _row(ALPHA, "a1"), _row(ALPHA, "a2")]

    groups = release_groups(rows)

    assert groups[0] == [pack]
    assert _links(groups[1:]) == [["a1", "a2"]]


def test_empty_input_has_no_groups():
    assert release_groups([]) == []
