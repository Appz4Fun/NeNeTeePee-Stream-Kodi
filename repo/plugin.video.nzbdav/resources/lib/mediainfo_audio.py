# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A release's audio tracks, from an indexer's MediaInfo call.

An NFO is free text. MediaInfo is the tool's own report of the file, with
every audio track's format, Atmos or DTS:X marker and channel count as
fields, so where an indexer serves it, it is the exact answer.

Treasure Maps serves it from a REST API beside its newznab one:
``GET /api/v1/releases/{guid}/mediainfo`` with the key in an ``X-API-Key``
header, answering JSON that holds MediaInfo's XML. The add-on has no key of
its own for an indexer; the NFO link that indexer sends with a result (see
``newznab_attrs.nfo_link``) carries the release's guid and the key, on the
indexer's own host, and the request is built from it.

``nfo_audio`` asks for MediaInfo first and reads the NFO when the release has
none. This module holds what both readers share: MediaInfo's names for the
formats, and how several tracks become one release's audio.
"""

import json
import re
from urllib.parse import parse_qsl, quote, urlsplit

from resources.lib.filter_audio import main_audio
from resources.lib.filter_tags import detect_audio
from resources.lib.xml_safety import ParseError, UnsafeXmlError, safe_fromstring

# MediaInfo's names for formats, as release names write them. The longer
# names come first: "Enhanced AC-3" must not be read as its last word.
MEDIAINFO_NAMES = (
    (
        re.compile(
            r"dolby digital plus with dolby atmos"
            r"|enhanced ac-?3 with joint object coding|e-?ac-?3[ /+]*joc",
            re.I,
        ),
        "DDP Atmos",
    ),
    (re.compile(r"dolby truehd with dolby atmos|mlp fba 16-ch", re.I), "TrueHD Atmos"),
    (re.compile(r"dts[ :-]?x\b|dts xll x", re.I), "DTS-X DTS-HD MA"),
    (re.compile(r"dts-hd master audio|dts xll", re.I), "DTS-HD MA"),
    (re.compile(r"dts-hd high resolution(?: audio)?|dts xbr", re.I), "DTS-HD HR"),
    (re.compile(r"dolby digital plus|enhanced ac-?3|e-?ac-?3", re.I), "DDP"),
    (re.compile(r"mlp fba|dolby truehd", re.I), "TrueHD"),
    (re.compile(r"dolby digital|audio coding 3|\bac-?3\b", re.I), "DD"),
)
# MediaInfo's channel counts, as release names write them.
CHANNEL_COUNT = {"1": "1.0", "2": "2.0", "6": "5.1", "7": "6.1", "8": "7.1"}

# Object audio rides on a bed track: Atmos on TrueHD or DD+, DTS:X on DTS.
_OBJECT_FORMATS = ("Atmos", "DTS:X")


def _without_stray_objects(entries, formats, main):
    """``formats`` without object audio that belongs to another track.

    The formats of every track are kept in one list, and the picker reads
    "Atmos" beside "TrueHD" as TrueHD with Atmos. An NFO with a plain TrueHD
    7.1 track and a separate Dolby Digital Plus Atmos track must not say
    that: the object format is kept only when a track carries it together
    with the main track's format, or when it is the main format itself.
    """
    kept = []
    for name in formats:
        if name in _OBJECT_FORMATS and name != main:
            if not any(
                name in entry["audio"] and main in entry["audio"] for entry in entries
            ):
                continue
        kept.append(name)
    return kept


def readable_formats(text):
    """``text`` with MediaInfo's format names replaced by release-name tags."""
    for pattern, name in MEDIAINFO_NAMES:
        text = pattern.sub(name, text)
    return text


def merge_tracks(entries):
    """``{"audio": [...], "channels": "5.1"}`` for a release's tracks.

    ``entries`` are ``{"audio": [...], "channels": ""}`` per track, in order.
    The audio list holds the formats of every track, in the order named
    (object audio only when it is on the main track, see
    ``_without_stray_objects``). The channel count is the main track's own,
    the track with the format the picker leads with
    (``filter_audio.main_audio``): "French DD 5.1" beside "English TrueHD
    7.1" is 7.1. See ``_main_channels`` for several tracks of that format.
    """
    formats = []
    for entry in entries:
        formats.extend(name for name in entry["audio"] if name not in formats)
    if not formats:
        return {"audio": [], "channels": ""}
    main = main_audio(formats)
    formats = _without_stray_objects(entries, formats, main)
    return {"audio": formats, "channels": _main_channels(entries, formats, main)}


def _main_channels(entries, formats, main):
    """The channel count of the track the release is named for.

    Several tracks can share the main format: a stereo Dolby Digital Plus
    track beside the 5.1 one with Atmos, or a commentary. The one carrying
    the object audio that was kept leads, then the one with the most
    channels, then the first.
    """
    objects = [name for name in formats if name in _OBJECT_FORMATS and name != main]
    best, best_rank = "", None
    for entry in entries:
        if main not in entry["audio"] or not entry["channels"]:
            continue
        try:
            count = float(entry["channels"])
        except ValueError:
            count = 0.0
        rank = (any(name in entry["audio"] for name in objects), count)
        if best_rank is None or rank > best_rank:
            best, best_rank = entry["channels"], rank
    return best


# --- the request ------------------------------------------------------------

_GUID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_API_PATH = "/api/v1/releases/{}/mediainfo"


def request_for(nfo_url):
    """``(url, headers)`` of the MediaInfo call for an NFO link, or None.

    The NFO link is ``.../api?t=getnfo&id=<guid>&apikey=<key>``. The call
    goes to the same host, with the guid in the path and the key in a
    header: the key is never put in this URL.
    """
    try:
        parts = urlsplit(nfo_url)
        query = dict(parse_qsl(parts.query))
    except (AttributeError, TypeError, ValueError):
        return None
    guid = query.get("id", "")
    key = query.get("apikey", "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not _GUID.match(guid) or not key or len(key) > 128:
        return None
    url = "{}://{}{}".format(
        parts.scheme, parts.netloc, _API_PATH.format(quote(guid, safe=""))
    )
    return url, {"X-API-Key": key, "Accept": "application/json"}


# --- the reply --------------------------------------------------------------

# What a reply to the call means.
TRACKS = "tracks"  # MediaInfo was returned: the tracks are with it
MISSING = "missing"  # the API is there, this release has no MediaInfo
NO_API = "no api"  # this indexer has no such call
REFUSED = "refused"  # not for this client, or not now
FAILED = "failed"  # no usable answer this time

_REFUSAL_STATUSES = frozenset((401, 403, 429))


def read_reply(status, body):
    """``(what, tracks)`` for a reply to the MediaInfo call.

    ``status`` is the HTTP status (None when the request itself failed) and
    ``body`` the text that came with it. ``tracks`` is the list of track
    entries when ``what`` is ``TRACKS``, otherwise None.
    """
    if status is None:
        return FAILED, None
    if status in _REFUSAL_STATUSES:
        return REFUSED, None
    if status >= 500:
        return FAILED, None
    reply = _json_object(body)
    if status == 200:
        xml = _mediainfo_xml(reply)
        if xml is None:
            return NO_API, None
        tracks = audio_tracks(xml)
        return (TRACKS, tracks) if tracks is not None else (MISSING, None)
    if status == 404 and isinstance((reply or {}).get("error"), dict):
        # The API's own "not found" for this release.
        return MISSING, None
    return NO_API, None


def _json_object(body):
    try:
        reply = json.loads(body)
    except (TypeError, ValueError):
        return None
    return reply if isinstance(reply, dict) else None


def _mediainfo_xml(reply):
    """The MediaInfo XML inside the call's JSON reply, or None."""
    holder = (reply or {}).get("mediainfo")
    if not isinstance(holder, dict):
        return None
    for field in ("utf8", "raw"):
        text = holder.get(field)
        if isinstance(text, str) and text.strip():
            return text
    return ""


_TRACK_FIELDS = ("Format", "Format_AdditionalFeatures", "Format_Commercial_IfAny")


def audio_tracks(xml):
    """The audio tracks of a MediaInfo XML report, or None when unreadable.

    One ``{"audio": [...], "channels": "5.1"}`` per ``<track type="Audio">``,
    in order. The format comes from the track's format, its additional
    features ("JOC", "XLL", "16-ch") and its commercial name ("Dolby Digital
    Plus with Dolby Atmos"), read with the same tags as release names. The
    XML is untrusted and goes through the safe reader.
    """
    try:
        root = safe_fromstring(str(xml).lstrip("\ufeff").strip())
    except (ParseError, UnsafeXmlError, TypeError, ValueError):
        return None
    tracks = []
    for element in root.iter():
        if _local(element.tag) != "track":
            continue
        if str(element.get("type") or "").lower() != "audio":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in element}
        described = " ".join(fields.get(name, "") for name in _TRACK_FIELDS)
        formats = detect_audio(readable_formats(described))
        channels = CHANNEL_COUNT.get(fields.get("Channels", ""), "")
        tracks.append({"audio": list(formats), "channels": channels})
    return tracks


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""
