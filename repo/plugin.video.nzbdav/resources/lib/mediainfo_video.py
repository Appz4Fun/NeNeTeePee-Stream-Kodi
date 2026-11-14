# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A release's video facts, from MediaInfo: HDR format, Dolby Vision profile,
bit depth.

A release name can leave its dynamic range out, or state one the file does
not have. MediaInfo's video track says: ``HDR_Format`` ("Dolby Vision /
SMPTE ST 2086"), its profile ("dvhe.08"), what it is compatible with
("HDR10"), the transfer characteristics ("PQ", "HLG", "BT.709") and the bit
depth.

Two sources carry it: the XML an indexer's MediaInfo call returns
(``from_reply``), and the MediaInfo text many NFOs are (``from_nfo``, the
"Video" block). Both give ``{"hdr": [...], "dv_profile": "8", "bit_depth":
"10", "hdr_exact": True}`` with only the keys that could be read:

* ``hdr`` in the add-on's names (Dolby Vision, HDR10+, HDR10, HLG, or SDR);
* ``hdr_exact`` when MediaInfo named the HDR format itself, or showed the
  video to be SDR: the list then replaces what the name says. Without it the
  list only adds to the name: an older MediaInfo reports a PQ transfer and no
  HDR format, which says HDR10 and cannot rule Dolby Vision out.
"""

import re

from resources.lib.mediainfo_audio import mediainfo_xml_of
from resources.lib.xml_safety import ParseError, UnsafeXmlError, safe_fromstring

_DV_PROFILE = re.compile(r"\bd(?:vhe|vav|av1|vh1)\.(\d\d)", re.I)
_DOLBY_VISION = re.compile(r"dolby\s*vision", re.I)
_HDR10_PLUS = re.compile(r"hdr10\s*\+|hdr10plus|st\s*2094", re.I)
_HDR10 = re.compile(r"hdr10(?!\s*\+|plus)|st\s*2086", re.I)
_HLG = re.compile(r"\bhlg\b|hybrid log", re.I)
_PQ = re.compile(r"\bpq\b|st\s*2084|perceptual quant", re.I)
# Transfer characteristics of standard dynamic range video.
_SDR_TRANSFER = re.compile(r"bt\.?\s*(?:709|601|470)|smpte\s*(?:170|240)", re.I)
_BIT_DEPTH = re.compile(r"^\s*(8|10|12)\b")

_XML_HDR_FIELDS = (
    "HDR_Format",
    "HDR_Format_String",
    "HDR_Format_Profile",
    "HDR_Format_Compatibility",
)


def facts(hdr_text, transfer, bit_depth):
    """The video facts for one video track's MediaInfo values.

    ``hdr_text`` is everything MediaInfo says of the HDR format, ``transfer``
    the transfer characteristics, ``bit_depth`` the bit depth's value.
    """
    hdr_text = str(hdr_text or "")
    transfer = str(transfer or "")
    found = {}
    depth = _BIT_DEPTH.match(str(bit_depth or ""))
    if depth:
        found["bit_depth"] = depth.group(1)
    hdr = []
    profile = _DV_PROFILE.search(hdr_text)
    if profile or _DOLBY_VISION.search(hdr_text):
        hdr.append("Dolby Vision")
        if profile:
            found["dv_profile"] = str(int(profile.group(1)))
    if _HDR10_PLUS.search(hdr_text):
        hdr.append("HDR10+")
    if _HDR10.search(hdr_text):
        hdr.append("HDR10")
    if _HLG.search(hdr_text) or _HLG.search(transfer):
        hdr.append("HLG")
    if hdr_text.strip() and hdr:
        found["hdr"] = hdr
        found["hdr_exact"] = True
    elif hdr:
        # HLG named by the transfer alone.
        found["hdr"] = hdr
    elif _PQ.search(transfer):
        # A PQ transfer with no HDR format named: HDR10 at least.
        found["hdr"] = ["HDR10"]
    elif _SDR_TRANSFER.search(transfer) or found.get("bit_depth") == "8":
        found["hdr"] = ["SDR"]
        found["hdr_exact"] = True
    return found


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def from_xml(xml):
    """The facts of the first video track of a MediaInfo XML report, or {}."""
    try:
        root = safe_fromstring(str(xml).lstrip("﻿").strip())
    except (ParseError, UnsafeXmlError, TypeError, ValueError):
        return {}
    for element in root.iter():
        if _local(element.tag) != "track":
            continue
        if str(element.get("type") or "").lower() != "video":
            continue
        fields = {_local(child.tag): (child.text or "").strip() for child in element}
        return facts(
            " / ".join(fields.get(name, "") for name in _XML_HDR_FIELDS).strip(" /"),
            fields.get("transfer_characteristics", ""),
            fields.get("BitDepth", ""),
        )
    return {}


def from_reply(body):
    """The facts in the JSON reply of an indexer's MediaInfo call, or {}."""
    xml = mediainfo_xml_of(body)
    return from_xml(xml) if xml else {}


# MediaInfo's text layout: a line that is just "Video" (or "Video #1") heads
# a block of "Label : value" lines that ends at the next blank line.
_VIDEO_SECTION = re.compile(r"^\W*video(?:\s*#?\s*1)?\W*$", re.I)
_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z /()]*?)\s*:\s*(.*?)\s*$")


def from_nfo(text):
    """The facts in the "Video" block of an NFO that is MediaInfo text, or {}.

    Only a block with a ``Format`` line is read: a hand-written NFO's
    "Video: x264 @ 15371 kb/s" names no dynamic range.
    """
    fields = {}
    in_section = False
    for line in str(text or "").splitlines():
        if not line.strip():
            if in_section:
                break
            continue
        if _VIDEO_SECTION.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        match = _FIELD.match(line)
        if match:
            fields.setdefault(match.group(1).strip().lower(), match.group(2))
    if "format" not in fields:
        return {}
    return facts(
        fields.get("hdr format", ""),
        fields.get("transfer characteristics", ""),
        fields.get("bit depth", ""),
    )
