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
* ``password``: 0 for none, 2 (or 10) for a passworded archive; 1 means
  "possibly" on some indexers and "yes" on others, and -1 "not checked".
* ``files`` and ``grabs``: how many files the release has and how often its
  NZB was fetched.

The add-on uses the first four to tell whether two listings are the same
upload (``nzbget_fleet_dedup.same_listing``). Only the fields an indexer sent
are returned, so a result from one that sends none is unchanged.

Some also send ``nfo`` and ``info``: a flag that the release has an NFO and
the link to it (``nfo_link``, read by ``nfo_audio``). What each indexer
offers, and how a result without a link is asked about, is in
``indexer_media``.
"""

import re
from urllib.parse import parse_qsl, urlsplit

from resources.lib.indexer_media import (
    acceptable_link,
    direct_refs,
    host_of,
    id_from_details_link,
    media_ref,
    same_site,
    serves_mediainfo,
    usable_nfo_link,
    valid_release_id,
)

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


def _attr_values(item, names):
    """``{name: value}`` for the first of each named attribute on ``item``."""
    values = {}
    for attr in item.iter():
        tag = attr.tag
        if not isinstance(tag, str) or tag.rsplit("}", 1)[-1] != "attr":
            continue
        name = attr.get("name", "")
        if name in names and name not in values:
            values[name] = str(attr.get("value", "") or "").strip()
    return values


# The API calls that return a release's NFO.
_NFO_CALLS = ("getnfo", "info")


def _link_call(link):
    try:
        return dict(parse_qsl(urlsplit(link).query)).get("t", "").lower()
    except ValueError:
        return ""


def nfo_link(item, indexer_host=""):
    """The link to the release's NFO an indexer sent with ``item``, or "".

    Most indexers send ``nfo`` (1 when the release has an NFO) and ``info``
    (the call that returns it). Some send the link alone, on the results
    that have an NFO: it is taken when it is an NFO call. The link carries
    the user's API key for that indexer, so it is only accepted when it
    points at the indexer the result came from: ``indexer_host`` for a
    direct indexer, else ``hydraIndexerHost``, which NZBHydra2 adds. A
    result must not be able to send the add-on anywhere else.
    """
    values = _attr_values(item, ("nfo", "info", "hydraIndexerHost"))
    link = values.get("info", "")
    if not acceptable_link(link):
        return ""
    flag = values.get("nfo")
    if flag is None:
        if _link_call(link) not in _NFO_CALLS:
            return ""
    elif flag != "1":
        return ""
    indexer_host = indexer_host or values.get("hydraIndexerHost", "")
    if not same_site(host_of(link), indexer_host):
        return ""
    return usable_nfo_link(link)


def _release_id(item, values):
    """The indexer's own id for the release on ``item``, or "".

    From its details link (``comments``, or a ``guid`` that is a link), else
    the ``guid`` attribute. NZBHydra2 replaces the ``guid`` attribute and
    element with its own number, so the details link is looked at first.
    """
    for tag in ("comments", "guid"):
        element = item.find(tag)
        text = (element.text or "").strip() if element is not None else ""
        found = id_from_details_link(text)
        if found:
            return found
    guid = values.get("guid", "")
    # A bare number is NZBHydra2's id, not the indexer's.
    return guid if valid_release_id(guid) and not guid.isdigit() else ""


def media_ref_link(item):
    """A keyless reference for the MediaInfo call of ``item``'s release, or "".

    For an NZBHydra2 result with no NFO link, from an indexer known to serve
    MediaInfo: the reference names the indexer's host and the release's id,
    and ``nfo_audio`` completes it with the key of another result of that
    indexer in the same search.
    """
    values = _attr_values(item, ("hydraIndexerHost", "guid"))
    host = values.get("hydraIndexerHost", "").lower()
    if serves_mediainfo(host) is not True:
        return ""
    comments = item.find("comments")
    details = (comments.text or "").strip() if comments is not None else ""
    if not same_site(host_of(details), host):
        return ""
    try:
        scheme = urlsplit(details).scheme.lower()
    except ValueError:
        return ""
    return media_ref(scheme, host, _release_id(item, values))


def direct_media_links(item, api_url, api_key):
    """``{"nfo_url": ..., "media_ref": ...}`` for a direct indexer's result.

    The link the indexer sent is used when it points at that indexer.
    Otherwise the add-on, which holds the key in direct-indexer mode, builds
    the calls from the release's id (``indexer_media.direct_refs``): the NFO
    call for a result flagged as having one, and a reference for the
    MediaInfo call unless the indexer is known to have none. Only the keys
    with a value are returned.
    """
    api_host = host_of(api_url)
    if not api_host:
        return {}
    sent = nfo_link(item, indexer_host=api_host)
    if sent:
        return {"nfo_url": sent}
    values = _attr_values(item, ("nfo", "guid"))
    nfo_url, ref = direct_refs(
        api_url, api_key, _release_id(item, values), values.get("nfo") == "1"
    )
    links = {}
    if nfo_url:
        links["nfo_url"] = nfo_url
    elif ref:
        links["media_ref"] = ref
    return links


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


# The values that mean a release certainly has a password. 1 is left out: on
# some indexers it means "possibly" (nZEDb, Treasure Maps), on others "yes".
_PASSWORDED = frozenset(("2", "10"))


def password_flag(row):
    """True for a passworded release, False for one without, None if unsure.

    Indexers do not agree on the values: 0 is "none" everywhere, 2 and 10
    are "yes", and 1 is "possibly" on some and "yes" on others, so it and
    anything else (-1 for "not checked") count as unsure.
    """
    value = row.get("password") if isinstance(row, dict) else None
    value = str(value).strip() if value is not None else ""
    if value == "0":
        return False
    if value in _PASSWORDED:
        return True
    return None
