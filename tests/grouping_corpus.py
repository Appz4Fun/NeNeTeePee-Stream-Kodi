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

    A row joins the first group whose every member the rule accepts it
    against (``release_match.same_release``), and a season pack never
    groups. No keys, buckets or caches: this is what any faster grouping
    has to equal.
    """
    from resources.lib.release_match import same_release

    groups = []
    for row in rows:
        if not row.get("_season_pack"):
            for group in groups:
                if all(
                    not member.get("_season_pack") and same_release(member, row)
                    for member in group
                ):
                    group.append(row)
                    break
            else:
                groups.append([row])
        else:
            groups.append([row])
    return groups


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
