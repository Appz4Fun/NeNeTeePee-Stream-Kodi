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
first group whose every member the rule accepts it against. Every member,
because the rule is not transitive: a name without a year matches both a
2005 and a 2023 show, which reject each other. Which of the two it joins
used to depend on the order of the rows, so the copy count a release was
ranked by (counted before ranking) could differ from its picker row
(grouped after). Now the names of a bucket are placed in a fixed order,
the one on the most rows first, and the grouping is the same for any
order of the same rows. Two things make it cheap without changing an
answer:

* Rows can only be one release when they share an exact key: the parsed
  release profile, the title, and the episode. Each key is its own bucket,
  so rows are never compared across them.
* Inside a bucket the rule reads only a row's signature (what it is a
  release of, its container and its upscale flag). Rows with one signature
  always get one answer, so each signature is placed once and the rows
  follow it.

Two things the rule itself does not decide are decided here.

Two names of one group for one upload. A group's release is listed as
``...DV.x265-GRP`` by one indexer and ``...DV.HDR.x265-GRP`` by another:
the parses differ, so the rule keeps them apart. When a listing of each is
the same film or episode at the same exact size in bytes, they are one
upload, and the two releases are joined whole (``_joined_by_upload``).

A name with no release group. ``same_release`` never accepts such a row,
since nothing says whose release it is. Here, rows with no group that
carry the same name (in any
spelling, ``release_subject.name_form_key``) are one release: the same
unnamed post listed by several indexers, and its reposts. So are listings
that took their parse from one of them (``filter_payload``). Only a named
container keeps them apart (a ``.mkv`` and a ``.mp4`` of one name are two
files). A name with no group still never joins a release that has one.
"""

from resources.lib import fallback_streams as _fs
from resources.lib import release_match
from resources.lib.filter_payload import ADOPTED_KEY, exact_size, item_of, same_item
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

    The grouping does not depend on the order of ``rows``: the list is
    grouped before it is ranked (for the copy counts) and again after (for
    the picker and the backups), and both must come to the same releases.
    """
    singles = []
    keyed = []
    names = _Names()
    by_name = {}
    for at, row in enumerate(rows):
        keys = _keys_once_per_name(row, by_name)
        if keys is None:
            singles.append((at, [row]))
            continue
        keyed.append((at, row, keys))
        if keys[0][0] is _NO_GROUP:
            names.join(keys[0][2], keys[0][3])
    buckets = {}
    for at, row, (exact, signature) in keyed:
        if exact[0] is _NO_GROUP:
            # One release for every name linked by a shared upload.
            exact = exact[:2] + (names.of(exact[2]),)
        bucket = buckets.get(exact)
        if bucket is None:
            bucket = buckets[exact] = {}
        bucket.setdefault(signature, []).append((at, row))
    releases = []
    for exact, bucket in buckets.items():
        releases.extend(_bucket_groups(exact[0] is not _NO_GROUP, bucket))
    placed = singles + [
        (members[0][0], [row for _at, row in members])
        for members in _joined_by_upload(releases)
    ]
    placed.sort(key=_position)
    return ReleaseIndex([group for _at, group in placed])


class _Names:
    """Names with no group that are one release with each other.

    A listing that took its parse from another name (``filter_payload``)
    is one upload with it, and each name is one release with its own other
    listings, so the names are joined into sets. A set goes by its first
    name in sorted order, whatever order the rows came in.
    """

    def __init__(self):
        self._parent = {}

    def of(self, name):
        """The name the set of ``name`` goes by."""
        parent = self._parent
        root = name
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(name, name) != root:
            parent[name], name = root, parent[name]
        return root

    def join(self, name, other):
        """``name`` and ``other`` (or None) are one release."""
        if not other or other == name:
            return
        left, right = self.of(name), self.of(other)
        if left != right:
            low, high = sorted((left, right))
            self._parent[high] = low


def _joined_by_upload(releases):
    """``releases`` with those joined that share an upload.

    A group names one upload two ways (``DV`` on one indexer, ``DV.HDR`` on
    another; ``PROPER`` here, ``REPACK`` there). The parses differ, so the
    rule keeps the names apart, and each name has its own other listings.
    Two listings of one group, of the same film or episode, at the same
    exact size are one upload (``filter_payload``: two encodes never come
    to the same number of bytes), so their releases are one release: the
    whole of both, not just the two listings.

    ``releases`` is a list of ``[(position, row), ...]``, each in list
    order; so is the result.
    """
    uploads = {}
    for number, members in enumerate(releases):
        for _at, row in members:
            key = _upload_key(row)
            if key is not None:
                uploads.setdefault(key[:3], []).append((number, key[3]))
    parent = list(range(len(releases)))

    def find(number):
        while parent[number] != number:
            parent[number] = parent[parent[number]]
            number = parent[number]
        return number

    for listed in uploads.values():
        first_number, first_item = listed[0]
        for number, item in listed[1:]:
            if find(number) != find(first_number) and same_item(first_item, item):
                low, high = sorted((find(number), find(first_number)))
                parent[high] = low
    joined = {}
    for number, members in enumerate(releases):
        joined.setdefault(find(number), []).extend(members)
    if len(joined) == len(releases):
        return releases
    for members in joined.values():
        members.sort(key=_position)
    return list(joined.values())


def _upload_key(row):
    """``(group, size, what it is of, item)`` of a named row, or None.

    None for a row with no group, a season pack, a row whose size cannot
    stand for an upload (missing, or rounded by its indexer) or whose parse
    names no film or episode.
    """
    if row.get("_season_pack"):
        return None
    group = release_match.title_key((row.get("_meta") or {}).get("group"))
    size = exact_size(row) if group else 0
    item = item_of(row) if size else None
    if item is None:
        return None
    return group, size, item[0], item


def _position(placed):
    """Sort key: the list position that leads a ``(position, ...)`` pair."""
    return placed[0]


def _bucket_groups(named, bucket):
    """The releases among the rows of one exact key (see ``_keys``).

    ``bucket`` maps each signature to its ``(position, row)`` pairs, in list
    order. Returns each release as its ``(position, row)`` pairs in list
    order.

    A signature joins the first release whose every member it is accepted
    against. Which one is "first" matters only where the rule is not
    transitive: a name with no year fits both the 2005 and the 2023 show.
    The signatures are therefore taken in a fixed order, the one on the
    most rows first (then by the signature itself), so the name with no
    year goes with the year more indexers list, however ``rows`` were
    ordered.

    ``named`` rows are compared by the release rule. Rows with no group
    share a name already (it is their key) and only a named container
    separates them.
    """
    order = sorted(
        bucket.items(), key=lambda item: (-len(item[1]), _orderable(item[0]))
    )
    releases = []  # [[(position, row), ...], [(signature, a row of it), ...]]
    for signature, listed in order:
        first = listed[0][1]
        if named and len(listed) > 1:
            # The rule says once whether a release name groups with itself
            # (it does, unless the rule cannot read it).
            if not release_match.same_release(first, listed[1][1]):
                releases.extend([[one], ()] for one in listed)
                continue
        for members, examples in releases:
            if examples and all(
                _accepts(named, known, example, signature, first)
                for known, example in examples
            ):
                members.extend(listed)
                examples.append((signature, first))
                break
        else:
            releases.append([list(listed), [(signature, first)]])
    for members, _examples in releases:
        members.sort(key=_position)
    return [members for members, _examples in releases]


def _orderable(signature):
    """A signature as a value that sorts against any other signature."""
    return repr(signature)


def _accepts(named, known, member, signature, row):
    if not named:
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
            # The name it took its parse from, when it took one: listings of
            # one upload that ended with no group stay one release.
            taken = meta.get(ADOPTED_KEY)
            taken = taken if isinstance(taken, str) and taken else None
            return (_NO_GROUP, bool(row.get("_season_pack")), name, taken), container
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
