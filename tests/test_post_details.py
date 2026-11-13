# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The post details indexers send, and how they decide "same upload"."""

from resources.lib import direct_indexers, hydra, newznab_attrs
from resources.lib.nzbget_fleet_dedup import FleetDedup, same_listing
from resources.lib.xml_safety import safe_fromstring

_NS = 'xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"'
_POSTED = "Sat, 03 Oct 2026 12:00:00 +0000"
_POSTED_90S = "Sat, 03 Oct 2026 12:01:30 +0000"
_POSTED_1H = "Sat, 03 Oct 2026 13:00:00 +0000"
_ADDED = "Sat, 03 Oct 2026 18:30:00 +0000"
_ADDED_LATER = "Sun, 04 Oct 2026 02:10:00 +0000"


def _rss(attrs):
    extra = "".join(
        '<newznab:attr name="{}" value="{}"/>'.format(name, value)
        for name, value in attrs
    )
    return (
        '<?xml version="1.0"?><rss version="2.0" {}><channel>'
        "<item><title>Movie.2024.1080p.WEB-DL.x264-GRP</title>"
        "<link>http://hydra/getnzb/api/1</link>"
        "<pubDate>{}</pubDate>"
        '<newznab:attr name="size" value="1000"/>'
        '<newznab:attr name="hydraIndexerName" value="abNZB"/>{}'
        "</item></channel></rss>"
    ).format(_NS, _ADDED, extra)


_FULL = (
    ("usenetdate", _POSTED),
    ("poster", "poster@example.com (Poster)"),
    ("group", "alt.binaries.test"),
    ("password", "0"),
    ("files", "68"),
    ("grabs", "12"),
    ("coverurl", "http://cover"),
    ("usenetdate", "ignored: the first one wins"),
)


# --- reading them -----------------------------------------------------------


def test_hydra_results_keep_the_post_details():
    (row,) = hydra.parse_results(_rss(_FULL))
    assert row["usenetdate"] == _POSTED
    assert row["poster"] == "poster@example.com (Poster)"
    assert row["group"] == "alt.binaries.test"
    assert row["password"] == "0"
    assert row["files"] == "68" and row["grabs"] == "12"
    # The fields the picker always had are as they were.
    assert row["pubdate"] == _ADDED and row["size"] == "1000"
    assert row["indexer"] == "abNZB"
    # Nothing else an indexer sends is kept.
    assert "coverurl" not in row


def test_direct_indexer_results_keep_them_too():
    parsed = direct_indexers.parse_results(_rss(_FULL), "fallback")
    # The parser answers with the rows, or the rows and an error.
    rows = parsed[0] if isinstance(parsed, tuple) else parsed
    (row,) = rows
    assert row["usenetdate"] == _POSTED
    assert row["poster"] == "poster@example.com (Poster)"
    assert row["password"] == "0"


def test_a_result_with_no_details_is_unchanged():
    (row,) = hydra.parse_results(_rss(()))
    assert sorted(row) == ["age", "indexer", "link", "pubdate", "size", "title"]
    (row,) = hydra.parse_results(_rss((("poster", " "), ("password", ""))))
    assert "poster" not in row and "password" not in row


def test_a_long_value_is_cut():
    item = safe_fromstring(
        '<item {}><newznab:attr name="poster" value="{}"/></item>'.format(
            _NS, "x" * 5000
        )
    )
    assert len(newznab_attrs.post_details(item)["poster"]) == 256


def test_the_posters_address_is_what_is_compared():
    address = newznab_attrs.poster_address
    assert address({"poster": "Poster@Example.com (Poster)"}) == "poster@example.com"
    assert address({"poster": "Poster <poster@example.com>"}) == "poster@example.com"
    assert address({"poster": "just a name"}) == ""
    assert address({"poster": None}) == "" and address({}) == "" and address(None) == ""


def test_the_password_flag_is_known_only_for_0_and_1():
    flag = newznab_attrs.password_flag
    assert flag({"password": "0"}) is False
    assert flag({"password": 1}) is True
    for unknown in ("-1", "2", "", None, "yes"):
        assert flag({"password": unknown}) is None
    assert flag({}) is None and flag(None) is None


# --- deciding "same upload" -------------------------------------------------


def _row(pubdate=_ADDED, size="1000", **extra):
    return dict({"size": size, "pubdate": pubdate}, **extra)


def test_one_upload_added_hours_apart_is_one_upload():
    # Two indexers added it at different times; both say when it was posted.
    left = _row(pubdate=_ADDED, usenetdate=_POSTED)
    right = _row(pubdate=_ADDED_LATER, usenetdate=_POSTED_90S)
    assert same_listing(left, right)
    # Without the real post time the two dates are all there is.
    assert not same_listing(_row(pubdate=_ADDED), _row(pubdate=_ADDED_LATER))


def test_two_uploads_added_in_the_same_minute_are_two():
    left = _row(pubdate=_ADDED, usenetdate=_POSTED)
    right = _row(pubdate=_ADDED, usenetdate=_POSTED_1H)
    assert not same_listing(left, right)
    # Without the real post time they would have been taken for one.
    assert same_listing(_row(pubdate=_ADDED), _row(pubdate=_ADDED))


def test_a_repost_by_the_same_poster_stays_a_separate_upload():
    poster = "poster@example.com"
    left = _row(usenetdate=_POSTED, poster=poster, password="0")
    right = _row(usenetdate=_POSTED_1H, poster=poster, password="0")
    assert not same_listing(left, right)


def test_one_side_with_the_real_post_time_is_compared_with_the_others_date():
    # This indexer puts the post time in pubDate and sends no usenetdate.
    plain = _row(pubdate=_POSTED_90S)
    detailed = _row(pubdate=_ADDED, usenetdate=_POSTED)
    assert same_listing(plain, detailed) and same_listing(detailed, plain)
    assert not same_listing(_row(pubdate=_POSTED_1H), detailed)
    # Two add dates that agree still count, as before.
    assert same_listing(_row(pubdate=_ADDED), _row(pubdate=_ADDED, usenetdate=_POSTED))


def test_an_unreadable_post_time_falls_back_to_the_other_date():
    left = _row(pubdate=_ADDED, usenetdate="not a date")
    assert same_listing(left, _row(pubdate=_ADDED))
    assert not same_listing(left, _row(pubdate=_ADDED_LATER))
    assert same_listing(_row(usenetdate=17), _row())


def test_different_posters_are_different_uploads():
    left = _row(usenetdate=_POSTED, poster="one@example.com (One)")
    right = _row(usenetdate=_POSTED, poster="two@example.com (Two)")
    assert not same_listing(left, right)
    # The same address written two ways is one poster.
    again = _row(usenetdate=_POSTED_90S, poster="One <ONE@example.com>")
    assert same_listing(left, again)


def test_a_poster_that_is_missing_or_has_no_address_decides_nothing():
    left = _row(usenetdate=_POSTED, poster="one@example.com")
    assert same_listing(left, _row(usenetdate=_POSTED))
    assert same_listing(left, _row(usenetdate=_POSTED, poster="Anonymous"))
    assert same_listing(
        _row(usenetdate=_POSTED, poster="Name A"),
        _row(usenetdate=_POSTED, poster="Name B"),
    )


def test_a_passworded_and_an_open_release_are_different_uploads():
    assert not same_listing(
        _row(usenetdate=_POSTED, password="1"), _row(usenetdate=_POSTED, password="0")
    )
    assert same_listing(
        _row(usenetdate=_POSTED, password="1"), _row(usenetdate=_POSTED, password="1")
    )


def test_an_unknown_password_flag_decides_nothing():
    for unknown in ("-1", "2", "", None):
        assert same_listing(
            _row(usenetdate=_POSTED, password="0"),
            _row(usenetdate=_POSTED, password=unknown),
        )
    assert same_listing(_row(usenetdate=_POSTED, password="1"), _row())


def test_the_size_must_still_match():
    assert not same_listing(
        _row(usenetdate=_POSTED, size="1000"), _row(usenetdate=_POSTED, size="1001")
    )


def test_listings_group_by_the_real_post_time():
    dedup = FleetDedup()
    rows = [
        _row(pubdate=_ADDED, usenetdate=_POSTED, link="a", indexer="abNZB"),
        _row(pubdate=_ADDED_LATER, usenetdate=_POSTED, link="b", indexer="altHUB"),
        # A second upload of the same size, an hour later.
        _row(pubdate=_ADDED, usenetdate=_POSTED_1H, link="c", indexer="abNZB"),
        _row(pubdate=_ADDED, usenetdate=_POSTED_1H, link="d", indexer="miatrix"),
    ]
    clusters = dedup.clusters(rows)
    assert [[row["link"] for row in cluster] for cluster in clusters] == [
        ["a", "b"],
        ["c", "d"],
    ]
    assert dedup.report.mirrors == 2


# --- reaching the worker ----------------------------------------------------


def test_the_backup_row_carries_the_details_the_worker_compares():
    from resources.lib.router_play import _backup_row

    result = {
        "link": "http://hydra/1",
        "title": "Movie",
        "pubdate": _ADDED,
        "size": "1000",
        "indexer": "abNZB",
        "usenetdate": _POSTED,
        "poster": "poster@example.com",
        "password": "0",
        "files": "68",
        "_meta": {"x": 1},
    }
    row = _backup_row(result)
    assert row["usenetdate"] == _POSTED
    assert row["poster"] == "poster@example.com" and row["password"] == "0"
    assert "files" not in row and "_meta" not in row
    assert sorted(_backup_row({"link": "http://hydra/2"})) == [
        "indexer",
        "link",
        "pubdate",
        "size",
        "title",
    ]


def test_the_fleet_row_keeps_them():
    import importlib

    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib.nzbget_resolver_dupes import _FLEET_ROW_KEYS

    assert {"usenetdate", "poster", "password"} <= set(_FLEET_ROW_KEYS)
