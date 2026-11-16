# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""How each of NZBHydra2's indexers answered a search the add-on just ran.

Hydra's Newznab answer is the merged rows only: it does not say which
indexers were asked, which failed, or why. Hydra records that per search in
its history, so right after a search the add-on reads its own entry back:

* ``POST /internalapi/history/searches`` for the newest searches, then
* ``GET /internalapi/history/searches/details/{id}`` for that search's
  indexers: name, success, result count, response time and error.

This is read only while the add-on's own search progress window is showing
its indexer table (``search_progress``), and not after a cancel. It costs two
or three small local requests with short timeouts, sends no indexer a request,
and fails soft: a Hydra with its history turned off, or one that requires a
sign-in for its web interface, simply yields nothing and the window counts
the result rows instead.

Hydra logs a search when the request arrives and saves its indexer rows
before it answers, so the entry is complete by the time it is read. The
entry is told apart from other clients' searches by its source, IDs or
query, season and episode, and by being logged when this search was sent.
"""

import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import xbmc

from resources.lib import search_progress
from resources.lib.http_util import HTTP_USER_AGENT, redact_text

# The requests are to a Hydra that answered a search a moment ago.
_TIMEOUT_SECONDS = 3
# How long the search waits for the record before it carries on without it.
_WAIT_SECONDS = 1.5
# How many of the newest searches are looked through for the add-on's own.
_RECENT_SEARCHES = 25
# A search is ours when Hydra logged it this close to when it was sent, by
# Hydra's own clock (its answer's Date header). Without that header the
# device's clock is used, which may differ from Hydra's.
_SLACK_SECONDS = 5
_SLACK_SECONDS_LOCAL_CLOCK = 30
# A history page or search record is a few kilobytes; more is not Hydra.
_MAX_REPLY_BYTES = 2 * 1024 * 1024
# Hydra names known clients from their user agent. These never are the add-on.
_OTHER_CLIENTS = frozenset(
    ("sonarr", "radarr", "lidarr", "readarr", "mylar", "lazylibrarian", "prowlarr")
)
_XSRF_COOKIE = "HYDRA-XSRF-TOKEN"
_ALLOWED_SCHEMES = frozenset(("http", "https"))
# The Newznab id parameters, and the identifier keys Hydra stores them under.
_ID_KEYS = {
    "imdbid": ("IMDB", "TVIMDB"),
    "tvdbid": ("TVDB",),
    "tmdbid": ("TMDB",),
    "rid": ("TVRAGE",),
    "tvmazeid": ("TVMAZE",),
}


def _digits(value):
    return "".join(char for char in str(value or "") if char.isdigit()).lstrip("0")


def _epoch(value):
    """Seconds since 1970 from Hydra's time field (text or number), or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Milliseconds when too large to be seconds.
        return value / 1000.0 if value > 1e11 else float(value)
    if not isinstance(value, str) or "T" not in value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    head, dot, rest = text.partition(".")
    if dot:
        # Python 3.8 reads a fraction of exactly three or six digits only.
        digits = len(rest) - len(rest.lstrip("0123456789"))
        fraction = rest[:digits][:6].ljust(6, "0")
        text = (
            "{}.{}{}".format(head, fraction, rest[digits:]) if digits else head + rest
        )
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _server_time(headers):
    """The ``Date`` of a Hydra answer as seconds since 1970, or None."""
    try:
        return parsedate_to_datetime(headers.get("Date")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def _xsrf_token(error):
    headers = getattr(error, "headers", None)
    cookies = headers.get_all("Set-Cookie") if headers is not None else None
    for cookie in cookies or ():
        name, _, rest = str(cookie).partition("=")
        if name.strip() == _XSRF_COOKIE:
            return rest.split(";", 1)[0].strip()
    return ""


def _open_json(request):
    """Send ``request``; return ``(decoded JSON, server time or None)``."""
    if urlsplit(request.full_url).scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("unsupported URL scheme")
    # nosemgrep
    with urlopen(  # nosec B310 - scheme allowlist enforced above
        request, timeout=_TIMEOUT_SECONDS
    ) as response:
        body = response.read(_MAX_REPLY_BYTES + 1)
        if len(body) > _MAX_REPLY_BYTES:
            raise ValueError("reply too large")
        return json.loads(body.decode("utf-8")), _server_time(response.headers)


def _post_json(url, payload):
    """POST ``payload`` to Hydra's internal API, echoing its XSRF token.

    Hydra refuses a first POST with 403 and a ``HYDRA-XSRF-TOKEN`` cookie,
    signed in or not; the request is sent again once with that token, the
    way its web interface does.
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": HTTP_USER_AGENT}
    try:
        return _open_json(Request(url, data=body, headers=headers, method="POST"))
    except HTTPError as error:
        token = _xsrf_token(error)
        if error.code != 403 or not token:
            raise
    headers["Cookie"] = "{}={}".format(_XSRF_COOKIE, token)
    headers["X-XSRF-TOKEN"] = token
    return _open_json(Request(url, data=body, headers=headers, method="POST"))


def _same_number(ours, theirs):
    return _digits(ours) == _digits(theirs)


def _is_ours(entry, params):
    """Whether a history entry is the search ``params`` describes."""
    if not isinstance(entry, dict) or entry.get("source") != "API":
        return False
    if str(entry.get("userAgent") or "").strip().casefold() in _OTHER_CLIENTS:
        return False
    for ours, theirs in (("season", "season"), ("ep", "episode")):
        if params.get(ours) not in (None, "") and not _same_number(
            params[ours], entry.get(theirs)
        ):
            return False
    stored = {}
    for pair in entry.get("identifiers") or ():
        if isinstance(pair, dict):
            stored[str(pair.get("identifierKey") or "").upper()] = _digits(
                pair.get("identifierValue")
            )
    asked_by_id = False
    for name, keys in _ID_KEYS.items():
        wanted = _digits(params.get(name))
        if not wanted:
            continue
        asked_by_id = True
        if any(stored.get(key) == wanted for key in keys):
            return True
    query = str(params.get("q") or "").strip().casefold()
    if query:
        return str(entry.get("query") or "").strip().casefold() == query
    return not asked_by_id


def _find_search(entries, params, seconds, now, slack=_SLACK_SECONDS):
    """The id of the newest history entry that is this search, or None.

    Hydra logged it when the request arrived, ``seconds`` before the search
    ended; ``now`` is a moment after that, by Hydra's clock when known.
    """
    expected = max(0.0, seconds)
    for entry in entries:
        if not _is_ours(entry, params):
            continue
        logged = _epoch(entry.get("time"))
        if logged is None:
            continue
        if abs((now - logged) - expected) <= slack:
            return entry.get("id")
    return None


def _merge(rows):
    """One entry per indexer from Hydra's rows (it may ask one more than once)."""
    merged = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        name = str(row.get("indexerName") or "").strip()
        if not name:
            continue
        count = row.get("resultsCount")
        took = row.get("responseTime")
        entry = merged.setdefault(
            name, {"name": name, "ok": True, "count": 0, "ms": 0, "error": ""}
        )
        entry["count"] += count if isinstance(count, int) and count > 0 else 0
        entry["ms"] += took if isinstance(took, int) and took > 0 else 0
        if not row.get("successful", True):
            entry["ok"] = False
            entry["error"] = str(row.get("errorMessage") or entry["error"] or "")
    return list(merged.values())


def indexer_searches(base_url, params, seconds):
    """Hydra's per-indexer record of the search just run, or ``[]``.

    ``params`` are the Newznab parameters that were sent and ``seconds`` how
    long the search took. Each entry is a dict of ``name``, ``ok``,
    ``count``, ``ms`` and ``error``. Raises on a request failure.
    """
    base_url = str(base_url or "").rstrip("/")
    page, server_now = _post_json(
        "{}/internalapi/history/searches".format(base_url),
        {
            "page": 1,
            "limit": _RECENT_SEARCHES,
            "sortModel": {"column": "time", "sortMode": 2},
        },
    )
    entries = page.get("content") if isinstance(page, dict) else None
    if not isinstance(entries, list):
        return []
    if server_now is not None:
        now, slack = server_now, _SLACK_SECONDS
    else:
        now, slack = time.time(), _SLACK_SECONDS_LOCAL_CLOCK
    search_id = _find_search(entries, params or {}, seconds, now, slack)
    if not isinstance(search_id, int) or isinstance(search_id, bool):
        return []
    details, _ = _open_json(
        Request(
            "{}/internalapi/history/searches/details/{}".format(base_url, search_id),
            headers={"User-Agent": HTTP_USER_AGENT},
        )
    )
    if not isinstance(details, dict):
        return []
    return _merge(details.get("indexerSearches"))


def report_indexers(base_url, params, seconds, wait=_WAIT_SECONDS):
    """Tell the search progress window how Hydra's indexers answered.

    Does nothing, and asks Hydra nothing, unless the add-on's window is
    showing its indexer table and the search was not cancelled. The record
    is read on a daemon thread and this waits at most ``wait`` seconds for
    it, so a slow Hydra never holds up the search's results: a later answer
    is dropped by the window. Never raises.
    """
    session = search_progress.active()
    if session is None or not session.detailed or session.cancelled():
        return
    reader = threading.Thread(
        target=_read_and_report,
        args=(base_url, params, seconds),
        name="nzbdav-hydra-history",
        daemon=True,
    )
    try:
        reader.start()
        reader.join(max(0.0, wait))
    except Exception:  # pylint: disable=broad-except
        return


def _read_and_report(base_url, params, seconds):
    """Read Hydra's record of the search and report it; never raises."""
    try:
        entries = indexer_searches(base_url, params, seconds)
    except Exception as error:  # pylint: disable=broad-except
        xbmc.log(
            "{}: Hydra's per-indexer search record could not be read: {}".format(
                search_progress.LOG_TAG, redact_text(str(error))
            ),
            xbmc.LOGDEBUG,
        )
        return
    if not entries:
        return
    for entry in entries:
        if not entry["ok"]:
            xbmc.log(
                "{}: Hydra indexer {} did not answer the search: {}".format(
                    search_progress.LOG_TAG,
                    entry["name"],
                    search_progress.short_error(entry["error"]) or "no reason given",
                ),
                xbmc.LOGINFO,
            )
    search_progress.indexer_report(entries)
