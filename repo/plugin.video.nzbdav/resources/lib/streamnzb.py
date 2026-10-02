# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""StreamNZB's token-scoped Stremio API, without NZB-DAV submission logic.

StreamEntry contains only returned display text, the exact HTTP URL, and
request headers from Stremio behaviorHints.proxyHeaders.request (if supplied).
Entries without a direct HTTP URL, including externalUrl diagnostics, are
excluded. Order is the server's order; no local ranking or metadata guessing.
"""

import json
import re
from dataclasses import dataclass, field
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit

from resources.lib.http_util import http_get, redact_text

_TIMEOUT = 35  # Server searches have a 30-second deadline.
_MAX_RESPONSE = 4 * 1024 * 1024


class StreamNZBError(ValueError):
    """A safe, actionable error; never includes upstream exception text."""


class StreamNZBCancelled(Exception):
    """Kodi shutdown or user cancellation before a playback handoff."""


@dataclass
class StreamEntry:
    """A directly playable server result; credentials excluded from repr."""

    name: str
    description: str
    url: str = field(repr=False)
    headers: dict = field(default_factory=dict, repr=False)


def safe_display(text, token=""):
    """Redact secrets and make upstream Kodi markup inert."""
    value = text if isinstance(text, str) else ""
    if token:
        for secret in (token, quote(token, safe="")):
            value = value.replace(secret, "REDACTED")
    value = redact_text(value)
    # StreamNZB's token is a PATH credential, unlike Newznab query keys.
    value = re.sub(
        r"(https?://[^\s/]+(?:/[^\s/]+)*)/[^\s/]+/(stream|play|next|meta|catalog)/",
        r"\1/REDACTED/\2/",
        value,
    )
    return value.replace("[", "（").replace("]", "）").replace("\r", "")


def normalize_base_url(value):
    """Accept a server base (including a reverse-proxy prefix), not a manifest."""
    value = value.strip() if isinstance(value, str) else ""
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in ("http", "https")
            and parts.hostname
            and parts.port != 0
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and not any(c.isspace() for c in value)
            and not parts.path.endswith(".json")
            and "|" not in value
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise StreamNZBError("Configure a valid StreamNZB HTTP server/base URL.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _positive_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and int(value) > 0


def content_identity(params):
    """Return (Stremio type, ID), using SHOW ids and canonical episode numbers.

    TMDBHelper supplies season/episode plus ep_* aliases. Preserve the existing
    precedence and season zero specials. Anime supplied as a show uses these
    same numbers; do not reinterpret them as Kitsu entry-relative numbering.
    """
    kind = params.get("type", "movie")
    if kind not in ("movie", "episode", "series"):
        raise StreamNZBError("StreamNZB needs a movie or series episode identity.")
    imdb = params.get("imdb", "")
    identity = (
        imdb if isinstance(imdb, str) and re.fullmatch(r"tt[0-9]{7,9}", imdb) else ""
    )
    for key, prefix in (("tmdb_id", "tmdb"), ("tvdb", "tvdb")):
        if not identity and _positive_id(params.get(key, "")):
            identity = prefix + ":" + params[key]
    if not identity:
        raise StreamNZBError(
            "StreamNZB needs an IMDb, TMDB or TVDB ID from TMDBHelper."
        )
    if kind == "movie":
        return "movie", identity
    season = params.get("season", "") or params.get("ep_season", "")
    episode = params.get("episode", "") or params.get("ep_episode", "")
    if not (
        isinstance(season, str)
        and re.fullmatch(r"[0-9]+", season)
        and isinstance(episode, str)
        and re.fullmatch(r"[0-9]+", episode)
    ):
        raise StreamNZBError("StreamNZB needs the show's season and episode numbers.")
    return "series", "{}:{}:{}".format(identity, int(season), int(episode))


def _playable_url(value):
    if (
        not isinstance(value, str)
        or not value
        or any(c.isspace() for c in value)
        or "|" in value
    ):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme in ("http", "https")
            and bool(parts.hostname)
            and parts.port != 0
        )
    except ValueError:
        return False


def _request_headers(hints):
    proxy = hints.get("proxyHeaders", {})
    if not isinstance(proxy, dict):
        return None
    headers = proxy.get("request", {})
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if not isinstance(key, str) or not re.fullmatch(
            r"[!#$%&'*+.^_`~0-9A-Za-z-]+", key
        ):
            return None
        if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
            return None
    return dict(headers)


def parse_streams(payload, token=""):
    """Validate the top-level schema; omit unsupported/malformed individual rows."""
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise StreamNZBError("StreamNZB returned an invalid stream response.")
    entries = []
    for row in payload["streams"]:
        if not isinstance(row, dict) or not _playable_url(row.get("url")):
            continue
        hints = row.get("behaviorHints", {})
        if hints is None:
            hints = {}
        if not isinstance(hints, dict):
            continue
        # Advanced search diagnostics can use a real slot URL even when there
        # are no releases; source marks these separately from playable rows.
        if hints.get("bingeGroup") == "streamnzb-debug":
            continue
        headers = _request_headers(hints)
        if headers is None:
            continue
        name = safe_display(row.get("name"), token) or "StreamNZB"
        description = safe_display(row.get("description") or row.get("title"), token)
        entries.append(StreamEntry(name, description, row["url"], headers))
    return entries


def fetch_streams(base_url, token, params, cancelled=None):
    """Fetch releases with bounded I/O and shutdown checks before/after I/O.

    No worker is started. An in-flight urllib request finishes or reaches its
    finite timeout; cancellation prevents the subsequent dialog/handoff.
    """
    base = normalize_base_url(base_url)
    if not isinstance(token, str) or not token.strip():
        raise StreamNZBError(
            "Configure the StreamNZB stream token (not admin credentials)."
        )
    content_type, identity = content_identity(params)
    url = "{}/{}/stream/{}/{}.json".format(
        base, quote(token.strip(), safe=""), content_type, quote(identity, safe="")
    )
    if cancelled and cancelled():
        raise StreamNZBCancelled()
    try:
        body = http_get(url, timeout=_TIMEOUT, max_bytes=_MAX_RESPONSE)
    except HTTPError as error:
        if error.code in (401, 403):
            raise StreamNZBError(
                "StreamNZB rejected the stream token. Check its stream settings."
            ) from None
        raise StreamNZBError(
            "StreamNZB HTTP request failed (status {}).".format(error.code)
        ) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise StreamNZBError(
            "StreamNZB request failed or timed out. "
            "Check the server URL and connectivity."
        ) from None
    if cancelled and cancelled():
        raise StreamNZBCancelled()
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise StreamNZBError("StreamNZB returned malformed JSON.") from None
    return parse_streams(payload, token.strip())
