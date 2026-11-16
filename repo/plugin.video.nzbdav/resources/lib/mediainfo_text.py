# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A MediaInfo XML report as the text MediaInfo itself prints.

An indexer's MediaInfo call returns the report as XML. The picker's viewer
shows it the way people know it: a block per track, one ``Label : value``
line per field, the labels padded to one width.

Only the fields worth reading are shown, with their numbers made readable
(``61.0 Mb/s``, ``2 h 28 min``). The file name is shown without its folder:
a report made on someone's machine can carry a path.
"""

from resources.lib.xml_safety import ParseError, UnsafeXmlError, safe_fromstring

_LABEL_WIDTH = 25


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _number(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _bit_rate(text):
    value = _number(text)
    if value is None:
        return str(text or "")
    if value >= 10000000:
        return "{:.1f} Mb/s".format(value / 1000000)
    if value >= 1000000:
        return "{:.2f} Mb/s".format(value / 1000000)
    return "{:,.0f} kb/s".format(value / 1000).replace(",", " ")


def _size(text):
    value = _number(text)
    if value is None:
        return str(text or "")
    if value >= 1073741824:
        return "{:.1f} GiB".format(value / 1073741824)
    return "{:.0f} MiB".format(value / 1048576)


def _duration(text):
    value = _number(text)
    if value is None:
        return str(text or "")
    minutes = int(round(value / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "{} h {} min".format(hours, minutes)
    return "{} min".format(minutes)


def _pixels(text):
    value = _number(text)
    if value is None:
        return str(text or "")
    return "{:,.0f} pixels".format(value).replace(",", " ")


def _suffix(unit):
    return lambda text: "{} {}".format(text, unit)


def _base_name(text):
    return str(text or "").replace("\\", "/").rsplit("/", 1)[-1]


# (XML field, label, how its value is written), in MediaInfo's order.
_GENERAL = (
    ("Format", "Format", str),
    ("FileSize", "File size", _size),
    ("Duration", "Duration", _duration),
    ("OverallBitRate", "Overall bit rate", _bit_rate),
    ("Encoded_Application", "Writing application", str),
)
_VIDEO = (
    ("Format", "Format", str),
    ("Format_Profile", "Format profile", str),
    ("HDR_Format", "HDR format", str),
    ("HDR_Format_Profile", "HDR format profile", str),
    ("HDR_Format_Compatibility", "HDR format compatibility", str),
    ("BitRate", "Bit rate", _bit_rate),
    ("Width", "Width", _pixels),
    ("Height", "Height", _pixels),
    ("DisplayAspectRatio", "Display aspect ratio", str),
    ("FrameRate", "Frame rate", _suffix("FPS")),
    ("BitDepth", "Bit depth", _suffix("bits")),
    ("colour_primaries", "Color primaries", str),
    ("transfer_characteristics", "Transfer characteristics", str),
)
_AUDIO = (
    ("Format", "Format", str),
    ("Format_Commercial_IfAny", "Commercial name", str),
    ("Format_AdditionalFeatures", "Additional features", str),
    ("Channels", "Channel(s)", _suffix("channels")),
    ("ChannelLayout", "Channel layout", str),
    ("BitRate", "Bit rate", _bit_rate),
    ("Language", "Language", str),
    ("Title", "Title", str),
    ("Default", "Default", str),
)
_TEXT = (
    ("Format", "Format", str),
    ("Language", "Language", str),
    ("Title", "Title", str),
    ("Forced", "Forced", str),
)
_FIELDS = {"general": _GENERAL, "video": _VIDEO, "audio": _AUDIO, "text": _TEXT}
_HEADINGS = {"general": "General", "video": "Video", "audio": "Audio", "text": "Text"}


def _line(label, value):
    return "{} : {}".format(label.ljust(_LABEL_WIDTH), value)


def file_name(xml):
    """The name of the file a MediaInfo XML report is about, or ""."""
    try:
        root = safe_fromstring(str(xml).lstrip("﻿").strip())
    except (ParseError, UnsafeXmlError, TypeError, ValueError):
        return ""
    for element in root.iter():
        if _local(element.tag) == "media" and element.get("ref"):
            return _base_name(element.get("ref"))
    return ""


def as_text(xml):
    """The report as text; the XML itself when it cannot be read."""
    raw = str(xml or "")
    try:
        root = safe_fromstring(raw.lstrip("﻿").strip())
    except (ParseError, UnsafeXmlError, TypeError, ValueError):
        return raw
    blocks = []
    counts = {}
    totals = {}
    tracks = [el for el in root.iter() if _local(el.tag) == "track"]
    for track in tracks:
        kind = str(track.get("type") or "").lower()
        totals[kind] = totals.get(kind, 0) + 1
    name = file_name(raw)
    for track in tracks:
        kind = str(track.get("type") or "").lower()
        if kind not in _FIELDS:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        heading = _HEADINGS[kind]
        if totals.get(kind, 0) > 1:
            heading = "{} #{}".format(heading, counts[kind])
        values = {_local(child.tag): (child.text or "").strip() for child in track}
        lines = [heading]
        if kind == "general" and name:
            lines.append(_line("Complete name", name))
        for field, label, write in _FIELDS[kind]:
            if values.get(field):
                lines.append(_line(label, write(values[field])))
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else raw
