# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A corpus of real searches, for checking a grouping or ranking change.

``fixtures/grouping_corpus.json.gz`` holds what 23 searches returned (12
films, 11 episodes, 41,446 rows): each row's release name, size, the two
dates, the password flag, and stand-ins for the rest. The link is a number
per distinct link, the indexer a label ("Indexer 01"), the poster a hash
that stays an address where it was one. Nothing else was kept: no real
link, key, host or indexer name.

``open_picker`` runs a search's rows through the same steps a picker open
does and returns what the picker would list. ``measure`` turns that into
the numbers a change is judged by, and ``reference_groups`` is the release
rule applied pair by pair with nothing clever in between, to compare any
faster grouping against.

Use it from a test (``tests/test_grouping_corpus.py``) or by hand::

    uv run --with-requirements requirements-dev.txt --no-project \\
        python tests/grouping_corpus.py
"""

import collections
import gzip
import json
import os
import time

_FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "grouping_corpus.json.gz"
)
_LINK = "https://indexer.invalid/nzb/{}"

Search = collections.namedtuple("Search", "name request rows")
Opened = collections.namedtuple("Opened", "search rows filtered groups")

# Every filter open and nothing cut off, so a change is judged on all rows.
SETTINGS = {"max_results": "0", "sort_order": "0"}


def settings_getter(key, default=""):
    """The add-on's settings for a corpus run: defaults, and no row limit."""
    return SETTINGS.get(key, default)


def load():
    """The corpus as a list of ``Search``; ``rows`` are fresh dicts."""
    with gzip.open(_FIXTURE, "rt", encoding="utf-8") as handle:
        data = json.load(handle)
    columns = data["columns"]
    searches = []
    for number, entry in enumerate(data["searches"], 1):
        request = entry["request"]
        name = "{:02d} {}".format(number, request["title"])
        rows = [_row(dict(zip(columns, values))) for values in entry["rows"]]
        searches.append(Search(name, request, rows))
    return searches


def load_search(name):
    """Fresh rows of the search called ``name`` (``Search.name``)."""
    return next(search.rows for search in load() if search.name == name)


def _row(packed):
    row = {
        "title": packed["title"],
        "size": packed["size"],
        "pubdate": packed["pubdate"],
        "indexer": packed["indexer"],
        "link": _LINK.format(packed["link"]) if packed["link"] else "",
    }
    for key in ("usenetdate", "poster", "password"):
        if packed[key]:
            row[key] = packed[key]
    return row


def requested_rows(search, rows):
    """``rows`` after the rules that keep only the requested film or episode."""
    from resources.lib import filter as _filter

    request = search.request
    if request["kind"] == "episode":
        rows = _filter.drop_other_episodes(
            rows, (request["season"], request["episode"])
        )
        return _filter.drop_other_shows(rows, request["title"])
    rows = _filter.drop_other_years(rows, request.get("year", ""), request["title"])
    return _filter.drop_tv_episodes(rows)


def open_picker(search, rows=None):
    """Run one search as a picker open; ``rows`` default to the search's own.

    Returns ``Opened``: the rows it started from, the ranked rows that passed
    the filters, and those grouped as the List compacted view shows them.
    """
    from resources.lib import filter as _filter
    from resources.lib.results_groups import release_groups

    rows = search.rows if rows is None else rows
    _filter.attach_metadata(rows)
    kept = requested_rows(search, rows)
    filtered, _all_rows = _filter.filter_results(kept, settings_getter=settings_getter)
    return Opened(search, rows, filtered, release_groups(filtered))


def reference_groups(rows):
    """``rows`` grouped by the release rule alone, the slow and plain way.

    A row joins the first group whose every member it is the same release
    as (``_plainly_the_same``), and a season pack never groups. No keys,
    buckets or caches: this is what any faster grouping has to equal.

    The rule is not transitive, so the outcome depends on the order rows
    are taken in. They are taken as ``release_index`` documents: the name
    and parse carried by the most rows first (``_commonest_first``). Then
    releases that share an upload are joined (``_join_shared_uploads``).
    The groups come back in list order, each with its rows in list order.
    """
    position = {id(row): at for at, row in enumerate(rows)}
    linked = _linked_names(rows)
    groups = []
    for row in _commonest_first(rows, linked):
        if not row.get("_season_pack"):
            for group in groups:
                if all(
                    not member.get("_season_pack")
                    and _plainly_the_same(member, row, linked)
                    for member in group
                ):
                    group.append(row)
                    break
            else:
                groups.append([row])
        else:
            groups.append([row])
    groups = [part for group in groups for part in _split_by_size(group)]
    groups = _join_shared_uploads(groups)
    for group in groups:
        group.sort(key=lambda row: position[id(row)])
    groups.sort(key=lambda group: position[id(group[0])])
    return groups


def _split_by_size(group):
    """A group of rows with no group, cut where sizes are 1.35x apart.

    Rows without a size go with the largest part (on a tie, the part of the
    larger posts). Groups with a release group are returned whole.
    """
    if any(_named(row.get("_meta") or {}) for row in group):
        return [group]

    def size(row):
        try:
            return int(row.get("size") or 0)
        except (TypeError, ValueError):
            return 0

    sized = sorted((row for row in group if size(row) > 0), key=size)
    parts = []
    for row in sized:
        if parts and size(row) <= size(parts[-1][-1]) * 1.35:
            parts[-1].append(row)
        else:
            parts.append([row])
    if len(parts) < 2:
        return [group]
    parts.reverse()
    biggest = max(range(len(parts)), key=lambda at: (len(parts[at]), -at))
    parts[biggest].extend(row for row in group if size(row) <= 0)
    return parts


def _linked_names(rows):
    """For names with no group: the name each is listed under.

    A listing that took its parse from another name is one upload with it,
    and every listing of a name is that name's release. So a name whose
    rows took from one other name only is one release with it. A name
    whose rows took from two says too little: it is left alone here, and
    ``_listed_under`` sends each such row with the name it took from.
    """
    from resources.lib.release_subject import name_form_key

    took = collections.defaultdict(set)
    for row in rows:
        meta = row.get("_meta") or {}
        source = meta.get("parse_from_payload")
        if not _named(meta) and isinstance(source, str) and source:
            took[name_form_key(row.get("title"))].add(source)
    sets = []
    for name, sources in took.items():
        if len(sources) != 1:
            continue
        pair = {name} | sources
        touching = [one for one in sets if one & pair]
        for one in touching:
            sets.remove(one)
            pair |= one
        sets.append(pair)
    linked = {name: min(one) for one in sets for name in one}
    linked[_AMBIGUOUS] = {name for name, sources in took.items() if len(sources) > 1}
    return linked


_AMBIGUOUS = object()


def _named(meta):
    """Whether a parse has a group, as the release rule reads it."""
    from resources.lib.release_match import title_key

    return bool(title_key(meta.get("group")))


def _listed_under(row, linked):
    """The name a row with no group is listed under (see ``_linked_names``)."""
    from resources.lib.release_subject import name_form_key

    name = name_form_key(row.get("title"))
    source = (row.get("_meta") or {}).get("parse_from_payload")
    if name in linked[_AMBIGUOUS] and isinstance(source, str) and source:
        name = source
    return linked.get(name, name)


def _join_shared_uploads(groups):
    """Join groups that hold listings of one upload of a group.

    Two rows are one upload when they have the same exact size in bytes
    (not a size an indexer rounded) and either the same release group and
    the same film or episode, or groups one the start of the other and
    names one the other cut short, posted together. A row far from its own
    group's usual size joins nothing, and groups are joined only when each
    of them shares an upload with every other one of the set.
    """
    import statistics

    from resources.lib.filter_payload import (
        cut_of_one_upload,
        exact_size,
        item_of,
        same_item,
    )
    from resources.lib.release_match import title_key

    def group_of(row):
        return title_key((row.get("_meta") or {}).get("group"))

    def usable(row, group):
        if not group_of(row) or row.get("_season_pack") or not exact_size(row):
            return False
        if item_of(row) is None:
            return False
        sizes = [int(one["size"]) for one in group if str(one.get("size")).isdigit()]
        sizes = [size for size in sizes if size > 0]
        if len(sizes) < 3:
            return True
        usual = statistics.median(sizes)
        return usual / 1.35 <= exact_size(row) <= usual * 1.35

    def one_upload(row, other):
        left, right = group_of(row), group_of(other)
        if left == right:
            return same_item(item_of(row), item_of(other))
        if left.startswith(right) or right.startswith(left):
            return cut_of_one_upload(row, other)
        return False

    by_size = collections.defaultdict(list)
    for number, group in enumerate(groups):
        for row in group:
            if usable(row, group):
                by_size[exact_size(row)].append((number, row))
    shared = set()
    for listed in by_size.values():
        for number, row in listed:
            for other, other_row in listed:
                if number < other and one_upload(row, other_row):
                    shared.add((number, other))
    sets = []
    for pair in shared:
        pair = set(pair)
        touching = [one for one in sets if one & pair]
        for one in touching:
            sets.remove(one)
            pair |= one
        sets.append(pair)
    out = [list(group) for group in groups]
    for one in sets:
        ordered = sorted(one)
        if all((a, b) in shared for a in ordered for b in ordered if a < b):
            for number in ordered[1:]:
                out[ordered[0]].extend(out[number])
                out[number] = []
    return [group for group in out if group]


def _commonest_first(rows, linked):
    """``rows`` ordered by how many rows read exactly as they do, most first.

    What a row reads as is everything the rule looks at: its parsed release
    fields, what it is a release of, its container and its upscale flag.
    Equal counts are ordered by that reading itself, so the order never
    depends on how ``rows`` came.
    """
    from resources.lib import fallback_streams as _fs
    from resources.lib.release_match import profile_group, release_profile

    def reading(row):
        meta = row.get("_meta") or {}
        container = str(meta.get("container") or "").strip().casefold()
        try:
            profile = release_profile(row)
            if profile_group(profile):
                identity = _fs._release_identity(row)
                upscaled = _fs._meta_bool_from_meta(meta, "upscaled")
                return (profile, repr((identity, container, upscaled)))
        except Exception:  # pylint: disable=broad-except
            return ("unreadable", id(row))
        return ("no group", _listed_under(row, linked), repr(container))

    readings = [reading(row) for row in rows]
    counts = collections.Counter(readings)
    order = sorted(
        range(len(rows)),
        key=lambda at: (-counts[readings[at]], repr(readings[at][-1]), at),
    )
    return [rows[at] for at in order]


def _plainly_the_same(member, row, linked):
    """The release rule, plus the one thing it leaves to the grouping.

    Two names with a group are ``release_match.same_release``. Two names
    with no group are one release when they are the same name in any
    spelling, or names ``linked`` by a shared upload (``_linked_names``),
    unless both name a container and the containers differ.
    """
    from resources.lib.release_match import same_release

    def unnamed(one):
        meta = one.get("_meta") or {}
        return not _named(meta), str(meta.get("container") or "").casefold()

    (left_bare, left_container), (right_bare, right_container) = (
        unnamed(member),
        unnamed(row),
    )
    if not (left_bare and right_bare):
        return same_release(member, row)
    if left_container and right_container and left_container != right_container:
        return False

    under = _listed_under(member, linked)
    return bool(under) and under == _listed_under(row, linked)


def reference_upload_count(rows):
    """``upload_count.count_uploads`` by comparing with every counted upload."""
    from resources.lib.nzbget_fleet_dedup import same_listing

    heads = []
    for row in rows:
        if isinstance(row, dict) and not any(same_listing(row, h) for h in heads):
            heads.append(row)
    return len(heads)


def partition(groups):
    """Groups as a comparable value: which links sit together, in any order."""
    return sorted(sorted(_ident(row) for row in group) for group in groups)


def _ident(row):
    return (row.get("link") or "", row.get("title") or "", row.get("indexer") or "")


def split_uploads(groups):
    """``(listed more than once, of those shown as two or more picker rows)``.

    An upload is the listings the same-listing rule joins
    (``nzbget_fleet_dedup.same_listing``): equal size, posted within two
    minutes, not told apart. One upload listed by several indexers belongs
    in one picker row.
    """
    from resources.lib.nzbget_fleet_dedup import same_listing

    by_size = collections.defaultdict(list)
    for number, group in enumerate(groups):
        for row in group:
            by_size[str(row.get("size") or "")].append((number, row))
    multi = split = 0
    for members in by_size.values():
        uploads = []
        for number, row in members:
            for upload in uploads:
                if same_listing(row, upload[0][1]):
                    upload.append((number, row))
                    break
            else:
                uploads.append([(number, row)])
        for upload in uploads:
            if len(upload) > 1:
                multi += 1
                split += len({number for number, _row in upload}) > 1
    return multi, split


def measure(opened):
    """The numbers one picker open is judged by, as a dict."""
    from resources.lib.upload_count import count_copies

    filtered, groups = opened.filtered, opened.groups
    groupless = sum(1 for row in filtered if not row["_meta"].get("group"))
    listed, split = split_uploads(groups)
    return {
        "rows": len(opened.rows),
        "shown": len(filtered),
        "picker_rows": len(groups),
        "groupless_rows": groupless,
        "groupless_picker_rows": sum(
            1 for group in groups if not group[0]["_meta"].get("group")
        ),
        "uploads_listed_twice": listed,
        "uploads_split": split,
        "multi_copy_rows": sum(1 for group in groups if count_copies(group) > 1),
    }


def total(measures):
    """Sum a list of ``measure`` dicts."""
    summed = collections.Counter()
    for one in measures:
        summed.update(one)
    return dict(summed)


def timed_open(search):
    """``(opened, first-open ms, cached-open ms)`` for one search.

    The cached open starts from rows that already carry their parses, as a
    search read back from the cache does.
    """
    started = time.perf_counter()
    opened = open_picker(search)
    first = (time.perf_counter() - started) * 1000.0
    best = None
    for _ in range(3):
        rows = [
            {key: value for key, value in row.items() if key in _CACHED_KEYS}
            for row in opened.rows
        ]
        started = time.perf_counter()
        open_picker(search, rows)
        spent = (time.perf_counter() - started) * 1000.0
        best = spent if best is None else min(best, spent)
    return opened, first, best


_CACHED_KEYS = frozenset(
    ("title", "size", "pubdate", "indexer", "link", "usenetdate", "poster", "password")
    + ("_meta",)
)


def _report():
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from kodi_mocks import install_kodi_mocks

    install_kodi_mocks()
    measures = []
    first_total = cached_total = 0.0
    print("{:<44} {:>5} {:>5} {:>6} {:>7} {:>7}".format(*_HEAD))
    for search in load():
        opened, first, cached = timed_open(search)
        one = measure(opened)
        measures.append(one)
        first_total += first
        cached_total += cached
        print(
            "{:<44} {:>5} {:>5} {:>6} {:>7.0f} {:>7.0f}".format(
                search.name[:44],
                one["rows"],
                one["shown"],
                one["picker_rows"],
                first,
                cached,
            )
        )
    for key, value in sorted(total(measures).items()):
        print("{:>8}  {}".format(value, key))
    print("first open {:.0f} ms".format(first_total))
    print("cached open {:.0f} ms".format(cached_total))


_HEAD = ("search", "rows", "shown", "picker", "first", "cached")

if __name__ == "__main__":
    _report()
