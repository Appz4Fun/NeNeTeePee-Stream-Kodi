# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What each indexer offers for a release's NFO and MediaInfo, and how to ask.

Indexers differ in what they tell about a release before it is downloaded:

* most newznab sites flag a result that has an NFO (``nfo`` = 1) and send the
  call that returns it (``info``), a ``t=getnfo`` link on their own API;
* nZEDb sites (Drunken Slug) send a ``t=info`` link and no flag;
* Treasure Maps also has a REST API beside newznab, with a MediaInfo call;
* some send neither flag nor link, and have no call that was found.

``KNOWN`` is that table, one entry per indexer checked. It replaces asking:
an indexer in it is asked only for what it has. An indexer that is not in it
is still probed once for the MediaInfo call (``nfo_audio``), and its NFO is
read where a result brings a link or a flag.

Through NZBHydra2 the add-on holds no key for an indexer. The link an indexer
sends with a result carries the release's id and the user's key, and every
request is built from such a link: to that indexer's own host only, with the
key in a header where the indexer takes one. A result that brings no link of
its own (``media_ref``) borrows the key from another result of the same
indexer in the same search. In direct-indexer mode the add-on has the key,
and builds the link itself (``direct_refs``).
"""

import re
from collections import namedtuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# How an indexer's NFO is asked for when the add-on builds the call itself.
GETNFO = "getnfo"  # newznab: api?t=getnfo&id=<id>&raw=1
INFO = "info"  # nZEDb: api?t=info&id=<id>
# Where an indexer serves MediaInfo.
REST_V1 = "rest v1"  # GET /api/v1/releases/<id>/mediainfo, key in X-API-Key

# ``nfo``: the NFO call (None: none was found). ``mediainfo``: the MediaInfo
# call (None: it has none).
Offer = namedtuple("Offer", ("nfo", "mediainfo"))

# Checked with a few searches and each site's API page, and by the probe
# below on real use (issue #580). The key is the site's domain:
# "api.example.org" matches "example.org". An indexer that sends no link with
# a result and whose API page was not read (NZBGeek, nzbplanet, miatrix) is
# left out: it stays unknown.
KNOWN = {
    # REST v1 beside newznab. Flags every result, so many flagged releases
    # answer "No such item" to getnfo; most have MediaInfo.
    "treasure-maps.com": Offer(GETNFO, REST_V1),
    # Plain newznab: an NFO flag and a getnfo link on the results that have one.
    "abnzb.com": Offer(GETNFO, None),
    "squareeyed.org": Offer(GETNFO, None),
    "ninjacentral.co.za": Offer(GETNFO, None),
    "althub.co.za": Offer(GETNFO, None),
    "nzb.life": Offer(GETNFO, None),
    "nzb.su": Offer(GETNFO, None),
    "nzbnoob.com": Offer(GETNFO, None),
    # Their links carry the key as "r" (``usable_nfo_link``).
    "digitalcarnage.info": Offer(GETNFO, None),
    # nZEDb: an info link on the few results that have an NFO, and no flag.
    "drunkenslug.com": Offer(INFO, None),
    # Neither flag nor link on a result, and no NFO or MediaInfo call in its
    # API (a JSON API v2 whose details repeat the search fields).
    "nzbfinder.ws": Offer(None, None),
}

_MAX_LINK = 2048
_MAX_KEY = 128
# A release id: 32 or 40 hex digits, a UUID, or a site's own number.
_RELEASE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
# The query parameters that carry a key, on the calls indexers send.
KEY_PARAMS = ("apikey", "i", "r")


def host_of(url):
    """The host of ``url`` in lower case, or "" when it has none."""
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def same_site(host, other):
    """Whether two hosts are one site: equal, or one under the other."""
    host = str(host or "").strip().lower()
    other = str(other or "").strip().lower()
    if not host or not other or "." not in host or "." not in other:
        return False
    return host == other or host.endswith("." + other) or other.endswith("." + host)


def offer_for(host):
    """What the indexer at ``host`` is known to offer, or None when unknown."""
    host = str(host or "").strip().lower()
    for site, offer in KNOWN.items():
        if host == site or host.endswith("." + site):
            return offer
    return None


def serves_mediainfo(host):
    """True when ``host`` has the MediaInfo call, False when it is known to
    have none, None when the indexer is not in the table (probe it)."""
    offer = offer_for(host)
    if offer is None:
        return None
    return offer.mediainfo == REST_V1


def valid_release_id(value):
    return bool(_RELEASE_ID.match(str(value or "")))


def id_from_details_link(link):
    """The release id in an indexer's details link, or "".

    ``https://host/details/<id>#comments`` on most sites, and
    ``https://host/geekseek.php?guid=<id>`` on one.
    """
    try:
        parts = urlsplit(str(link or ""))
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return ""
    for name, value in parse_qsl(parts.query):
        if name.lower() in ("guid", "id") and valid_release_id(value):
            return value
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) >= 2 and segments[-2].lower() == "details":
        if valid_release_id(segments[-1]):
            return segments[-1]
    return ""


def link_key(link):
    """``{param: value}`` for the key parameters of an API link."""
    try:
        query = parse_qsl(urlsplit(str(link or "")).query)
    except ValueError:
        return {}
    return {
        name: value
        for name, value in query
        if name in KEY_PARAMS and value and len(value) <= _MAX_KEY
    }


def link_id(link):
    """The release id an API link names, or ""."""
    try:
        query = dict(parse_qsl(urlsplit(str(link or "")).query))
    except ValueError:
        return ""
    value = query.get("id", "")
    return value if valid_release_id(value) else ""


def _api_link(scheme, netloc, path, params):
    return urlunsplit((scheme, netloc, path or "/api", urlencode(params), ""))


def media_ref(scheme, host, release_id):
    """A keyless reference to a release on an indexer's API.

    For a result that brings no link of its own, from an indexer with the
    MediaInfo call: ``with_key_from`` completes it from another result's link.
    """
    if scheme not in ("http", "https") or not host or not valid_release_id(release_id):
        return ""
    return _api_link(scheme, host, "/api", [("id", release_id)])


def with_key_from(ref, keyed_link):
    """``ref`` with the key of ``keyed_link``, a link to the same host; or ""."""
    try:
        target = urlsplit(ref)
        source = urlsplit(keyed_link)
    except (AttributeError, TypeError, ValueError):
        return ""
    if (
        not target.hostname
        or (target.hostname or "").lower() != (source.hostname or "").lower()
    ):
        return ""
    if target.scheme != source.scheme or target.port != source.port:
        return ""
    key = link_key(keyed_link)
    release_id = link_id(ref)
    if not key or not release_id:
        return ""
    params = [("id", release_id)] + sorted(key.items())
    return _api_link(target.scheme, target.netloc, source.path, params)


def has_key(link):
    return bool(link_key(link))


def direct_refs(api_url, api_key, release_id, flagged):
    """``(nfo_url, media_ref)`` for a result of a direct indexer.

    The add-on holds the key in direct-indexer mode, so it builds the calls
    itself. ``nfo_url`` when the result is ``flagged`` as having an NFO and
    the indexer has an NFO call (an indexer not in the table is taken to be
    newznab). ``media_ref``, a keyed reference for the MediaInfo call, unless
    the indexer is known to have none.
    """
    try:
        parts = urlsplit(str(api_url or ""))
    except ValueError:
        return "", ""
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "", ""
    if not valid_release_id(release_id) or not api_key or len(api_key) > _MAX_KEY:
        return "", ""
    offer = offer_for(host)
    call = offer.nfo if offer is not None else GETNFO
    nfo_url = ""
    if flagged and call:
        params = [("t", call), ("id", release_id), ("apikey", api_key)]
        if call == GETNFO:
            params.append(("raw", "1"))
        nfo_url = _api_link(parts.scheme.lower(), parts.netloc, parts.path, params)
    ref = ""
    if serves_mediainfo(host) is not False:
        ref = _api_link(
            parts.scheme.lower(),
            parts.netloc,
            parts.path,
            [("id", release_id), ("apikey", api_key)],
        )
    return nfo_url, ref


def mediainfo_request(link):
    """``(url, headers)`` of the REST MediaInfo call for an API link, or None.

    ``link`` is an NFO link or a ``media_ref``: it names the release (``id``)
    and carries the key (``apikey``). The call goes to the same host, with
    the id in the path and the key in a header: never in this URL.
    """
    try:
        parts = urlsplit(link)
        query = dict(parse_qsl(parts.query))
    except (AttributeError, TypeError, ValueError):
        return None
    release_id = query.get("id", "")
    key = query.get("apikey", "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not valid_release_id(release_id) or not key or len(key) > _MAX_KEY:
        return None
    url = "{}://{}/api/v1/releases/{}/mediainfo".format(
        parts.scheme, parts.netloc, quote(release_id, safe="")
    )
    return url, {"X-API-Key": key, "Accept": "application/json"}


def usable_nfo_link(link):
    """``link`` as the indexer's API takes it.

    nZEDb-family sites (Drunken Slug, Digital Carnage) send the link with the
    key as ``r`` and the user's number as ``i``, the way their RSS feeds take
    it, and their API answers "Missing parameter (apikey)" to it. The same
    key under ``apikey`` is accepted. A link that already has ``apikey`` is
    returned as it is.
    """
    link = str(link or "")
    try:
        parts = urlsplit(link)
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return ""
    names = [name for name, _value in query]
    if "apikey" in names or "r" not in names:
        return link
    key = dict(query)["r"]
    kept = [(name, value) for name, value in query if name not in ("i", "r")]
    kept.append(("apikey", key))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )


def acceptable_link(link):
    """Whether ``link`` is an http(s) link of a sane length with a host."""
    link = str(link or "")
    if not link or len(link) > _MAX_LINK:
        return False
    try:
        parts = urlsplit(link)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
