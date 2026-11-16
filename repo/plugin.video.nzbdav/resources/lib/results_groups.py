# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Group picker rows into releases for the List compacted view.

A group is every NZB of one release: the rows the copy-count tiebreak and the
NZBGet backups also treat as the same release (``release_index``). Grouping
rule changes belong in ``release_match.same_release``, so the picker follows
them unchanged.
"""

from resources.lib import release_index


def release_groups(rows):
    """Return ``rows`` grouped by release, as a list of lists.

    Each group keeps its rows in the input (ranked) order, and groups are
    ordered by their first row, so the top-ranked copy of each release leads
    its group and the groups read in the same order as the plain list. The
    grouping is ``release_index.build``: a row joins the first group whose
    every member the release rule accepts it against, so the rule alone
    decides; rows with no release group are listed together when they carry
    the same name, and a row that cannot be parsed stays on its own.

    Season-pack rows (the synthetic local pack included) are never grouped
    with anything here: the index groups them among themselves for the copy
    count, and the picker lists each on its own row.
    """
    index = release_index.build(rows)
    groups = []
    listed = set()
    for row in rows:
        if isinstance(row, dict) and row.get("_season_pack"):
            groups.append([row])
            continue
        group = index.group_of(row)
        if id(group) not in listed:
            listed.add(id(group))
            groups.append(group)
    return groups


def group_pick(group):
    """The copy to play for a group: the first already-downloaded one, if any.

    Otherwise the lead, top-ranked copy. A DL copy carries the completed-job
    match the play path reuses, so picking it plays without a new download.
    """
    for row in group:
        if isinstance(row, dict) and row.get("_available"):
            return row
    return group[0]
