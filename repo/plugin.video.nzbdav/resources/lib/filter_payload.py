# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A listing whose name lost its group takes the parse of the upload it is.

Indexers do not agree on a release's name. One cuts it short
(``...H.265`` for ``...H.265-FLUX``, ``-Ben.The`` for ``-Ben.The.Men``), one
lists the bare title, one a name with no group at all. Parsed on its own,
such a row has no group: it ranks last in its resolution and is never
listed with the release it is a copy of.

Two things show, before anything is downloaded, that two listings are one
upload, and both need the **byte-equal size**:

* **The same film or episode.** Two encodes of one film never come to the
  same number of bytes, so listings of one item at one exact size are one
  upload, whenever they were posted.
* **A name cut short.** One name is the start of the other and the two are
  the same upload (``nzbget_fleet_dedup.same_listing``: posted within two
  minutes, not told apart by poster or password), with the same season and
  episode where either names one.

A listing with no group that is one upload with a named listing takes the
named one's whole parse: its group, tags and title. So does a listing whose
group is the named one's cut short (``-GUA`` for ``-GUACAMOLE``); between
two such groups the one the result list knows better leads, so ``-GRP``
is not replaced by ``-GRP_mp4``. Listings with no group and no named
listing beside them take the fullest parse among themselves.

What is never done here: a listing that states a group does not take
another group's parse. Two names of one group that tag one upload
differently (``DV`` and ``DV.HDR``) keep their own parses and are listed
together by ``release_index``, which can join whole releases; changing one
upload's parse would split it from the other copies of its own name.

A size that an indexer rounded is left alone: a whole number of megabytes
or tenths of one, or of hundredths of a gibibyte ("4.37 GB"), could be two
encodes at one size. Sizes to the kilobyte are exact enough.
"""

import collections

from resources.lib import nfo_audio
from resources.lib.release_subject import name_form_key

# Set on a parse a row took from another listing of its upload: the name
# (``name_form_key``) of the listing it came from.
ADOPTED_KEY = "parse_from_payload"
_ROUND = (100000, 1048576)
# Sizes shown as "4.37 GB" or "512.3 MB" and turned back into bytes.
_ROUND_SHOWN = (1073741824 / 100.0, 1048576 / 10.0)
# More names than this at one exact size is not one upload listed a few
# ways; nothing is unified there.
_MAX_NAMES = 12
_MIN_CUT_NAME = 4
# What a read (NFO, MediaInfo) or a labelled copy put on a parse. A parse
# handed to another row holds what the name says; these are put on each row
# again afterwards (``nfo_audio.apply_known``, ``filter_atmos.share_atmos``).
_READ_KEYS = (
    nfo_audio.NAME_AUDIO_KEY,
    nfo_audio.SOURCE_KEY,
    nfo_audio.FROM_NFO_KEY,
    nfo_audio.NAME_HDR_KEY,
    "atmos_inferred",
    "dv_profile",
    "bit_depth",
)

# One name at one exact size: its key, its rows, what it is of
# (``item_of``), and its group's key ("" for none).
_Name = collections.namedtuple("_Name", "name rows item group")


def exact_size(row):
    """The row's size in bytes when it can stand for an upload, else 0."""
    try:
        size = int(str(row.get("size") or "").strip())
    except (TypeError, ValueError):
        return 0
    if size <= 0 or any(size % unit == 0 for unit in _ROUND):
        return 0
    if any(abs(size - round(size / unit) * unit) <= 1 for unit in _ROUND_SHOWN):
        return 0
    return size


def item_of(row):
    """What film or episode the row is, as its parse reads it, or None.

    ``((title key, seasons, episodes), year, part, air date)``.
    """
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


def same_item(left, right):
    """Whether two ``item_of`` values name one film or episode.

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


def _cut_of(short, full):
    """Whether ``short`` lists ``full``'s upload under a name cut short.

    The short name is the start of the full one and some listing of each is
    the same upload. A cut can take the end of the title with it, so the
    titles are not compared, but what is left must not contradict: the same
    season and episode on both sides (or on neither), and no two years.
    """
    from resources.lib.nzbget_fleet_dedup import same_listing

    if not _MIN_CUT_NAME <= len(short.name) < len(full.name):
        return False
    if not full.name.startswith(short.name) or not _may_be_cut(short.item, full.item):
        return False
    return any(same_listing(one, other) for one in short.rows for other in full.rows)


def _may_be_cut(short, full):
    """Whether what two names are of (``item_of``) allows one to be a cut."""
    if short is None or full is None or short[0][1:] != full[0][1:]:
        return False
    return not (short[1] and full[1] and short[1] != full[1])


def _one_upload(taker, giver):
    """Whether ``taker`` (no group) is one upload with the named ``giver``."""
    return same_item(taker.item, giver.item) or _cut_of(taker, giver)


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


def as_named(meta):
    """``meta`` as the name alone reads.

    Without this module's mark, and without what an NFO, MediaInfo or a
    labelled copy added on an earlier pass: the audio and channels the name
    tags (``nfo_audio.stated_audio``) and its own dynamic range.
    """
    if ADOPTED_KEY not in meta and not any(key in meta for key in _READ_KEYS):
        return meta
    named = {
        key: value
        for key, value in meta.items()
        if key != ADOPTED_KEY and key not in _READ_KEYS
    }
    named["audio"], named["channels"] = nfo_audio.stated_audio(meta)
    kept = meta.get(nfo_audio.NAME_HDR_KEY)
    if isinstance(kept, list):
        named["hdr"] = list(kept)
    return named


class _Pass:  # pylint: disable=too-few-public-methods
    """What one ``unify_one_payload`` call needs at every size."""

    def __init__(self, rows, tools):
        self.rows = rows
        self.parse_detail, self.meta_for, self.parses_agree, self.derived = tools
        self._known = None

    def known(self, group):
        """How many other names of the list carry ``group`` (0 for none)."""
        if self._known is None:
            self._known = _group_names(self.rows)
        return max(self._known.get(group, 0) - 1, 0)

    def rank(self, entry):
        """Sort key: the name whose parse leads, highest first.

        The group the list knows best, then a parse of its own over one
        taken from another name, then the most detailed (how much is set,
        then ``parse_detail``, then how many tags), then the name more
        indexers list, then the name itself, so the order of the rows
        never decides.
        """
        meta = as_named(entry.rows[0]["_meta"])
        detail = self.parse_detail(meta)
        return (
            self.known(entry.group),
            ADOPTED_KEY not in entry.rows[0]["_meta"],
            detail[-1],
            detail,
            _tags(meta),
            len(entry.rows),
            entry.name,
            entry.rows[0]["title"],
        )

    def give(self, giver, taker):
        """Give ``taker``'s rows the parse of ``giver``; True if any changed."""
        shared = as_named(giver.rows[0]["_meta"])
        source = giver.rows[0]["_meta"].get(ADOPTED_KEY) or giver.name
        changed = False
        for row in taker.rows:
            meta = row["_meta"]
            if not self.parses_agree(as_named(meta), shared):
                row["_meta"] = self.meta_for(shared, row)
                row["_meta"][ADOPTED_KEY] = source
            elif not shared.get("group") and meta.get(ADOPTED_KEY) != source:
                # Nothing to take, but the mark is what lists two names
                # with no group together (``release_index``).
                meta[ADOPTED_KEY] = source
            else:
                continue
            changed = True
            for key in self.derived:
                row.pop(key, None)
        return changed


def _named_giver(taker, named, state):
    """The named listing ``taker`` (no group) is an upload of, or None.

    None too when it is one upload with listings of two groups: nothing
    says which it is.
    """
    givers = [entry for entry in named if _one_upload(taker, entry)]
    if not givers or len({entry.group for entry in givers}) > 1:
        return None
    return max(givers, key=state.rank)


def _cut_groups(named, state):
    """Pairs ``(giver, taker)`` among named listings whose group was cut.

    ``-GUA`` beside ``-GUACAMOLE``, ``-GRP`` beside ``-GRP_mp4``: one group
    is the start of the other, one name the start of the other, the same
    upload. The group the list knows better gives; when neither is known
    better, the longer one does.
    """
    pairs = []
    for short in named:
        for full in named:
            if (
                short.group != full.group
                and full.group.startswith(short.group)
                and _cut_of(short, full)
            ):
                if state.known(short.group) > state.known(full.group):
                    pairs.append((short, full))
                else:
                    pairs.append((full, short))
    return pairs


def _unify_size(names, state):
    """Hand parses round among the names listed at one exact size."""
    named = [entry for entry in names if entry.group]
    bare = [entry for entry in names if not entry.group]
    for giver, taker in _cut_groups(named, state):
        state.give(giver, taker)
    left = []
    for taker in bare:
        giver = _named_giver(taker, named, state)
        if giver is None:
            left.append(taker)
        else:
            state.give(giver, taker)
    # Names with no group and no named listing to take from: the fullest
    # parse among those that are one upload.
    left.sort(key=state.rank, reverse=True)
    led = []
    for entry in left:
        lead = next(
            (
                other
                for other in led
                if same_item(entry.item, other.item)
                or _cut_of(entry, other)
                or _cut_of(other, entry)
            ),
            None,
        )
        if lead is None:
            led.append(entry)
        else:
            state.give(lead, entry)


def unify_one_payload(rows, parse_detail, meta_for, parses_agree, derived_keys=()):
    """Give a listing that lost its group the parse of its upload's name.

    ``rows`` carry ``_meta``, spellings already unified. ``parse_detail``,
    ``meta_for`` and ``parses_agree`` are ``filter``'s helpers for spellings
    of one name (``_parse_detail``, ``_meta_for_spelling``,
    ``_parses_agree``): the same copy-with-own-container is made here. A
    row that takes a parse gets ``ADOPTED_KEY`` on it (the name the parse
    came from) and loses its ``derived_keys``.

    Returns ``{id(row): names}`` for every row that took a parse and every
    row one was taken from: the other names (``name_form_key``) its upload
    is listed under. What was read for one of them is true of the row too
    (``nfo_audio.apply_known``).
    """
    state = _Pass(rows, (parse_detail, meta_for, parses_agree, derived_keys))
    by_size = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("_meta"), dict):
            continue
        if not isinstance(row.get("title"), str) or row.get("_season_pack"):
            continue
        size = exact_size(row)
        if size:
            by_size.setdefault(size, {}).setdefault(row["title"], []).append(row)
    for titles in by_size.values():
        if 2 <= len(titles) <= _MAX_NAMES:
            _unify_size(_names_at(titles), state)
    return _other_names(by_size)


def _names_at(titles):
    """``titles`` (``{title: rows}`` at one size) as ``_Name`` entries."""
    from resources.lib.release_match import title_key

    return [
        _Name(
            name_form_key(title),
            same,
            item_of(same[0]),
            title_key(same[0]["_meta"].get("group")),
        )
        for title, same in titles.items()
    ]


def _other_names(by_size):
    """``{id(row): names}``: the names a row's upload is also listed under.

    Read from the marks, so it is the same on every pass: a row that took a
    parse names where it came from, and a row of that name at that size
    names every row that took from it.
    """
    others = {}
    for titles in by_size.values():
        takers = {}
        for title, same in titles.items():
            for row in same:
                source = row["_meta"].get(ADOPTED_KEY)
                own = name_form_key(title)
                if isinstance(source, str) and source != own:
                    others[id(row)] = (source,)
                    takers.setdefault(source, set()).add(own)
        for title, same in titles.items():
            found = takers.get(name_form_key(title))
            if found:
                for row in same:
                    others.setdefault(id(row), tuple(sorted(found)))
    return others
