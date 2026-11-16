# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What an NFO or a MediaInfo report says of a release, beside what its
name says.

The picker's viewer (``nfo_viewer``) lists, under the text it shows, the
attributes read from that text: resolution, dynamic range, video codec,
audio, source and release group. Each one is marked as either confirming
what the listing's name already says, or as new: a listing named
``4k.Movie.DV`` whose NFO gives the audio and the group shows ``2160p`` and
``DV`` as confirmed, and the audio and the group as new.

An attribute is ``{"slot", "text", "new"}``. ``slot`` names the kind
(``SLOTS``), which is also what the viewer colors it by.
"""

from resources.lib import mediainfo_audio, mediainfo_video, nfo_audio, release_docs
from resources.lib.filter_audio import audio_label
from resources.lib.release_display import clean_release_name, codec_label
from resources.lib.xml_safety import ParseError, UnsafeXmlError, safe_fromstring

RESOLUTION = "resolution"
HDR = "hdr"
CODEC = "codec"
AUDIO = "audio"
SOURCE = "source"
GROUP = "group"
SLOTS = (RESOLUTION, HDR, CODEC, AUDIO, SOURCE, GROUP)

# The picker's short names for dynamic range, in its order.
_HDR_SHORT = (
    ("Dolby Vision", "DV"),
    ("HDR10+", "HDR10+"),
    ("HDR10", "HDR10"),
    ("HLG", "HLG"),
    ("SDR", "SDR"),
)
_SOURCE_SHORT = {"BluRay REMUX": "REMUX"}
# MediaInfo's video format names, as the add-on's parser names codecs.
_MEDIAINFO_CODECS = {
    "hevc": "x265/HEVC",
    "avc": "x264/AVC",
    "av1": "AV1",
    "vvc": "VVC",
    "vp9": "VP9",
    "vc-1": "VC-1",
    "mpeg video": "MPEG-2",
}
# Frame widths, for a report that gives pixels: a cropped 3840x1600 is 2160p.
_WIDTHS = ((3800, "2160p"), (2500, "1440p"), (1900, "1080p"), (1260, "720p"))


def _facts(meta, name=""):
    """``{slot: [values]}`` for a parsed name's metadata."""
    facts = {slot: [] for slot in SLOTS}
    if not isinstance(meta, dict):
        return facts
    if meta.get("resolution"):
        facts[RESOLUTION].append(str(meta["resolution"]))
    listed = meta.get("hdr") or []
    for value, short in _HDR_SHORT:
        if value in listed and value != "SDR":
            facts[HDR].append(short)
    if meta.get("codec"):
        facts[CODEC].append(codec_label(clean_release_name(name), meta) or "")
    label = audio_label(meta.get("audio"), meta.get("channels"), meta.get("quality"))
    if label:
        facts[AUDIO].append(label)
    quality = str(meta.get("quality") or "")
    if quality:
        facts[SOURCE].append(_SOURCE_SHORT.get(quality, quality))
    if meta.get("group"):
        facts[GROUP].append(str(meta["group"]))
    return facts


def name_facts(title, parse):
    """What the listing's own name says: ``{slot: [values]}``.

    ``parse`` is ``filter.parse_title_metadata``.
    """
    try:
        return _facts(parse(str(title or "")), str(title or ""))
    except Exception:  # pylint: disable=broad-except
        return {slot: [] for slot in SLOTS}


def _video_values(video):
    """The dynamic range a report names, in the picker's short words."""
    values = []
    for value, short in _HDR_SHORT:
        if value in (video.get("hdr") or ()):
            if value == "Dolby Vision" and video.get("dv_profile"):
                short = "DV P{}".format(video["dv_profile"])
            values.append(short)
    return values


def _nfo_facts(text, title, parse, describe):
    """What an NFO says: its audio lines, its video block, and the name of
    the release it gives for this film or episode."""
    stated = nfo_audio.stated_release_name(text, title, describe)
    facts = name_facts(stated, parse) if stated else {slot: [] for slot in SLOTS}
    found = nfo_audio.audio_from_nfo(text)
    if found["audio"]:
        quality = ""
        try:
            quality = (parse(stated) if stated else {}).get("quality", "")
        except Exception:  # pylint: disable=broad-except
            quality = ""
        label = audio_label(found["audio"], found["channels"], quality)
        if label:
            facts[AUDIO] = [label]
    video = mediainfo_video.from_nfo(text)
    if video.get("hdr"):
        facts[HDR] = _video_values(video)
    return facts


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _mediainfo_video_fields(xml):
    """``{field: value}`` of the first video track of a MediaInfo XML report."""
    try:
        root = safe_fromstring(str(xml).lstrip("﻿").strip())
    except (ParseError, UnsafeXmlError, TypeError, ValueError):
        return {}
    for element in root.iter():
        if _local(element.tag) == "track":
            if str(element.get("type") or "").lower() == "video":
                return {
                    _local(child.tag): (child.text or "").strip() for child in element
                }
    return {}


def _mediainfo_facts(xml):
    """What a MediaInfo report says of the file."""
    facts = {slot: [] for slot in SLOTS}
    fields = _mediainfo_video_fields(xml)
    try:
        width = int(float(fields.get("Width", "") or 0))
    except ValueError:
        width = 0
    for least, name in _WIDTHS:
        if width >= least:
            facts[RESOLUTION].append(name)
            break
    else:
        if width:
            facts[RESOLUTION].append("480p")
    facts[HDR] = _video_values(mediainfo_video.from_xml(xml))
    codec = _MEDIAINFO_CODECS.get(fields.get("Format", "").strip().lower())
    if codec:
        facts[CODEC].append(codec_label("", {"codec": codec}) or codec)
    tracks = mediainfo_audio.audio_tracks(xml) or []
    found = mediainfo_audio.merge_tracks(tracks)
    label = audio_label(found["audio"], found["channels"])
    if label:
        facts[AUDIO].append(label)
    return facts


def _same(slot, value, named):
    """Whether ``value`` only confirms what the name says for ``slot``."""
    if slot == HDR:
        # "DV P7" confirms a name's "DV": the profile is a detail of it.
        value = value.split(" ")[0]
    if slot == CODEC:
        return bool(named)
    return value.casefold() in (other.casefold() for other in named)


def attributes(kind, text, title, parse, describe=None):
    """The attributes read from a release's NFO or MediaInfo, marked.

    ``[{"slot", "text", "new"}]`` in the picker's order. ``new`` is False
    when the listing's name (``title``) already says it. ``parse`` is
    ``filter.parse_title_metadata`` and ``describe`` is
    ``filter.release_identity``.
    """
    named = name_facts(title, parse)
    if kind == release_docs.MEDIAINFO:
        read = _mediainfo_facts(text)
    else:
        read = _nfo_facts(text, title, parse, describe)
    marked = []
    for slot in SLOTS:
        for value in read[slot]:
            if value:
                marked.append(
                    {
                        "slot": slot,
                        "text": value,
                        "new": not _same(slot, value, named[slot]),
                    }
                )
    return marked
