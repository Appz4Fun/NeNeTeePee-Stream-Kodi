# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""How many separate uploads a release's listings are.

Several indexers list one upload, so a release listed 11 times may be 8
uploads. The listings are told apart before anything is downloaded, by the
rule the NZBGet backend uses to fetch one NZB per upload
(``nzbget_fleet_dedup.same_listing``): equal size and post times within two
minutes, unless the indexers name different posters or disagree on a
password. The picker shows and ranks by this count, since a second listing
of an upload is no second copy.

It is the count known before download. Two listings this rule keeps apart
can still turn out to be one posting once their NZBs are compared.
"""

from bisect import bisect_left


def count_copies(rows):
    """The separate uploads among the distinct NZBs of one release's ``rows``.

    What the picker shows as a release's copies and ranks it by: a link
    listed twice counts once, several indexers listing one upload count
    once (``count_uploads``), and a row without a link adds nothing. Never
    less than 1.
    """
    by_link = {}
    for row in rows:
        if isinstance(row, dict) and row.get("link"):
            by_link.setdefault(row["link"], row)
    if len(by_link) < 2:
        return 1
    return max(1, count_uploads(by_link.values()))


def count_uploads(rows):
    """The number of separate uploads among ``rows`` (at least 1 for any row).

    ``rows`` are listings of one release, best-ranked first. A row is a new
    upload unless it is the same listing as the first row of an upload
    already counted, the same way ``FleetDedup.clusters`` groups them. Only
    rows of equal size can be the same listing, so each is compared with
    the uploads of its own size; a row without a size or a readable date
    is its own upload.

    Two listings can only be the same when a date of one is within the
    window of a date of the other, so the uploads of a size are kept sorted
    by their dates and a row is compared with those inside its window, not
    with all of them. One name reposted 419 times at one size took 419 x 419
    comparisons before; the answer is the same.
    """
    from resources.lib import nzbget_fleet_dedup as dedup

    window = dedup.SAME_LISTING_WINDOW_SECONDS
    by_size = {}
    count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        times = _post_times(row, dedup)
        # Per size: the counted uploads' dates, sorted, and beside each date
        # the upload it belongs to.
        dates, heads = by_size.setdefault(str(row.get("size") or "").strip(), ([], []))
        if _listed_already(row, times, dates, heads, window, dedup.same_listing):
            continue
        count += 1
        for when in times:
            at = bisect_left(dates, when)
            dates.insert(at, when)
            heads.insert(at, row)
    return count


def _post_times(row, dedup):
    """The dates ``same_listing`` compares for ``row``: posted and listed."""
    if dedup._size_bytes(row) <= 0:
        # Without a size the row is the same listing as nothing.
        return ()
    times = {dedup._usenet_epoch(row), dedup._posted_epoch(row)}
    times.discard(None)
    return tuple(times)


def _listed_already(row, times, dates, heads, window, same_listing):
    """Whether ``row`` is the same listing as an upload already counted."""
    for when in times:
        at = bisect_left(dates, when - window)
        while at < len(dates) and dates[at] <= when + window:
            if same_listing(row, heads[at]):
                return True
            at += 1
    return False
