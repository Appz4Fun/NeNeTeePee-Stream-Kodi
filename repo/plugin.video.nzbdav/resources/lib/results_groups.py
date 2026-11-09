# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Group picker rows into releases for the List compacted view.

A group is every NZB of one release: the rows the copy-count tiebreak and the
NZBGet backups already treat as the same release (``filter._is_release_copy``).
Grouping rule changes belong there, so the picker follows them unchanged.
"""

from resources.lib.fallback_streams import _release_identity
from resources.lib.filter import _is_release_copy
from resources.lib.release_match import release_profile


def release_groups(rows):
    """Return ``rows`` grouped by release, as a list of lists.

    Each group keeps its rows in the input (ranked) order, and groups are
    ordered by their first row, so the top-ranked copy of each release leads
    its group and the groups read in the same order as the plain list. A row
    joins the first group whose every member ``_is_release_copy`` accepts it
    against, so the release rule alone decides. Every member, not just the
    lead: the rule is not transitive (a yearless episode matches both the
    2005 and 2023 shows, which reject each other), so matching one member
    does not mean matching the rest.

    ``same_release`` only accepts rows with equal ``release_profile`` tuples,
    a known group and (through the content gate) the same episode set, and
    the same season set when episodes are present. Each row's key is computed
    once and only groups with the same key are compared; a row with no group
    (or one that cannot be parsed) stays on its own. That keeps grouping near
    linear: comparing every row against every earlier group made a few
    hundred distinct releases, or episodes of one group, take seconds.
    Season-pack rows (the synthetic local pack included) are never grouped
    with anything.
    """
    groups = []
    by_profile = {}
    verdicts = {}
    for row in rows:
        key = _grouping_key(row)
        if key is None:
            groups.append([row])
            continue
        candidates = by_profile.setdefault(key, [])
        for group in candidates:
            if _matches_every_member(group, row, verdicts):
                group.append(row)
                break
        else:
            group = [row]
            candidates.append(group)
            groups.append(group)
    return groups


def _matches_every_member(group, row, verdicts):
    """Whether ``row`` is a copy of every member of ``group``.

    ``same_release`` reads only a row's title and the metadata parsed from
    it, so the verdict is the same for any two rows with these exact titles:
    each distinct title pair is checked once and remembered in ``verdicts``.
    """
    title = row.get("title")
    seen = set()
    for member in group:
        member_title = member.get("title")
        if member_title in seen:
            continue
        seen.add(member_title)
        key = (member_title, title)
        if key not in verdicts:
            verdicts[key] = bool(_is_release_copy(member, row))
        if not verdicts[key]:
            return False
    return True


def group_pick(group):
    """The copy to play for a group: the first already-downloaded one, if any.

    Otherwise the lead, top-ranked copy. A DL copy carries the completed-job
    match the play path reuses, so picking it plays without a new download.
    """
    for row in group:
        if isinstance(row, dict) and row.get("_available"):
            return row
    return group[0]


def _grouping_key(row):
    """Fields two copies must share, or None when ``row`` can never group.

    The release profile plus the content gate's exact-match parts: the
    episode set (presence and value must match), and the season set when the
    row names episodes (the phantom-season collapse only applies without
    episodes). The fuzzy title and year checks stay in ``same_release``.
    """
    if not isinstance(row, dict) or row.get("_season_pack"):
        return None
    try:
        profile = release_profile(row)
        if not dict(profile).get("group"):
            return None
        _title, _year, seasons, episodes, _part = _release_identity(row)
    except Exception:  # pylint: disable=broad-except
        return None
    return (profile, tuple(episodes), tuple(seasons) if episodes else None)
