# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Relevance: among top-tier ties, more NZB copies of the release first."""

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


def test_group_with_most_copies_leads_the_top_tier():
    # Three groups tie on 4K, Dolby Vision, and Atmos. Copies outrank size:
    # FLUX (3 NZBs) before playWEB (2) before Kaizen (1, but the largest).
    def title(group):
        return "Movie.2024.2160p.WEB-DL.DV.DDP5.1.Atmos.H265-" + group

    rows = [
        _row(title("Kaizen"), "k1", size="9000000000"),
        _row(title("playWEB"), "p1", size="7000000000"),
        _row(title("FLUX"), "f1", size="6000000000"),
        _row(title("playWEB"), "p2", size="7100000000"),
        _row(title("FLUX"), "f2", size="6100000000"),
        _row(title("FLUX"), "f3", size="6200000000"),
    ]

    assert _order(rows) == ["f3", "f2", "f1", "p2", "p1", "k1"]


def test_size_breaks_ties_between_equal_copy_counts():
    rows = [
        _row(ALPHA, "a1", size="4000000000"),
        _row(BRAVO, "b1", size="6000000000"),
    ]

    assert _order(rows) == ["b1", "a1"]


def test_copies_beat_size_within_the_tier():
    rows = [
        _row(ALPHA, "a1", size="6000000000"),
        _row(BRAVO, "b1"),
        _row(BRAVO, "b2", size="4000000000"),
    ]

    assert _order(rows) == ["b1", "b2", "a1"]


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


def test_other_groups_web_mirror_counts_for_its_own_group():
    # A same-size WEB-DL from another group is the same file to the fallback
    # gates, but its NZB belongs to that group's tally.
    rows = [
        _row(BRAVO, "b1"),
        _row("Bravo.2024.1080p.WEB-DL.DDP5.1.H264-OTHER", "o1"),
    ]

    assert _nzb_copy_count(rows[0], rows) == 1


def test_release_check_runs_per_distinct_title_not_per_pair():
    # 100 equal-size listings: ten groups, each listed ten times under two
    # titles. Identical titles share one check, so the release rule runs per
    # distinct title and cluster (20 titles, 10 clusters), far below the
    # 4950 pairs a row-by-row scan would compare.
    from unittest.mock import patch

    from resources.lib import release_match

    rows = []
    for group in range(10):
        for copy in range(10):
            sep = "." if copy % 2 else " "
            parts = ["Movie", "2024", "2160p", "WEB-DL", "DV", "DDP5.1", "Atmos"]
            title = sep.join(parts + ["H265-GRP" + "ABCDEFGHIJ"[group]])
            rows.append(_row(title, "g{}-{}".format(group, copy)))

    real = release_match.same_release
    with patch.object(release_match, "same_release", side_effect=real) as spy:
        ordered = _sort_results(rows, _settings())

    assert spy.call_count <= 20 + 20 * 10
    assert len(ordered) == 100
    assert _nzb_copy_count(rows[0], rows) == 10


def test_groupless_identical_titles_do_not_pool():
    # The shared rule never groups a row without a parsed group, not even
    # with a listing of the identical name.
    title = "Some.Show.S01E01.1080p.WEB.H264"
    rows = [_row(title, "x1"), _row(title, "x2")]

    assert not rows[0]["_meta"]["group"]
    assert _nzb_copy_count(rows[0], rows) == 1


def test_dotted_and_parenthesized_listings_of_one_release_pool_their_copies():
    # Real Six Feet Under S02E01 listings: the same HONE release under its
    # dotted scene name and its parenthesized "(... - HONE) [REMASTERED]"
    # name. Once both parse the HONE group, all four NZBs count together.
    dotted = (
        "Six.Feet.Under.2001.S02E01.1080p.AMZN.WEB-DL.H265.SDR.DDP.5.1."
        "English-HONE.REMASTERED"
    )
    parenthesized = (
        "Six Feet Under (2001) S02E01 (1080p AMZN WEB-DL H265 SDR DDP 5.1 "
        "English - HONE) [REMASTERED]"
    )
    rows = [
        _row(dotted, "h1", size="4139961138"),
        _row(parenthesized, "h2", size="4139958706"),
        _row(dotted, "h3", size="4139958706"),
        _row(parenthesized, "h4", size="4139814085"),
    ]

    assert {row["_meta"]["group"] for row in rows} == {"HONE"}
    assert _nzb_copy_count(rows[0], rows) == 4
