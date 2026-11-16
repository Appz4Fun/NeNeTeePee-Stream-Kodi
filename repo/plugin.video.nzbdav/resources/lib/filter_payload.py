# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""One parse for listings that are one payload under different names.

Indexers do not agree on a release's name. One cuts it short
(``...H.265`` for ``...H.265-FLUX``, ``-Ben.The`` for ``-Ben.The.Men``), one
drops a tag (``DV`` for ``DV.HDR``), one lists the bare title. Parsed each
on its own, those rows rank, filter and group as different releases, though
the NZBs behind them are the same files.

Two things show that listings are the same payload before anything is
downloaded, and both need the **byte-equal size**:

* **The same film or episode.** Two encodes of one film never come to the
  same number of bytes, so listings of one item at one exact size are one
  payload, whenever they were posted.
* **A name cut short.** One name is the start of the other and the two are
  the same upload (``nzbget_fleet_dedup.same_listing``: posted within two
  minutes, not told apart by poster or password). This also covers a cut
  that took part of the title with it.

Such listings all take one parse among them (``_fullest``: the one whose
group the result list knows best, then the most detailed), and so does a
name with no group beside a named listing of the payload. They keep their
own names and containers; the release rule then groups them on equal parsed
fields, as it groups everything else. When the parse they share has no
group, ``release_index`` lists them together by the name it came from.

A size that is a round number of megabytes is left alone: an indexer that
rounds could give two encodes one size.
"""

from resources.lib.release_subject import name_form_key

# Set on a parse a row took from another listing of its payload: the name
# (``name_form_key``) of the listing it came from.
ADOPTED_KEY = "parse_from_payload"
_ROUND = (1000000, 1048576)
# More names than this at one exact size is not one payload listed a few
# ways; nothing is unified there.
_MAX_NAMES = 12
_MIN_CUT_NAME = 4


def _exact_size(row):
    """The row's size in bytes when it can stand for a payload, else 0."""
    try:
        size = int(str(row.get("size") or "").strip())
    except (TypeError, ValueError):
        return 0
    if size <= 0 or any(size % unit == 0 for unit in _ROUND):
        return 0
    return size


def _item(row):
    """What film or episode the row is, as its parse reads it, or None."""
    from resources.lib import fallback_streams as _fs
    from resources.lib.release_match import air_date, title_key

    try:
        title, year, seasons, episodes, part = _fs._release_identity(row)
        date = air_date(row, row["_meta"])
    except Exception:  # pylint: disable=broad-except
        return None
    key = title_key(title)
    if not key:
        return None
    return (key, tuple(seasons), tuple(episodes)), year, part, date


def _same_item(left, right):
    """Whether two ``_item`` values name one film or episode.

    The title, seasons and episodes are equal. A year, an air date or (on an
    episode) a part number may be missing on one side; two that are named
    must be equal. A film's part must be equal on both sides, absent
    included: "Dune" is not "Dune Part Two".
    """
    if left is None or right is None or left[0] != right[0]:
        return False
    (_what, year, part, date), (_what2, year2, part2, date2) = left, right
    if year and year2 and year != year2:
        return False
    if date and date2 and date != date2:
        return False
    episodic = bool(left[0][1] or left[0][2])
    if episodic:
        return not (part and part2 and part != part2)
    return part == part2


def _cut_short(short, full):
    """Whether one group of rows lists the other's upload under a cut name.

    ``short`` and ``full`` are ``(name key, rows)``.
    """
    from resources.lib.nzbget_fleet_dedup import same_listing

    short_name, short_rows = short
    full_name, full_rows = full
    if len(short_name) > len(full_name):
        short_name, full_name = full_name, short_name
    if (
        len(short_name) < _MIN_CUT_NAME
        or short_name == full_name
        or not full_name.startswith(short_name)
    ):
        return False
    return any(same_listing(one, other) for one in short_rows for other in full_rows)


def _clusters(names):
    """Group the names of one size bucket into payloads.

    ``names`` is a list of ``(name key, rows, item)``. Two names are one
    payload when they are the same item or one is the other cut short; a
    payload is every name linked that way.
    """
    parent = list(range(len(names)))

    def find(at):
        while parent[at] != at:
            parent[at] = parent[parent[at]]
            at = parent[at]
        return at

    for left, left_name in enumerate(names):
        for right in range(left + 1, len(names)):
            if find(left) == find(right):
                continue
            right_name = names[right]
            if _same_item(left_name[2], right_name[2]) or _cut_short(
                left_name[:2], right_name[:2]
            ):
                parent[find(right)] = find(left)
    payloads = {}
    for at, name in enumerate(names):
        payloads.setdefault(find(at), []).append(name)
    return [payload for payload in payloads.values() if len(payload) > 1]


def _group_names(rows):
    """For each release group in ``rows``, how many different names carry it.

    A real group posts many releases (resolutions, episodes, films), so its
    tag is on many names of a result list. A tag an indexer or a reposter
    added (``-GRP_mp4``, ``-Obfuscated``) or a word misread as a group is on
    one.
    """
    from resources.lib.release_match import title_key

    names = {}
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        if not isinstance(meta, dict) or not isinstance(row.get("title"), str):
            continue
        group = title_key(meta.get("group"))
        if group:
            names.setdefault(group, set()).add(name_form_key(row["title"]))
    return {group: len(found) for group, found in names.items()}


def _tags(meta):
    """How many tags a parse holds in all: ``DV.HDR`` is two, ``DV`` one."""
    return sum(
        len(value) for value in meta.values() if isinstance(value, (list, tuple))
    )


def _fullest(payload, parse_detail, group_names):
    """The entry of ``payload`` whose parse the others take.

    First the parse whose group the rest of the list knows best: ``-NTb``
    over the ``-Obfuscated`` a repost carries, ``-GRP`` over ``-GRP_mp4``,
    ``-Ben.The.Men`` over the cut ``-Ben.The``. A group seen on this one
    name counts for nothing there. Then the most detailed parse
    (``parse_detail``, but with how much is set ahead of whether a group
    is: a bare word misread as a group must not beat a full name), then
    the one with more tags (``DV.HDR`` over ``DV``), then the name that is
    not a cut of another, then the one more indexers list, then the first
    met.
    """
    from resources.lib.release_match import title_key

    def rank(entry):
        name, rows, _item_of = entry
        meta = rows[0]["_meta"]
        detail = parse_detail(meta)
        known = group_names.get(title_key(meta.get("group")), 0) - 1
        cut = any(other[0] != name and other[0].startswith(name) for other in payload)
        return (
            max(known, 0),
            detail[1],
            detail[-1],
            detail,
            _tags(meta),
            not cut,
            len(rows),
        )

    best = payload[0]
    for entry in payload[1:]:
        if rank(entry) > rank(best):
            best = entry
    return best


def _own_fields(meta):
    """``meta`` without the mark this module puts on a parse it copied."""
    if ADOPTED_KEY not in meta:
        return meta
    return {key: value for key, value in meta.items() if key != ADOPTED_KEY}


def unify_one_payload(rows, parse_detail, meta_for, parses_agree, derived_keys=()):
    """Give listings of one payload the fullest parse among them.

    ``rows`` carry ``_meta``, spellings already unified. ``parse_detail``,
    ``meta_for`` and ``parses_agree`` are ``filter``'s helpers for spellings
    of one name (``_parse_detail``, ``_meta_for_spelling``,
    ``_parses_agree``): the same copy-with-own-container is made here. A
    row that takes a parse gets ``ADOPTED_KEY`` on it (the name the parse
    came from) and loses its ``derived_keys``. Returns how many rows changed.
    """
    by_size = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("_meta"), dict):
            continue
        if not isinstance(row.get("title"), str) or row.get("_season_pack"):
            continue
        size = _exact_size(row)
        if size:
            by_size.setdefault(size, {}).setdefault(row["title"], []).append(row)
    changed = 0
    group_names = None
    for titles in by_size.values():
        if not 2 <= len(titles) <= _MAX_NAMES:
            continue
        names = [
            (name_form_key(title), same, _item(same[0]))
            for title, same in titles.items()
        ]
        for payload in _clusters(names):
            if group_names is None:
                group_names = _group_names(rows)
            best_name, best_rows, _item_of = _fullest(
                payload, parse_detail, group_names
            )
            best_meta = best_rows[0]["_meta"]
            # Whose parse it is: the name the fullest row took its own from,
            # when it took one in an earlier pass.
            source = best_meta.get(ADOPTED_KEY) or best_name
            for _name, same, _item_of in payload:
                for row in same:
                    if row["_meta"] is best_meta or parses_agree(
                        _own_fields(row["_meta"]), _own_fields(best_meta)
                    ):
                        continue
                    row["_meta"] = meta_for(best_meta, row)
                    row["_meta"][ADOPTED_KEY] = source
                    for key in derived_keys:
                        row.pop(key, None)
                    changed += 1
    return changed
