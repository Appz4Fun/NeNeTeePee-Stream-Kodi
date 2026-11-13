"""Backup NZB collection against a real local HTTP server.

Nothing in the fetch path is stubbed here: the play's real fetcher downloads
over HTTP from a server on 127.0.0.1 that answers per "indexer" the way real
ones do (an NZB, 403, 429, 400, or an HTML page with status 200).
"""

import http.server
import importlib
import threading
from unittest.mock import patch

import pytest
from resources.lib.nzbget_fleet_dedup import FleetDedup, NzbSpool, prefetched_clusters


def _nzb(name):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
        '<file poster="p" date="1" subject="&quot;movie.mkv&quot; yEnc (1/1)">'
        "<groups><group>alt.binaries.test</group></groups>"
        '<segments><segment bytes="700000" number="1">{}@local</segment>'
        "</segments></file></nzb>".format(name)
    ).encode("utf-8")


# First path segment -> (status, body builder).
_INDEXERS = {
    "geek": (200, _nzb),
    "slug": (200, _nzb),
    "square": (403, lambda _name: b"Forbidden"),
    "limited": (429, lambda _name: b"Too Many Requests"),
    "finder": (400, lambda _name: b"Bad Request"),
    "loginpage": (200, lambda _name: b"<html>please sign in</html>"),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):  # pylint: disable=invalid-name
        type(self).requests.append(self.path)
        indexer, _, name = self.path.strip("/").partition("/")
        status, build = _INDEXERS[indexer]
        body = build(name)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # pylint: disable=arguments-differ
        return


@pytest.fixture(name="indexers")
def _indexers():
    _Handler.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:{}".format(server.server_address[1])

    def row(indexer, name, **extra):
        return dict(
            {"link": "{}/{}/{}".format(base, indexer, name), "indexer": indexer},
            **extra,
        )

    try:
        yield row
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _collect(phases, tmp_path):
    """Run each phase the way a play does: one FleetDedup, a fetcher per phase."""
    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib.nzbget_resolver_dupes import _fleet_fetcher

    dedup = FleetDedup(spool_base=str(tmp_path))
    spool = NzbSpool(str(tmp_path))
    results = []
    try:
        for clusters in phases:
            fetch = _fleet_fetcher(clusters, spool, blocked=dedup.blocked_indexers)
            for head, payload, fingerprint in prefetched_clusters(
                clusters, fetch, window=1
            ):
                results.append((head["indexer"], bool(payload and fingerprint)))
    finally:
        spool.close()
    return results, dedup


def _fetch_logs(log):
    return [
        call.args[0].split("NZB fetch from ", 1)[1].split(": http", 1)[0]
        for call in log.call_args_list
        if "NZB fetch from " in call.args[0]
    ]


def test_a_403_indexer_is_asked_once_per_play(indexers, tmp_path):
    phases = [
        [
            [indexers("square", "a"), indexers("geek", "a")],
            [indexers("square", "b"), indexers("slug", "b")],
        ],
        # A later phase of the same play (loader extras).
        [[indexers("square", "c"), indexers("geek", "c")], [indexers("square", "d")]],
    ]
    with patch("resources.lib.nzbget_fleet_dedup.xbmc.log") as log:
        results, dedup = _collect(phases, tmp_path)
    assert _Handler.requests == ["/square/a", "/geek/a", "/slug/b", "/geek/c"]
    assert results == [
        ("square", True),  # the head keeps its slot; geek supplied the NZB
        ("square", True),
        ("square", True),
        ("square", False),  # no other listing: this backup is dropped
    ]
    assert dedup.blocked_indexers == {"square"}
    logs = _fetch_logs(log)
    assert logs[0] == (
        "square: failed (skipping this indexer for the rest of this play), "
        "trying the next listing: HTTP Error 403: Forbidden"
    )
    assert logs[1:] == [
        "geek: ok",
        "square: skipped, this indexer refused an NZB earlier in this play",
        "slug: ok",
        "square: skipped, this indexer refused an NZB earlier in this play",
        "geek: ok",
        "square: skipped, this indexer refused an NZB earlier in this play",
    ]


def test_a_429_indexer_is_skipped_too(indexers, tmp_path):
    phases = [
        [
            [indexers("limited", "a"), indexers("geek", "a")],
            [indexers("limited", "b")],
        ]
    ]
    results, dedup = _collect(phases, tmp_path)
    assert _Handler.requests == ["/limited/a", "/geek/a"]
    assert results == [("limited", True), ("limited", False)]
    assert dedup.blocked_indexers == {"limited"}


def test_a_400_or_a_login_page_does_not_skip_the_indexer(indexers, tmp_path):
    phases = [
        [
            [indexers("finder", "a"), indexers("geek", "a")],
            [indexers("loginpage", "b"), indexers("slug", "b")],
            [indexers("finder", "c")],
            [indexers("loginpage", "d")],
        ]
    ]
    with patch("resources.lib.nzbget_fleet_dedup.xbmc.log") as log:
        results, dedup = _collect(phases, tmp_path)
    assert _Handler.requests == [
        "/finder/a",
        "/geek/a",
        "/loginpage/b",
        "/slug/b",
        "/finder/c",
        "/loginpage/d",
    ]
    assert [ok for _indexer, ok in results] == [True, True, False, False]
    assert dedup.blocked_indexers == set()
    logs = _fetch_logs(log)
    assert logs[0] == (
        "finder: failed, trying the next listing: HTTP Error 400: Bad Request"
    )
    assert logs[2] == "loginpage: no usable NZB in the reply"


def test_the_pick_is_fetched_even_from_a_refused_indexer(indexers, tmp_path):
    # "square" refuses a backup; the pick on that indexer is still requested.
    phases = [
        [[indexers("square", "a"), indexers("geek", "a")]],
        [[indexers("square", "pick", _is_pick=True)]],
    ]
    _results, dedup = _collect(phases, tmp_path)
    assert dedup.blocked_indexers == {"square"}
    assert _Handler.requests == ["/square/a", "/geek/a", "/square/pick"]


def test_the_collector_reports_what_it_kept_and_left_out(indexers, tmp_path):
    # The same collector the fleet uses, against the real server: the report
    # it leaves behind is what the dialog and the toast read.
    import threading as _threading

    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib import nzbget_progress
    from resources.lib.nzbget_resolver_dupes import _collect_unique, _fleet_fetcher

    rows = [
        indexers("geek", "a", _is_pick=True),
        indexers("square", "b"),  # refused
        indexers("slug", "c"),
        indexers("square", "d"),  # skipped: square refused already
        indexers("finder", "e"),  # HTTP 400: not downloaded
        indexers("slug", "a"),  # the same posting as the pick ("a")
    ]
    dedup = FleetDedup(spool_base=str(tmp_path))
    spool = NzbSpool(str(tmp_path))
    clusters = [[row] for row in rows]
    fetch = _fleet_fetcher(clusters, spool, blocked=dedup.blocked_indexers)
    tally = {"wanted": 50, "seen": 0, "total": len(clusters), "pick_pending": True}
    stream = prefetched_clusters(clusters, fetch, window=1)
    try:
        kept = _collect_unique(
            stream, (dedup, spool, tally, fetch), _threading.Event(), 50
        )
    finally:
        stream.close()
        spool.close()
    report = dedup.report
    assert [row["indexer"] for row, _handle, _token in kept] == ["geek", "slug"]
    assert report.sent == ["geek", "slug"]
    assert report.refused == {"square": 2}
    assert report.failed == 1
    assert report.duplicates == 1
    assert nzbget_progress.sending_message(report, len(kept)) == (
        "Sending 2 NZBs to NZBGet...[CR]geek, slug"
        "[CR]Not sent: square refused 2, 1 could not be downloaded, "
        "1 duplicate upload"
    )
    assert nzbget_progress.ready_toast(2, 100, report) == (
        "2 of 6 copies ready - health 100%"
    )


def test_a_copy_a_mirror_supplied_is_named_after_the_mirror(indexers, tmp_path):
    # One posting listed on a refusing indexer first and a working one second:
    # the head keeps the slot, but the dialog must name who delivered.
    import threading as _threading

    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib import nzbget_progress
    from resources.lib.nzbget_resolver_dupes import _collect_unique, _fleet_fetcher

    dedup = FleetDedup(spool_base=str(tmp_path))
    spool = NzbSpool(str(tmp_path))
    clusters = [
        [indexers("geek", "a", _is_pick=True)],
        [indexers("square", "b"), indexers("slug", "b")],
    ]
    fetch = _fleet_fetcher(clusters, spool, blocked=dedup.blocked_indexers)
    tally = {"wanted": 50, "seen": 0, "total": len(clusters), "pick_pending": True}
    stream = prefetched_clusters(clusters, fetch, window=1)
    try:
        kept = _collect_unique(
            stream, (dedup, spool, tally, fetch), _threading.Event(), 50
        )
    finally:
        stream.close()
        spool.close()
    # The cluster's head (its title and rank) is still the refused listing.
    assert [row["indexer"] for row, _handle, _token in kept] == ["geek", "square"]
    assert dedup.report.sent == ["geek", "slug"]
    assert nzbget_progress.sending_message(dedup.report, 2) == (
        "Sending 2 NZBs to NZBGet...[CR]geek, slug"
    )
