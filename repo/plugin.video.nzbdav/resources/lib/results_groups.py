# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Group picker rows into releases for the List compacted view.

A group is every NZB of one release: the rows the copy-count tiebreak and the
NZBGet backups already treat as the same release (``filter._is_release_copy``).
Grouping rule changes belong there, so the picker follows them unchanged.
"""

from resources.lib.filter import _is_release_copy, _normalized_release_name


def release_groups(rows):
    """Return ``rows`` grouped by release, as a list of lists.

    Each group keeps its rows in the input (ranked) order, and groups are
    ordered by their first row, so the top-ranked copy of each release leads
    its group and the groups read in the same order as the plain list. Rows
    with the same normalized name always share a group; a new name joins the
    first existing group whose lead row it is a copy of. Season-pack rows
    (the synthetic local pack included) are never grouped with anything.
    """
    groups = []
    by_name = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("_season_pack"):
            groups.append([row])
            continue
        name = _normalized_release_name(row.get("title"))
        group = by_name.get(name) if name else None
        if group is None:
            group = _matching_group(groups, row)
            if group is None:
                group = [row]
                groups.append(group)
            else:
                group.append(row)
            if name:
                by_name[name] = group
        else:
            group.append(row)
    return groups


def _matching_group(groups, row):
    """The first group whose lead row ``row`` is another copy of, or None."""
    for group in groups:
        lead = group[0]
        if not isinstance(lead, dict) or lead.get("_season_pack"):
            continue
        if _is_release_copy(lead, row):
            return group
    return None
