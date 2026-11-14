# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Separate uploads among a release's listings, for the picker's copy count."""

from resources.lib.upload_count import count_uploads

_GB = 10**9


def _listing(indexer, size, listed, posted=None, **extra):
    row = {"indexer": indexer, "size": str(size), "pubdate": listed}
    if posted:
        row["usenetdate"] = posted
    row.update(extra)
    return row


# One remux as eleven indexers listed it (sizes and dates as they were sent).
REMUX = [
    _listing("A", 84201110874, "Sat, 18 Apr 2026 11:19:45 +0000"),
    _listing(
        "B",
        84201110874,
        "Sat, 18 Apr 2026 11:19:45 +0000",
        "Sun, 12 Apr 2026 14:10:47 +0200",
    ),
    _listing(
        "C",
        81087484970,
        "Sun, 12 Apr 2026 05:36:54 +0000",
        "Sun, 12 Apr 2026 03:36:53 +0000",
    ),
    _listing(
        "D",
        81092854338,
        "Sat, 11 Apr 2026 19:42:38 +0000",
        "Sat, 11 Apr 2026 23:42:37 +0000",
    ),
    _listing(
        "E",
        81092854338,
        "Sat, 11 Apr 2026 22:48:46 +0000",
        "Sat, 11 Apr 2026 22:48:46 +0000",
    ),
    _listing("A", 81779963000, "Sat, 11 Apr 2026 21:03:48 +0000"),
    _listing(
        "F",
        81779963000,
        "Sat, 11 Apr 2026 21:01:04 +0000",
        "Sat, 11 Apr 2026 21:01:04 +0000",
    ),
    # The same instant, written in two time zones.
    _listing(
        "G",
        80181162016,
        "Tue, 13 Jan 2026 08:53:17 +0000",
        "Tue, 13 Jan 2026 09:52:26 +0100",
    ),
    _listing(
        "H",
        80181162016,
        "Tue, 13 Jan 2026 14:01:09 +0000",
        "Tue, 13 Jan 2026 03:52:26 -0500",
    ),
    _listing(
        "I",
        81339958427,
        "Tue, 13 Jan 2026 06:46:46 +0000",
        "Tue, 13 Jan 2026 06:46:46 +0000",
    ),
    _listing("A", 81339958427, "Tue, 13 Jan 2026 06:46:46 +0000"),
]


def test_eleven_listings_of_one_remux_are_eight_uploads():
    assert count_uploads(REMUX) == 8
    # The order they are listed in does not change the count.
    assert count_uploads(list(reversed(REMUX))) == 8


def test_listings_that_cannot_be_compared_are_each_an_upload():
    undated = [_listing("A", 5 * _GB, ""), _listing("B", 5 * _GB, "")]
    assert count_uploads(undated) == 2
    unsized = [
        {"indexer": "A", "pubdate": "Tue, 13 Jan 2026 06:46:46 +0000"},
        {"indexer": "B", "pubdate": "Tue, 13 Jan 2026 06:46:46 +0000"},
    ]
    assert count_uploads(unsized) == 2


def test_one_upload_on_three_indexers_is_one():
    when = "Tue, 13 Jan 2026 06:46:46 +0000"
    rows = [_listing(name, 5 * _GB, when) for name in ("A", "B", "C")]
    assert count_uploads(rows) == 1
    # Another poster, or a password on one of them, is another upload.
    rows[0]["poster"] = rows[1]["poster"] = "one@example.com"
    rows[2]["poster"] = "two@example.com"
    assert count_uploads(rows) == 2
    assert count_uploads(rows[:1] + [dict(rows[0], password="2")]) == 1
    told_apart = [dict(rows[0], password="0"), dict(rows[0], password="2")]
    assert count_uploads(told_apart) == 2


def test_nothing_and_junk_count_nothing():
    assert count_uploads([]) == 0
    assert count_uploads([None, "row", 5]) == 0
    assert count_uploads(iter(REMUX[:2])) == 1
