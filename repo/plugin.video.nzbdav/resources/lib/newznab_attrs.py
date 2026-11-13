# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The post details an indexer sends beside a search result.

A newznab item carries more than its title, link and size. Indexers add
``newznab:attr`` elements, and NZBHydra2 passes every one of them through:

* ``usenetdate``: when the release was posted to Usenet. The item's
  ``pubDate`` is often when the indexer added it, hours or days later, and
  differs between indexers for one upload.
* ``poster``: who posted it.
* ``group``: the newsgroup.
* ``password``: 0 for none, 1 for a passworded archive, other values for
  "not known".
* ``files`` and ``grabs``: how many files the release has and how often its
  NZB was fetched.

The add-on uses the first four to tell whether two listings are the same
upload (``nzbget_fleet_dedup.same_listing``). Only the fields an indexer sent
are returned, so a result from one that sends none is unchanged.

Some also send ``nfo`` and ``info``: a flag that the release has an NFO and
the link to it (``nfo_link``, read by ``nfo_audio``).
"""

import re
from urllib.parse import urlsplit

# The row keys, which are the attribute names.
POST_DETAIL_KEYS = ("usenetdate", "poster", "group", "password", "files", "grabs")
_MAX_LENGTH = 256


def post_details(item):
    """``{key: value}`` for the post details on a newznab ``item`` element."""
    details = {}
    for attr in item.iter():
        tag = attr.tag
        if not isinstance(tag, str) or tag.rsplit("}", 1)[-1] != "attr":
            continue
        name = attr.get("name", "")
        if name not in POST_DETAIL_KEYS or name in details:
            continue
        value = str(attr.get("value", "") or "").strip()
        if value:
            details[name] = value[:_MAX_LENGTH]
    return details


def nfo_link(item):
    """The link to the release's NFO an indexer sent with ``item``, or "".

    Some indexers send ``nfo`` (1 when the release has an NFO) and ``info``
    (the call that returns it). The link carries the user's API key for that
    indexer, so it is only accepted when it points at the indexer the result
    came from (``hydraIndexerHost``, which NZBHydra2 adds): a result must not
    be able to send the add-on anywhere else.
    """
    values = {}
    for attr in item.iter():
        tag = attr.tag
        if not isinstance(tag, str) or tag.rsplit("}", 1)[-1] != "attr":
            continue
        name = attr.get("name", "")
        if name in ("nfo", "info", "hydraIndexerHost") and name not in values:
            values[name] = str(attr.get("value", "") or "").strip()
    link = values.get("info", "")
    if values.get("nfo") != "1" or not link or len(link) > 2048:
        return ""
    try:
        parts = urlsplit(link)
        host = (parts.hostname or "").lower()
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return ""
    return link if _same_site(host, values.get("hydraIndexerHost", "")) else ""


def _same_site(host, indexer_host):
    """Whether ``host`` is the indexer's host, or one under or above it."""
    indexer_host = str(indexer_host or "").strip().lower()
    if not indexer_host or "." not in indexer_host:
        return False
    return (
        host == indexer_host
        or host.endswith("." + indexer_host)
        or indexer_host.endswith("." + host)
    )


_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def poster_address(row):
    """The poster's address in lower case, or "" when the row names none.

    Indexers write one poster differently ("name@host (Name)", "Name
    <name@host>"), so only the address is compared, and a poster with no
    address in it is not compared at all.
    """
    poster = row.get("poster") if isinstance(row, dict) else None
    if not isinstance(poster, str):
        return ""
    match = _ADDRESS.search(poster)
    return match.group(0).lower() if match else ""


def password_flag(row):
    """True for a passworded release, False for one without, None if unknown."""
    value = row.get("password") if isinstance(row, dict) else None
    value = str(value).strip() if value is not None else ""
    if value == "0":
        return False
    if value == "1":
        return True
    return None
