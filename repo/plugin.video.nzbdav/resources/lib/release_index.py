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

Two names for one upload of a group. A group's release is listed as
``...DV.x265-GRP`` by one indexer and ``...DV.HDR.x265-GRP`` by another, or
with its group cut short (``-GUA`` for ``-GUACAMOLE``): the parses differ,
so the rule keeps them apart. When a listing of each is the same upload
(the same exact size in bytes, and the same film or episode or one name
the other cut short), the two releases are joined whole
(``_joined_by_upload``). No listing's parse is changed for it.

A name with no release group. ``same_release`` never accepts such a row,
since nothing says whose release it is. Here, rows with no group that
carry the same name (in any
spelling, ``release_subject.name_form_key``) are one release: the same
unnamed post listed by several indexers, and its reposts. So are listings
that took their parse from one of them (``filter_payload``). Only a named
container keeps them apart (a ``.mkv`` and a ``.mp4`` of one name are two
files). A name with no group still never joins a release that has one.
"""

import collections
import threading

from resources.lib import fallback_streams as _fs
from resources.lib import release_match
from resources.lib.filter_payload import (
    ADOPTED_KEY,
    Sets,
    cut_of_one_upload,
    exact_size,
    item_of,
    same_item,
)
from resources.lib.release_subject import name_form_key

# First in the exact key of a row with no release group.
_NO_GROUP = "no group"


class ReleaseIndex:  # pylint: disable=too-few-public-methods
    """The rows of one list, grouped into releases.

    ``groups`` is a list of lists: each group keeps its rows in the order
    they were given, and the groups are ordered by their first row. A row
    that cannot be parsed is a group of one.
    """

    __slots__ = ("groups", "labels", "_group_at")

    def __init__(self, groups, labels=None):
        self.groups = groups
        # id(row) -> the size label this grouping gave it (``_by_size``).
        self.labels = labels or {}
        self._group_at = {id(row): group for group in groups for row in group}

    def group_of(self, row):
        """The group ``row`` is in: the same list object for every member.

        A row the index was not built from is its own group.
        """
        group = self._group_at.get(id(row))
        return group if group is not None else [row]


# The last few groupings made, for a list grouped again unchanged.
_RECENT = collections.OrderedDict()
_RECENT_KEPT = 4
# Lists are filtered on worker threads too (``filter.filter_results``).
_RECENT_LOCK = threading.Lock()


def build(rows):
    """Group ``rows`` into releases and return the ``ReleaseIndex``.

    One picker open groups the same rows several times: to count copies
    before ranking, for the picker's rows, for the NZBGet backups. The
    grouping depends on the rows and their parses, not on their order, so
    a list grouped again with the same rows, parses and metadata pass
    (``filter.ATTACHED_KEY``) takes the earlier grouping, put in this
    list's order.
    """
    key = _recent_key(rows)
    earlier = None
    if key is not None:
        with _RECENT_LOCK:
            earlier = _RECENT.get(key)
            if earlier is not None:
                _RECENT.move_to_end(key)
    if earlier is not None:
        return _in_order(earlier, rows)
    index = _build(rows)
    if key is not None:
        with _RECENT_LOCK:
            _RECENT[key] = index
            while len(_RECENT) > _RECENT_KEPT:
                _RECENT.popitem(last=False)
    return index


def _recent_key(rows):
    """What a list's grouping depends on, or None when it cannot be told.

    Each row, its parse object and the mark of the pass that made it. A
    row with no mark may have had its parse changed in place, so a list
    with one is grouped afresh.
    """
    key = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("_attached"):
            return None
        key.append((id(row), id(row.get("_meta")), row["_attached"]))
    return frozenset(key) if key else None


def _in_order(index, rows):
    """``index``'s groups with their rows, and themselves, in ``rows``' order.

    The size labels (``SIZE_LABEL_KEY``) are put back as this grouping made
    them: a grouping of another list may have relabelled a shared row.
    """
    for group in index.groups:
        for row in group:
            label = index.labels.get(id(row))
            if label:
                row[SIZE_LABEL_KEY] = label
            else:
                row.pop(SIZE_LABEL_KEY, None)
    position = {id(row): at for at, row in enumerate(rows)}
    groups = [
        sorted(group, key=lambda row: position[id(row)]) for group in index.groups
    ]
    groups.sort(key=lambda group: position[id(group[0])])
    return ReleaseIndex(groups, index.labels)


def _build(rows):
    """``build`` without the reuse of an earlier grouping.

    Season packs (``_season_pack`` rows) are grouped among themselves and
    never with an episode, as the rule's callers always held.

    The grouping does not depend on the order of ``rows``: the list is
    grouped before it is ranked (for the copy counts) and again after (for
    the picker and the backups), and both must come to the same releases.
    """
    singles = []
    keyed = []
    by_name = {}
    taken = {}  # a name with no group -> the names its rows took parses from
    for at, row in enumerate(rows):
        keys = _keys_once_per_name(row, by_name)
        if keys is None:
            singles.append((at, [row]))
            continue
        keyed.append((at, row, keys))
        if keys[0][0] is _NO_GROUP and keys[0][3]:
            taken.setdefault(keys[0][2], set()).add(keys[0][3])
    names = Sets()
    for name, sources in taken.items():
        if len(sources) == 1:
            names.join(name, next(iter(sources)))
    buckets = {}
    for at, row, (exact, signature) in keyed:
        if exact[0] is _NO_GROUP:
            exact = exact[:2] + (_release_name(exact[2], exact[3], taken, names),)
        bucket = buckets.get(exact)
        if bucket is None:
            bucket = buckets[exact] = {}
        bucket.setdefault(signature, []).append((at, row))
    releases = []
    for exact, bucket in buckets.items():
        if exact[0] is _NO_GROUP:
            for members in _bucket_groups(False, bucket):
                releases.extend(_by_size(members))
        else:
            releases.extend(_bucket_groups(True, bucket))
    placed = singles + [
        (members[0][0], [row for _at, row in members])
        for members in _joined_by_upload(releases)
    ]
    placed.sort(key=_position)
    groups = [group for _at, group in placed]
    labels = {
        id(row): row[SIZE_LABEL_KEY]
        for group in groups
        for row in group
        if isinstance(row, dict) and row.get(SIZE_LABEL_KEY)
    }
    return ReleaseIndex(groups, labels)


# Set on a row with no group whose name is listed at two or more sizes far
# apart: "NOGRP-A" for the largest posts, "NOGRP-B" for the next, and so on.
SIZE_LABEL_KEY = "_nogroup_label"


def _by_size(members):
    """One release of rows with no group, split where the sizes are far apart.

    A name with no group is all there is to go by, and one name can cover a
    1 GB and a 50 GB post. The sized rows are sorted and cut wherever one
    is more than ``_ODD_SIZE_FACTOR`` times the one before. Rows without a
    size go with the part that has the most rows (the larger on a tie).
    When there are two or more parts, each row is labelled after its part,
    largest first (``SIZE_LABEL_KEY``).
    """
    for _at, row in members:
        row.pop(SIZE_LABEL_KEY, None)
    sized = sorted(
        ((_row_size(row), at, row) for at, row in members if _row_size(row) > 0),
        key=lambda one: (one[0], one[1]),
    )
    parts = []
    for size, at, row in sized:
        if parts and size <= parts[-1][-1][0] * _ODD_SIZE_FACTOR:
            parts[-1].append((size, at, row))
        else:
            parts.append([(size, at, row)])
    if len(parts) < 2:
        return [members]
    parts.reverse()  # largest first
    unsized = [(at, row) for at, row in members if _row_size(row) <= 0]
    biggest = max(range(len(parts)), key=lambda at: (len(parts[at]), -at))
    out = []
    for number, part in enumerate(parts):
        label = "NOGRP-" + _letters(number)
        rows = [(at, row) for _size, at, row in part]
        if number == biggest:
            rows.extend(unsized)
        for _at, row in rows:
            row[SIZE_LABEL_KEY] = label
        rows.sort(key=_position)
        out.append(rows)
    return out


def _letters(number):
    """ "A" for 0, "B" for 1, ... "Z", then "AA", "AB" and on."""
    out = ""
    number += 1
    while number:
        number, rest = divmod(number - 1, 26)
        out = chr(ord("A") + rest) + out
    return out


def _release_name(name, source, taken, names):
    """The name a row with no group is listed under.

    A listing that took its parse from another name (``filter_payload``) is
    one upload with it, and each name is one release with its own other
    listings. So when the rows of ``name`` took from one other name only,
    the two names are one release (``names`` holds those sets). When they
    took from two (a bare "Film 1080p" beside the 2019 film at one size and
    the 2021 film at another), ``name`` says too little to be one release:
    each such row goes with the name it took from, and the rest stay under
    their own.
    """
    if source and len(taken.get(name, ())) > 1:
        return names.of(source)
    return names.of(name)


# As ``filter._SIZE_OUTLIER_FACTOR`` and ``_SIZE_OUTLIER_MIN_COPIES``: a
# listing this far from its own release's usual size is not a normal copy.
_ODD_SIZE_FACTOR = 1.35
_ODD_SIZE_MIN_COPIES = 3


def _joined_by_upload(releases):
    """``releases`` with those joined that share an upload.

    A group's upload gets two names: ``DV`` on one indexer and ``DV.HDR`` on
    another, ``PROPER`` here and ``REPACK`` there, ``-GUA`` where the name
    was cut and ``-GUACAMOLE`` where it was not. The parses differ, so the
    rule keeps the names apart, and each name has its own other listings.
    Two listings at the same exact size are one upload
    (``filter_payload``: two encodes never come to the same number of
    bytes) when they are

    * of one group and the same film or episode, or
    * of two groups, one the start of the other, under names one the start
      of the other, posted as one upload (``cut_of_one_upload``).

    Their releases are then one release: the whole of both, not just the
    two listings, so no listing leaves the copies of its own name.

    Two guards keep distinct releases apart:

    * a listing far from its own release's usual size joins nothing: a box
      set listed once under a single film's name must not bring the film's
      release in with it;
    * only releases that each share an upload with every other one are
      joined. A name that drops a tag can be listed at the size of a
      group's Dolby Vision encode and again at the size of its HDR10
      encode: it shares an upload with both, they share none with each
      other, and such a set is left as the rule has it.

    ``releases`` is a list of ``[(position, row), ...]``, each in list
    order; so is the result.
    """
    by_size = {}
    for number, members in enumerate(releases):
        if members[0][1].get("_season_pack"):
            continue
        usual = _usual_size(members)
        for _at, row in members:
            size = exact_size(row)
            if size and _near(size, usual):
                by_size.setdefault(size, []).append((number, row))
    shared = set()
    for listed in by_size.values():
        if len({number for number, _row in listed}) < 2:
            # Every listing at this size is in one release already.
            continue
        listed = [
            (number, row, key)
            for number, row in listed
            for key in (_upload_key(row),)
            if key is not None
        ]
        for at, (number, row, key) in enumerate(listed):
            for other, other_row, other_key in listed[at + 1 :]:
                if other != number and _one_upload(row, key, other_row, other_key):
                    shared.add((min(number, other), max(number, other)))
    if not shared:
        return releases
    linked = Sets()
    for number, other in shared:
        linked.join(number, other)
    joined = []
    absorbed = set()
    for numbers in linked.sets({number for pair in shared for number in pair}):
        ordered = sorted(numbers)
        if all(
            (number, other) in shared
            for at, number in enumerate(ordered)
            for other in ordered[at + 1 :]
        ):
            members = [one for number in ordered for one in releases[number]]
            members.sort(key=_position)
            joined.append(members)
            absorbed.update(ordered)
    return [
        members for number, members in enumerate(releases) if number not in absorbed
    ] + joined


def _one_upload(row, key, other, other_key):
    """Whether two named rows of one exact size are one upload."""
    group, _size, item = key
    other_group, _other_size, other_item = other_key
    if group == other_group:
        return same_item(item, other_item)
    if group.startswith(other_group) or other_group.startswith(group):
        return cut_of_one_upload(row, other)
    return False


def _usual_size(members):
    """The median size of a release's listings, or 0 with too few to tell."""
    sizes = sorted(
        size for size in (_row_size(row) for _at, row in members) if size > 0
    )
    if len(sizes) < _ODD_SIZE_MIN_COPIES:
        return 0
    middle = len(sizes) // 2
    if len(sizes) % 2:
        return sizes[middle]
    return (sizes[middle - 1] + sizes[middle]) / 2.0


def _row_size(row):
    try:
        return int(row.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def _near(size, usual):
    """Whether ``size`` is a normal copy's, given the release's usual size."""
    if not usual:
        return True
    return usual / _ODD_SIZE_FACTOR <= size <= usual * _ODD_SIZE_FACTOR


def _upload_key(row):
    """``(group, size, item)`` of a named row, or None.

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
    return group, size, item


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
