# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The grouping and ranking of 23 real searches, held to what it is today.

A change to the release rule, the index or the parser shows here as a number
(``grouping_corpus.measure``). A refactor must leave every number alone; a
change meant to group better moves them, and the new values are written
here with the reason in the commit.
"""

import gzip
import random

import grouping_corpus
import pytest
from resources.lib import release_index
from resources.lib.upload_count import count_uploads

# What the 23 searches come to, in all. History:
#   8450 picker rows, 2882 of them a name with no group, 611 uploads split
#   across picker rows: the release rule alone.
#   6676 / 1108 / 119: names with no group listed together by name.
#   6503 / 981 / 9: listings of one payload take one parse (a name cut
#   short, or the same film or episode at a byte-equal size); 234 rows
#   gained a group that way.
#   6489 / 934 / 9, and 45 more rows shown: a tag run into the codec
#   ("HDR10x265", once read as season 10 episode 265 and dropped from a
#   film search), ATV as Apple TV+, and a trailing word the list knows as
#   a group (207 rows).
EXPECTED = {
    "rows": 41446,
    "shown": 39733,
    "picker_rows": 6489,
    "groupless_rows": 2531,
    "groupless_picker_rows": 934,
    "uploads_listed_twice": 7713,
    "uploads_split": 9,
    "multi_copy_rows": 4329,
}


@pytest.fixture(scope="module", name="opened")
def _opened():
    """Every search of the corpus, run once as a picker open."""
    return [grouping_corpus.open_picker(search) for search in grouping_corpus.load()]


def test_the_fixture_holds_no_link_key_or_indexer_name():
    with gzip.open(grouping_corpus._FIXTURE, "rb") as handle:
        raw = handle.read().lower()
    for secret in (b"apikey", b"getnzb", b"t=get", b"192.168."):
        assert secret not in raw
    searches = grouping_corpus.load()
    assert len(searches) == 23
    for search in searches:
        for row in search.rows:
            assert row["indexer"].startswith("Indexer ")
            assert row["link"].startswith("https://indexer.invalid/nzb/")
            assert not row.get("poster") or row["poster"][0] in "np"


def test_the_numbers_a_change_is_judged_by(opened):
    measures = [grouping_corpus.measure(one) for one in opened]

    assert grouping_corpus.total(measures) == EXPECTED


def test_the_index_groups_exactly_as_the_rule_does_pair_by_pair(opened):
    # The gate for any faster grouping: the same picker rows as applying
    # ``same_release`` to every row against every member of every group.
    for one in opened:
        plain = grouping_corpus.reference_groups(one.filtered)

        assert grouping_corpus.partition(one.groups) == grouping_corpus.partition(
            plain
        ), one.search.name


def test_every_row_is_in_one_group_and_groups_keep_the_ranked_order(opened):
    for one in opened:
        index = release_index.build(one.filtered)
        seen = [row for group in index.groups for row in group]
        assert sorted(map(id, seen)) == sorted(map(id, one.filtered))
        position = {id(row): at for at, row in enumerate(one.filtered)}
        firsts = [position[id(group[0])] for group in index.groups]
        assert firsts == sorted(firsts)
        for group in index.groups:
            inside = [position[id(row)] for row in group]
            assert inside == sorted(inside)
            assert all(index.group_of(row) is group for row in group)


def test_upload_counts_match_comparing_with_every_counted_upload(opened):
    shuffler = random.Random(20261116)
    checked = 0
    for one in opened:
        for group in one.groups:
            if len(group) < 2:
                continue
            checked += 1
            assert count_uploads(group) == grouping_corpus.reference_upload_count(group)
            mixed = list(group)
            shuffler.shuffle(mixed)
            assert count_uploads(mixed) == grouping_corpus.reference_upload_count(mixed)
    assert checked > 4000


def test_every_listing_of_a_release_is_ranked_by_one_count(opened):
    from resources.lib.filter import _nzb_copy_counts

    for one in opened:
        ranked_by = _nzb_copy_counts(one.filtered)
        for group in one.groups:
            assert len({ranked_by[id(row)] for row in group}) == 1
