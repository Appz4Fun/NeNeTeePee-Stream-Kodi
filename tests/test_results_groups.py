# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""List compacted: picker rows grouped by release."""

from unittest.mock import patch

from resources.lib.filter import parse_title_metadata
from resources.lib.results_groups import group_pick, release_groups

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
    assert not release_groups([])


def test_release_rule_alone_decides_even_for_same_name_rows():
    # The shared rule can keep same-name rows apart (no parsed group, say);
    # the picker must not group them on the name alone.
    rows = [_row(ALPHA, "a1"), _row(ALPHA, "a2")]
    with patch("resources.lib.results_groups._is_release_copy", return_value=False):
        groups = release_groups(rows)

    assert _links(groups) == [["a1"], ["a2"]]


def test_a_row_must_match_every_member_not_just_the_lead():
    # same_release is not transitive: a yearless episode matches both
    # reboots, which reject each other, so they must not share its group.
    yearless = "Show.S01E01.1080p.WEB-DL.H264-GRP"
    old = "Show.2005.S01E01.1080p.WEB-DL.H264-GRP"
    new = "Show.2023.S01E01.1080p.WEB-DL.H264-GRP"

    def fake_copy(lead, row):
        return {lead["title"], row["title"]} != {old, new}

    rows = [_row(yearless, "y"), _row(old, "o"), _row(new, "n")]
    with patch("resources.lib.results_groups._is_release_copy", side_effect=fake_copy):
        groups = release_groups(rows)

    assert _links(groups) == [["y", "o"], ["n"]]


def test_identical_titles_are_compared_once():
    calls = []

    def fake_copy(lead, row):
        calls.append((lead["link"], row["link"]))
        return True

    rows = [_row(ALPHA, "a{}".format(i)) for i in range(5)]
    with patch("resources.lib.results_groups._is_release_copy", side_effect=fake_copy):
        groups = release_groups(rows)

    assert _links(groups) == [["a0", "a1", "a2", "a3", "a4"]]
    assert len(calls) == 1


def test_rows_with_different_profiles_are_never_compared():
    rows = [
        _row("Movie.2024.1080p.WEB-DL.H264-GRP{}".format(i), str(i)) for i in range(300)
    ]
    with patch("resources.lib.results_groups._is_release_copy") as copy:
        groups = release_groups(rows)

    assert len(groups) == 300
    copy.assert_not_called()


def test_a_row_without_a_group_stays_alone():
    title = "Alpha.2024.1080p.WEB-DL.DDP5.1.H264"
    rows = [_row(title, "x1"), _row(title, "x2")]

    assert _links(release_groups(rows)) == [["x1"], ["x2"]]


def test_group_pick_prefers_an_already_downloaded_copy():
    lead = _row(ALPHA, "a1")
    done = _row(ALPHA, "a2", _available=True)

    assert group_pick([lead, done]) is done
    assert group_pick([lead, _row(ALPHA, "a3")]) is lead
