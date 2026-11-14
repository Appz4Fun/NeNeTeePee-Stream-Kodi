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


def count_uploads(rows):
    """The number of separate uploads among ``rows`` (at least 1 for any row).

    ``rows`` are listings of one release, best-ranked first. A row is a new
    upload unless it is the same listing as the first row of an upload
    already counted, the same way ``FleetDedup.clusters`` groups them. Only
    rows of equal size can be the same listing, so each is compared with
    the uploads of its own size; a row without a size or a readable date
    is its own upload.
    """
    from resources.lib.nzbget_fleet_dedup import same_listing

    by_size = {}
    count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        heads = by_size.setdefault(str(row.get("size") or "").strip(), [])
        if any(same_listing(row, head) for head in heads):
            continue
        heads.append(row)
        count += 1
    return count
