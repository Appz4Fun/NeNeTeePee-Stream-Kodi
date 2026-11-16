# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""One grouping of a result list into releases, read by everything that asks.

Which NZBs are the same release is decided by one rule
(``release_match.same_release``), but it used to be applied by three pieces
of code, each with its own shortcut key: the copy counts behind the ranking,
the oddly-sized-copy check, and the picker's List compacted rows. They
disagreed at the edges (a release named with and without an episode title
was one picker row and two size buckets), and together they took most of a
picker open.

``build`` groups a list once. ``ReleaseIndex.groups`` is every row, grouped,
and ``group_of(row)`` is the group a row is in. The copy counts, the size
check, the picker rows and the NZBGet backups all read that.

How rows are grouped is what the picker has always done: a row joins the
first group whose every member the rule accepts it against, in the order
the rows are given. Every member, because the rule is not transitive: a
name without a year matches both a 2005 and a 2023 show, which reject each
other. Two things make that cheap without changing an answer:

* Rows can only be one release when they share an exact key: the parsed
  release profile, the title, and the episode. Each key is its own bucket,
  so rows are never compared across them.
* Inside a bucket the rule reads only a row's signature (what it is a
  release of, its container and its upscale flag). Rows with one signature
  always get one answer, so each signature is placed once and the rows
  follow it.

A name with no release group is the one case the rule itself does not
decide. ``same_release`` never accepts such a row, since nothing says whose
release it is. Here, rows with no group that carry the same name (in any
spelling, ``release_subject.name_form_key``) are one release: the same
unnamed post listed by several indexers, and its reposts. Only a named
container keeps them apart (a ``.mkv`` and a ``.mp4`` of one name are two
files). A name with no group still never joins a release that has one.
"""

from resources.lib import fallback_streams as _fs
from resources.lib import release_match
from resources.lib.release_subject import name_form_key

# First in the exact key of a row with no release group.
_NO_GROUP = "no group"


class ReleaseIndex:  # pylint: disable=too-few-public-methods
    """The rows of one list, grouped into releases.

    ``groups`` is a list of lists: each group keeps its rows in the order
    they were given, and the groups are ordered by their first row. A row
    that cannot be parsed is a group of one.
    """

    __slots__ = ("groups", "_group_at")

    def __init__(self, groups):
        self.groups = groups
        self._group_at = {id(row): group for group in groups for row in group}

    def group_of(self, row):
        """The group ``row`` is in: the same list object for every member.

        A row the index was not built from is its own group.
        """
        group = self._group_at.get(id(row))
        return group if group is not None else [row]


def build(rows):
    """Group ``rows`` into releases and return the ``ReleaseIndex``.

    Season packs (``_season_pack`` rows) are grouped among themselves and
    never with an episode, as the rule's callers always held.
    """
    groups = []
    buckets = {}
    by_name = {}
    for row in rows:
        keys = _keys_once_per_name(row, by_name)
        if keys is None:
            groups.append([row])
            continue
        exact, signature = keys
        bucket = buckets.get(exact)
        if bucket is None:
            bucket = buckets[exact] = _Bucket(exact[0] is not _NO_GROUP)
        group = bucket.group_for(signature, row)
        if not group:
            groups.append(group)
        group.append(row)
    return ReleaseIndex(groups)


class _Bucket:  # pylint: disable=too-few-public-methods
    """The groups among rows of one exact key (see ``_keys``).

    ``named`` rows are grouped by the release rule. Rows with no group
    share a name already (it is their key) and only a named container
    separates them.
    """

    __slots__ = ("_named", "_placed", "_groups")

    def __init__(self, named):
        self._named = named
        # signature -> [its group, its first row, whether two rows of it group]
        self._placed = {}
        # [(group, [(signature, a row of it), ...])], in the order they began
        self._groups = []

    def group_for(self, signature, row):
        """The group ``row`` goes to; an empty list is a group just begun."""
        placed = self._placed.get(signature)
        if placed is None:
            group = self._place(signature, row)
            self._placed[signature] = [group, row, None]
            return group
        if placed[2] is None:
            # The second row of a signature: the rule says once whether a
            # release name groups with itself (it does, unless the rule
            # cannot read it), and the rest follow.
            placed[2] = not self._named or bool(
                release_match.same_release(placed[1], row)
            )
        return placed[0] if placed[2] else []

    def _place(self, signature, row):
        for group, members in self._groups:
            if all(
                self._accepts(known, member, signature, row)
                for known, member in members
            ):
                members.append((signature, row))
                return group
        group = []
        self._groups.append((group, [(signature, row)]))
        return group

    def _accepts(self, known, member, signature, row):
        if not self._named:
            # The signature is the container: two named ones must be equal.
            return not (known and signature and known != signature)
        return _may_match(known, signature) and release_match.same_release(member, row)


def _may_match(left, right):
    """False when the rule is certain to keep two signatures apart.

    A cheap look before the rule itself, so a title with many years or parts
    under one profile (every film a group posted in a series) does not ask
    the rule about each pair. It only ever says False where the rule does:

    * two named parts that differ, or two named containers that differ;
    * a film against any other year, absent included
      (``release_match._movie_years_match``; a row with a season but no
      episode only joins a film of its own year);
    * otherwise two named years that differ.
    """
    (_title, year, seasons, episodes, part), container, _upscaled = left
    (_title2, year2, seasons2, episodes2, part2), container2, _upscaled2 = right
    if part and part2 and part != part2:
        return False
    if container and container2 and container != container2:
        return False
    if not (seasons or episodes) or not (seasons2 or episodes2):
        return year == year2
    return not (year and year2 and year != year2)


def _keys_once_per_name(row, by_name):
    """``_keys(row)``, worked out once for rows with one name and one parse.

    Most rows of a search repeat a name another indexer listed, with an equal
    parse, and so have equal keys. ``by_name`` remembers the first such row's.
    """
    if not isinstance(row, dict):
        return None
    meta = row.get("_meta")
    name = (row.get("title"), bool(row.get("_season_pack")))
    known = by_name.get(name) if isinstance(name[0], str) else None
    if known is not None and known[0] == meta:
        return known[1]
    keys = _keys(row)
    if isinstance(name[0], str) and isinstance(meta, dict):
        by_name[name] = (meta, keys)
    return keys


def _keys(row):
    """``(exact key, signature)`` for ``row``, or None when it never groups."""
    if not isinstance(row, dict):
        return None
    try:
        profile = release_match.release_profile(row)
        meta = _fs._result_meta(row)
        container = str(meta.get("container") or "").strip().casefold()
        if not release_match.profile_group(profile):
            name = name_form_key(row.get("title"))
            if not name:
                return None
            return (_NO_GROUP, bool(row.get("_season_pack")), name), container
        identity = _fs._release_identity(row)
        upscaled = _fs._meta_bool_from_meta(meta, "upscaled")
    except Exception:  # pylint: disable=broad-except
        return None
    title, _year, seasons, episodes, _part = identity
    # What the rule needs equal before anything else: the profile, the title
    # (``release_match._titles_differ``) and, through the content gate, the
    # episode set, with the season set when episodes are named.
    exact = (
        bool(row.get("_season_pack")),
        profile,
        release_match.title_key(title),
        tuple(episodes),
        tuple(seasons) if episodes else None,
    )
    # Everything else the rule reads once the profile is equal.
    return exact, (identity, container, upscaled)
