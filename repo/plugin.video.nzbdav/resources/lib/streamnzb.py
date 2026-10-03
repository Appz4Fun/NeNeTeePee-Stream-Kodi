# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""StreamNZB's token-scoped API for NeNeTeePee-Stream-Kodi.

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
    size: int = 0


def safe_display(text, token=None):
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


def _valid_base_parts(parts):
    return all(
        (
            parts.scheme in ("http", "https"),
            bool(parts.hostname),
            parts.port != 0,
            not parts.username,
            not parts.password,
            not parts.query,
            not parts.fragment,
            not parts.path.endswith(".json"),
        )
    )


def _clean_url_text(value):
    return (
        isinstance(value, str)
        and bool(value)
        and not any(c.isspace() or c == "|" for c in value)
    )


def normalize_base_url(value):
    """Accept a server base (including a reverse-proxy prefix), not a manifest."""
    value = value.strip() if isinstance(value, str) else ""
    try:
        parts = urlsplit(value)
        valid = _valid_base_parts(parts) and _clean_url_text(value)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise StreamNZBError("Configure a valid StreamNZB HTTP server/base URL.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _positive_id(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and int(value) > 0


def _stable_identity(params):
    imdb = params.get("imdb", "")
    if isinstance(imdb, str) and re.fullmatch(r"tt[0-9]{7,9}", imdb):
        return imdb
    for key, prefix in (("tmdb_id", "tmdb"), ("tvdb", "tvdb")):
        if _positive_id(params.get(key, "")):
            return prefix + ":" + params[key]
    raise StreamNZBError("StreamNZB needs an IMDb, TMDB or TVDB ID from TMDBHelper.")


def _episode_coordinate(params, key):
    value = params.get(key, "") or params.get("ep_" + key, "")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise StreamNZBError("StreamNZB needs the show's season and episode numbers.")
    return int(value)


def content_identity(params):
    """Return (Stremio type, ID), using SHOW ids and canonical episode numbers.

    TMDBHelper supplies season/episode plus ep_* aliases. Preserve the existing
    precedence and season zero specials. Anime supplied as a show uses these
    same numbers; do not reinterpret them as Kitsu entry-relative numbering.
    """
    kind = params.get("type", "movie")
    if kind not in ("movie", "episode", "series"):
        raise StreamNZBError("StreamNZB needs a movie or series episode identity.")
    identity = _stable_identity(params)
    if kind == "movie":
        return "movie", identity
    return "series", "{}:{}:{}".format(
        identity,
        _episode_coordinate(params, "season"),
        _episode_coordinate(params, "episode"),
    )


def _playable_url(value):
    if not _clean_url_text(value):
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


def _valid_header(key, value):
    return (
        isinstance(key, str)
        and bool(re.fullmatch(r"[!#$%&'*+.^_`~0-9A-Za-z-]+", key))
        and isinstance(value, str)
        and not any(c in value for c in "\r\n\x00")
    )


def _request_headers(hints):
    proxy = hints.get("proxyHeaders", {})
    if not isinstance(proxy, dict):
        return None
    headers = proxy.get("request", {})
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if not _valid_header(key, value):
            return None
    return dict(headers)


def _playable_hints(row):
    hints = row.get("behaviorHints", {})
    if hints is None:
        hints = {}
    if not isinstance(hints, dict):
        return None
    # Advanced search diagnostics can use a real slot URL even when there
    # are no releases; source marks these separately from playable rows.
    if hints.get("bingeGroup") == "streamnzb-debug":
        return None
    return hints


def _parse_entry(row, token):
    if not isinstance(row, dict) or not _playable_url(row.get("url")):
        return None
    hints = _playable_hints(row)
    if hints is None:
        return None
    headers = _request_headers(hints)
    if headers is None:
        return None
    name = (
        safe_display(hints.get("filename"), token).strip()
        or safe_display(row.get("name"), token)
        or "StreamNZB"
    )
    description = safe_display(row.get("description") or row.get("title"), token)
    size = hints.get("videoSize", 0)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        size = 0
    return StreamEntry(name, description, row["url"], headers, size)


def parse_streams(payload, token=None):
    """Validate the top-level schema; omit unsupported/malformed individual rows."""
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise StreamNZBError("StreamNZB returned an invalid stream response.")
    entries = []
    for row in payload["streams"]:
        entry = _parse_entry(row, token)
        if entry is not None:
            entries.append(entry)
    return entries


def _request_body(url):
    try:
        return http_get(url, timeout=_TIMEOUT, max_bytes=_MAX_RESPONSE)
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


def _decode_payload(body):
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        raise StreamNZBError("StreamNZB returned malformed JSON.") from None


def _check_cancelled(cancelled):
    if cancelled and cancelled():
        raise StreamNZBCancelled()


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
    _check_cancelled(cancelled)
    body = _request_body(url)
    _check_cancelled(cancelled)
    return parse_streams(_decode_payload(body), token.strip())
