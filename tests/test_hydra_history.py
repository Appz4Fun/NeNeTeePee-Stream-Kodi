# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Reading back how Hydra's indexers answered the add-on's own search."""

import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest
from resources.lib import hydra_history
from resources.lib import search_progress as sp

_DATE = "Mon, 16 Nov 2026 07:19:10 GMT"
_LOGGED = "2026-11-16T07:19:07.676761Z"


class _Response(io.BytesIO):
    def __init__(self, payload, date=_DATE):
        super().__init__(json.dumps(payload).encode())
        self.headers = Message()
        if date:
            self.headers["Date"] = date

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _refused(token="tok-1"):
    headers = Message()
    if token:
        headers["Set-Cookie"] = "HYDRA-XSRF-TOKEN={}; Path=/".format(token)
    return HTTPError("http://h/internalapi", 403, "", headers, io.BytesIO())


def _entry(**changes):
    entry = {
        "id": 383,
        "source": "API",
        "searchType": "TVSEARCH",
        "time": _LOGGED,
        "identifiers": [
            {"identifierKey": "TVDB", "identifierValue": "278518"},
            {"identifierKey": "TVIMDB", "identifierValue": "tt3530232"},
        ],
        "query": "Some Show",
        "season": 13,
        "episode": "30",
    }
    entry.update(changes)
    return entry


_DETAILS = {
    "source": "API",
    "indexerSearches": [
        {
            "indexerName": "Alpha",
            "successful": True,
            "resultsCount": 10,
            "responseTime": 400,
            "errorMessage": None,
        },
        {
            "indexerName": "Alpha",
            "successful": True,
            "resultsCount": 2,
            "responseTime": 100,
            "errorMessage": None,
        },
        {
            "indexerName": "Beta",
            "successful": False,
            "resultsCount": 0,
            "responseTime": 0,
            "errorMessage": "Server returned: No route to host",
        },
        {"indexerName": "", "successful": True},
        "junk",
    ],
}

_PARAMS = {"t": "tvsearch", "tvdbid": "278518", "season": "13", "ep": "30"}


def _serve(*answers):
    """A urlopen that gives ``answers`` in turn and records the requests."""
    seen = []
    queue = list(answers)

    def _urlopen(request, timeout=None):
        seen.append((request, timeout))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return seen, _urlopen


def _shown():
    """An active session whose window shows the indexer table."""
    session = sp.Session()
    session.detailed = True
    return sp.activate(session)


@pytest.fixture(autouse=True)
def _no_active_session():
    sp.deactivate()
    yield
    sp.deactivate()


def test_the_searchs_indexers_are_read_with_the_xsrf_token():
    seen, urlopen = _serve(
        _refused(), _Response({"content": [_entry()]}), _Response(_DETAILS)
    )
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        entries = hydra_history.indexer_searches("http://h/", _PARAMS, 3.0)

    assert entries == [
        {"name": "Alpha", "ok": True, "count": 12, "ms": 500, "error": ""},
        {
            "name": "Beta",
            "ok": False,
            "count": 0,
            "ms": 0,
            "error": "Server returned: No route to host",
        },
    ]
    first, second, third = [request for request, _timeout in seen]
    assert first.full_url == "http://h/internalapi/history/searches"
    assert first.get_method() == "POST"
    assert json.loads(first.data)["sortModel"] == {"column": "time", "sortMode": 2}
    assert "X-xsrf-token" not in dict(first.header_items())
    assert dict(second.header_items())["X-xsrf-token"] == "tok-1"
    assert dict(second.header_items())["Cookie"] == "HYDRA-XSRF-TOKEN=tok-1"
    assert third.full_url == "http://h/internalapi/history/searches/details/383"
    assert third.get_method() == "GET"
    assert all(timeout == 3 for _request, timeout in seen)


def test_a_hydra_that_needs_no_token_is_asked_once():
    seen, urlopen = _serve(_Response({"content": [_entry()]}), _Response(_DETAILS))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        assert len(hydra_history.indexer_searches("http://h", _PARAMS, 1.0)) == 2
    assert len(seen) == 2


@pytest.mark.parametrize(
    "changes, params",
    [
        ({"source": "INTERNAL"}, _PARAMS),
        ({"season": 12}, _PARAMS),
        ({"episode": "31"}, _PARAMS),
        ({"identifiers": [{"identifierKey": "TVDB", "identifierValue": "1"}]}, _PARAMS),
        ({"query": "Other Show"}, {"t": "search", "q": "Some Show S13E30"}),
        ({"time": "2026-11-16T07:10:00Z"}, _PARAMS),
        ({"time": "2026-11-16T07:25:00Z"}, _PARAMS),
        ({"time": None}, _PARAMS),
        ({"id": "383"}, _PARAMS),
    ],
)
def test_another_clients_or_an_older_search_is_not_taken(changes, params):
    seen, urlopen = _serve(_Response({"content": [_entry(**changes), "junk"]}))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        assert not hydra_history.indexer_searches("http://h", params, 3.0)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "entry, params",
    [
        (_entry(), {"imdbid": "3530232", "season": "13", "ep": "30"}),
        (_entry(), {"imdbid": "tt3530232", "season": 13, "ep": "030"}),
        (_entry(identifiers=[]), {"q": "some show", "season": "13", "ep": "30"}),
        (_entry(identifiers=[], time=1794813547.6), {"q": "Some Show"}),
        (_entry(identifiers=[], time=1794813547600), {"q": "Some Show"}),
        (_entry(season=None, episode=None), {"t": "tvsearch"}),
    ],
)
def test_the_search_is_recognised_by_id_or_by_query(entry, params):
    _seen, urlopen = _serve(_Response({"content": [entry]}), _Response(_DETAILS))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        assert len(hydra_history.indexer_searches("http://h", params, 3.0)) == 2


def test_the_newest_matching_search_wins():
    newest = _entry(id=400, time="2026-11-16T07:19:09Z")
    seen, urlopen = _serve(
        _Response({"content": [newest, _entry()]}), _Response(_DETAILS)
    )
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        hydra_history.indexer_searches("http://h", _PARAMS, 3.0)
    assert seen[1][0].full_url.endswith("/details/400")


def test_without_a_date_header_the_local_clock_is_used():
    _seen, urlopen = _serve(
        _Response({"content": [_entry()]}, date=None), _Response(_DETAILS)
    )
    with patch.object(hydra_history, "urlopen", side_effect=urlopen), patch.object(
        hydra_history.time, "time", return_value=1794813550.0
    ):
        assert len(hydra_history.indexer_searches("http://h", _PARAMS, 3.0)) == 2


def test_odd_answers_yield_nothing():
    for page in ([], {"content": "x"}, {}):
        _seen, urlopen = _serve(_Response(page))
        with patch.object(hydra_history, "urlopen", side_effect=urlopen):
            assert not hydra_history.indexer_searches("http://h", _PARAMS, 3.0)
    _seen, urlopen = _serve(_Response({"content": [_entry()]}), _Response(["x"]))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        assert not hydra_history.indexer_searches("http://h", _PARAMS, 3.0)


def test_only_http_urls_are_opened():
    with patch.object(hydra_history, "urlopen") as urlopen:
        with pytest.raises(ValueError):
            hydra_history.indexer_searches("file:///etc", _PARAMS, 1.0)
    urlopen.assert_not_called()


def test_nothing_is_asked_when_no_table_is_being_shown():
    with patch.object(hydra_history, "urlopen") as urlopen:
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
        sp.activate(sp.Session())  # the corner indicator only
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
        _shown().cancel()
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
    urlopen.assert_not_called()


def test_any_failure_reading_the_record_is_swallowed():
    import http.client

    session = _shown()
    with patch.object(
        hydra_history, "_post_json", side_effect=http.client.IncompleteRead(b"")
    ):
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
    assert session.snapshot()["indexers"] == []


def test_an_oversized_reply_is_refused():
    big = _Response({"content": [_entry(query="x" * (3 * 1024 * 1024))]})
    _seen, urlopen = _serve(big)
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        with pytest.raises(ValueError):
            hydra_history.indexer_searches("http://h", _PARAMS, 3.0)


def test_the_report_reaches_the_progress_window_and_logs_failures():
    import xbmc

    session = _shown()
    _seen, urlopen = _serve(
        _refused(), _Response({"content": [_entry()]}), _Response(_DETAILS)
    )
    xbmc.log.reset_mock()
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        hydra_history.report_indexers("http://h", _PARAMS, 3.0)

    rows = {row["name"]: row for row in session.snapshot()["indexers"]}
    assert rows["Alpha"]["result"] == "12 results · 0.5 s"
    assert rows["Beta"]["result"] == "failed: No route to host"
    assert any(
        "Hydra indexer Beta did not answer the search: No route to host" in call.args[0]
        for call in xbmc.log.call_args_list
    )


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError("http://h", 401, "", Message(), io.BytesIO()),
        _refused(token=""),
        URLError("refused"),
        TimeoutError("timed out"),
        ValueError("not JSON"),
    ],
)
def test_a_hydra_that_will_not_say_fails_soft(failure):
    session = _shown()
    _seen, urlopen = _serve(failure)
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)

    assert session.snapshot()["indexers"] == []


def test_a_second_refusal_is_not_retried_again():
    _shown()
    seen, urlopen = _serve(_refused(), _refused())
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
    assert len(seen) == 2


def test_no_matching_search_reports_nothing():
    session = _shown()
    _seen, urlopen = _serve(_Response({"content": []}))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        hydra_history.report_indexers("http://h", _PARAMS, 1.0)
    assert session.snapshot()["indexers"] == []


def test_a_sonarr_search_for_the_same_episode_is_not_taken():
    entry = _entry(userAgent="Sonarr")
    seen, urlopen = _serve(_Response({"content": [entry]}))
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        assert not hydra_history.indexer_searches("http://h", _PARAMS, 3.0)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "logged, found",
    [
        ("2026-11-16T07:19:07Z", True),
        ("2026-11-16T07:19:07.123456789Z", True),
        ("2026-11-16T07:19:07+00:00", True),
        ("2026-11-16T08:19:07+01:00", True),
        ("2026-11-16T07:19:07", True),
        # Logged 10 s before the 3 s search ended: an earlier search.
        ("2026-11-16T07:19:00Z", False),
        # Logged as the history was read, not when this search was sent.
        ("2026-11-16T07:19:10Z", True),
        ("2026-11-16T07:19:16Z", False),
        ("not a time", False),
    ],
)
def test_the_entry_is_the_one_logged_when_the_search_was_sent(logged, found):
    # The search took 3 s and ended at Hydra's 07:19:10.
    seen, urlopen = _serve(
        _Response({"content": [_entry(time=logged)]}), _Response(_DETAILS)
    )
    with patch.object(hydra_history, "urlopen", side_effect=urlopen):
        entries = hydra_history.indexer_searches("http://h", _PARAMS, 3.0)
    assert bool(entries) is found


def test_a_slow_hydra_never_holds_up_the_search():
    import threading
    import time as clock

    session = _shown()
    release = threading.Event()

    def _slow(*_args, **_kwargs):
        release.wait(5)
        return [{"name": "Alpha", "ok": True, "count": 1, "ms": 1, "error": ""}]

    with patch.object(hydra_history, "indexer_searches", side_effect=_slow):
        started = clock.monotonic()
        hydra_history.report_indexers("http://h", _PARAMS, 1.0, wait=0.2)
        took = clock.monotonic() - started
        # The search is over before the late answer comes: it is dropped.
        session.search_finished([])
        release.set()
        for thread in threading.enumerate():
            if thread.name == "nzbdav-hydra-history":
                thread.join(5)

    assert took < 1.0
    assert session.snapshot()["indexers"] == []


@pytest.mark.parametrize("fraction", [".1", ".12", ".1234", ".123456789"])
def test_any_length_of_fraction_is_read(fraction):
    assert hydra_history._epoch("2026-11-16T07:19:07{}Z".format(fraction)) == (
        pytest.approx(1794813547 + float("0" + fraction), abs=1e-6)
    )


def test_an_indexer_with_one_answer_among_retries_is_not_failed():
    rows = [
        {"indexerName": "Alpha", "successful": True, "resultsCount": 100},
        {"indexerName": "Alpha", "successful": False, "errorMessage": "Read timed out"},
        {"indexerName": "Beta", "successful": False, "errorMessage": "403"},
        {"indexerName": "Beta", "successful": False, "errorMessage": "403 again"},
    ]
    merged = {entry["name"]: entry for entry in hydra_history._merge(rows)}

    assert merged["Alpha"]["ok"] is True
    assert merged["Alpha"]["count"] == 100
    assert merged["Alpha"]["error"] == ""
    assert merged["Beta"]["ok"] is False
    assert merged["Beta"]["error"] == "403 again"
