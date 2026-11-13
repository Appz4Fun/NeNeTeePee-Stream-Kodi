# SPDX-License-Identifier: GPL-3.0-or-later
"""The name a release is shown under in the picker.

Indexers send the same release as a dotted scene name, a spaced and
parenthesized one, or a raw Usenet subject (``[1/3] - "Name.mkv" yEnc 123``).
The picker shows all of them one way, modeled on TRaSH Guides' Radarr and
Sonarr naming::

    Dune Part Two (2024) [Remux-2160p][HYBRID][DV HDR10+][TrueHD Atmos 7.1][HEVC]-GRP
    Six Feet Under (2001) - S02E01 [WEB-DL-1080p][DD+ 5.1][H264]-FLUX

Every part is read from the release name's own parsed metadata (``_meta``),
so two listings that parse alike read alike, and nothing is looked up. A
name with no title or no source and resolution to show (a hash, a bare
word) is shown as its cleaned release name instead.
"""

import re

from resources.lib.filter_audio import audio_label, main_audio
from resources.lib.filter_languages import title_languages
from resources.lib.release_subject import (
    release_name_from_subject,
    without_media_extension,
)

_SPACES = re.compile(r"\s+")
_MULTI = re.compile(r"(?<![a-z0-9])(?:multi|dual[ ._-]*audio)(?![a-z0-9])", re.I)
# "MULTi.Subs", "MultiSub": many subtitles, one language heard.
_MULTI_SUBS = re.compile(r"(?<![a-z0-9])multi[ ._-]*subs?(?![a-z0-9])", re.I)
_X26 = re.compile(r"(?<![a-z0-9])x[. _-]?26([45])(?![a-z0-9])", re.I)
_H26 = re.compile(r"(?<![a-z0-9])h[. _-]?26([45])(?![a-z0-9])", re.I)

# Sources shown with the resolution ("WEB-DL-1080p", "Remux-2160p").
_SOURCES = {
    "web-dl": "WEB-DL",
    "web": "WEB-DL",
    "webmux": "WEB-DL",
    "webrip": "WEBRip",
    "web-dlrip": "WEBRip",
    "uhdrip": "WEBRip",
    "bluray remux": "Remux",
    "remux": "Remux",
    "bluray": "Bluray",
    "bdrip": "Bluray",
    "brrip": "Bluray",
    "hdtv": "HDTV",
    "hdtvrip": "HDTV",
    "pdtv": "HDTV",
    "satrip": "HDTV",
    "tvrip": "HDTV",
    "ppvrip": "HDTV",
}
# Standard-definition sources: the source alone says what it is.
_SD_SOURCES = {
    "dvd": "DVD",
    "dvdrip": "DVD",
    "telesync": "TELESYNC",
    "telecine": "TELECINE",
    "scr": "DVDSCR",
    "cam": "CAM",
}
_CODEC_NAMES = {"x265/HEVC": ("5", "HEVC"), "x264/AVC": ("4", "AVC")}
# A subtitle tag and the language it names: "KORSUB", "ESubs", "VOSTFR", or
# a bare "Subs" / "Subbed" with the word before it ("German.Subbed"). That
# language is read, not heard.
_SUB_TAGS = re.compile(
    r"(?<![a-z0-9])(?:[a-z]{1,3}subs?|vostfr"
    r"|(?:[a-z]+[ ._-])?(?:subs?|subbed|subtitles?))(?![a-z0-9])",
    re.I,
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PART_SEPARATORS = re.compile(r"[\s._-]+")
# ``meta["variant"]`` flags shown as their own "[...]" block, and the cut
# flags shown with the edition. PTT strips all of them from the title, so a
# name that left them out would read like the plain release.
_VARIANT_BLOCKS = (("3d", "3D"), ("dubbed", "DUBBED"), ("hardcoded", "HC"))
_CUT_NAMES = (
    ("extended", "Extended"),
    ("unrated", "Unrated"),
    ("uncensored", "Uncensored"),
    ("remastered", "Remastered"),
)
# More dubbed languages than this reads as "MULTi".
_MAX_LANGUAGES = 3

HYBRID_LABEL = "HYBRID"


def clean_release_name(title):
    """The release name inside ``title``: no subject wrapper, no extension."""
    return without_media_extension(release_name_from_subject(title)).strip()


def _collapse(text):
    return _SPACES.sub(" ", str(text or "")).strip()


def _int_list(values):
    out = []
    for value in values if isinstance(values, (list, tuple)) else ():
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _range(letter, numbers):
    """ "S01", "S01-S03", "E05", "E01-E02" for a sorted number list.

    A run with a gap lists each number ("E01E02E05"): it is a different
    release from the unbroken run with the same ends.
    """
    first, last = numbers[0], numbers[-1]
    if len(numbers) > 1 and numbers != list(range(first, last + 1)):
        return "".join("{}{:02d}".format(letter, number) for number in numbers)
    code = "{}{:02d}".format(letter, first)
    if last > first:
        code += "-{}{:02d}".format(letter, last)
    return code


def _episode_code(seasons, episodes):
    """ "S02E01", "S02E01-E02", "S01-S02E01-E10", "E05" (no season),
    "S01-S03" (a pack), or ""."""
    season = _range("S", seasons) if seasons else ""
    return season + (_range("E", episodes) if episodes else "")


def _year(identity, meta):
    for value in (identity.get("year"), meta.get("year")):
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 0


def _variant(meta):
    variant = meta.get("variant")
    return variant if isinstance(variant, (list, tuple)) else ()


def _edition(meta):
    """The edition plus any cut flag it doesn't already name: "Unrated"."""
    edition = _collapse(meta.get("edition"))
    parts = [edition] if edition else []
    variant = _variant(meta)
    for flag, name in _CUT_NAMES:
        if flag in variant and flag not in edition.lower():
            parts.append(name)
    return " ".join(parts)


def _part(name, title):
    """The part label of ``name`` ("Part 2") unless the title already has it.

    PTT keeps "Part 2" in a movie's title but drops it from a split season
    pack ("Show.S01.Part.1"), whose halves would then read alike.
    """
    # The label the same-release rule reads a part number from.
    from resources.lib.fallback_streams import _PART_LABEL_RE

    match = _PART_LABEL_RE.search(name)
    if not match or _PART_LABEL_RE.search(title):
        return ""
    words = [word for word in _PART_SEPARATORS.split(match.group(0)) if word]
    number = words[-1].upper() if len(words) > 1 else ""
    return " ".join(part for part in (words[0].capitalize(), number) if part)


def _heading(name, title, identity, meta):
    """ "Title (Year)", " - S02E01" or the air date, the part, " {Edition}"."""
    out = title
    year = _year(identity, meta)
    if year and not title.endswith("({})".format(year)):
        out += " ({})".format(year)
    code = _episode_code(
        _int_list(identity.get("seasons")), _int_list(identity.get("episodes"))
    )
    date = str(identity.get("date") or "")
    if code:
        out += " - " + code
    elif _DATE.fullmatch(date):
        # A daily show: the air date is its episode.
        out += " - " + date
    part = _part(name, title)
    if part:
        out += (" " if code else " - ") + part
    edition = _edition(meta)
    if edition:
        out += " {" + edition + "}"
    return out


def _height(resolution):
    digits = str(resolution or "")[:-1]
    return int(digits) if digits.isdigit() else 0


def _quality(meta):
    """Radarr's quality: "Bluray-1080p", "Remux-2160p", "DVD"."""
    resolution = str(meta.get("resolution") or "")
    source = str(meta.get("quality") or "")
    key = source.lower()
    if key in _SD_SOURCES:
        text = _SD_SOURCES[key]
    elif _SOURCES.get(key) == "HDTV" and 0 < _height(resolution) < 720:
        text = "SDTV"
    else:
        parts = (_SOURCES.get(key, source), resolution)
        text = "-".join(part for part in parts if part)
    if not text:
        return ""
    if meta.get("repack"):
        text += " REPACK"
    if meta.get("proper"):
        text += " Proper"
    return text


_ATMOS_BEDS = ("TrueHD", "DD+", "DD")


def _audio(meta):
    """The main track, then any other track the name tags: "TrueHD Atmos
    7.1 + DD". With no audio tag, the channel count alone ("5.1").

    Every tagged format is part of the same-release rule, so a name that
    left the second track out would read like the release without it.
    """
    names = [str(name) for name in meta.get("audio") or [] if name]
    label = audio_label(names, meta.get("channels"), meta.get("quality"))
    if not label:
        return str(meta.get("channels") or "")
    main = main_audio(names, meta.get("quality"))
    shown = {main}
    if main in _ATMOS_BEDS:
        shown.add("Atmos")
    if main == "DTS:X" and "DTS-HD MA" in names:
        # One track: DTS:X objects on the DTS-HD MA bed the name also tags.
        label = "DTS-HD MA " + label
        shown.add("DTS-HD MA")
    others = [name for name in names if name not in shown]
    return " + ".join([label] + others)


def dynamic_range(meta):
    """ "DV HDR10+": Dolby Vision first, then the HDR10 kind, then HLG."""
    values = meta.get("hdr") or []
    out = []
    if "Dolby Vision" in values:
        out.append("DV")
    if "HDR10+" in values:
        out.append("HDR10+")
    elif "HDR10" in values:
        out.append("HDR10")
    if "HLG" in values:
        out.append("HLG")
    return " ".join(out)


def _languages(name, meta):
    """ "MULTi", or the languages heard when one is not English.

    A language named only by a subtitle tag ("KORSUB", "German.Subbed") is
    left out; one the name states apart from it ("German ... ESubs") stays.
    """
    if _MULTI.search(_MULTI_SUBS.sub(" ", name)):
        return "MULTi"
    codes = [str(code) for code in meta.get("languages") or [] if code]
    if _SUB_TAGS.search(name):
        heard = title_languages(_SUB_TAGS.sub(" ", name), [])
        codes = [code for code in codes if code in heard]
    if not codes or all(code.lower() == "en" for code in codes):
        return ""
    if len(codes) > _MAX_LANGUAGES:
        return "MULTi"
    return "+".join(code.upper() for code in codes)


def codec_label(name, meta):
    """ "x265" when the name says so, "H265" likewise, else "HEVC"."""
    codec = str(meta.get("codec") or "")
    if codec not in _CODEC_NAMES:
        return codec
    digit, plain = _CODEC_NAMES[codec]
    for pattern, prefix in ((_X26, "x26"), (_H26, "H26")):
        match = pattern.search(name)
        if match and match.group(1) == digit:
            return prefix + digit
    return plain


def _specs(name, meta):
    """ "[WEB-DL-2160p][HYBRID][3D][DV HDR10][DD+ Atmos 5.1][MULTi][H265]"."""
    variant = _variant(meta)
    languages = _languages(name, meta)
    parts = (
        _quality(meta),
        HYBRID_LABEL if meta.get("hybrid") else "",
    )
    # PTT flags a MULTi release as dubbed; "[MULTi]" already says so.
    parts += tuple(
        label
        for flag, label in _VARIANT_BLOCKS
        if flag in variant and not (flag == "dubbed" and languages == "MULTi")
    )
    parts += (
        dynamic_range(meta),
        _audio(meta),
        languages,
        codec_label(name, meta),
    )
    return "".join("[{}]".format(part) for part in parts if part)


def display_name(result):
    """The normalized name of a picker row; see the module docstring.

    Falls back to the cleaned release name when the row has no parsed
    metadata, no title, or neither a source nor a resolution.
    """
    cleaned = clean_release_name(result.get("title"))
    meta = result.get("_meta")
    if not isinstance(meta, dict):
        return cleaned
    identity = meta.get("ptt_identity")
    identity = identity if isinstance(identity, dict) else {}
    title = _collapse(identity.get("title"))
    if not any(char.isalnum() for char in title):
        return cleaned
    if not (meta.get("resolution") or meta.get("quality")):
        return cleaned
    out = _heading(cleaned, title, identity, meta) + " " + _specs(cleaned, meta)
    group = _collapse(meta.get("group"))
    if group:
        out += "-" + group
    return out.strip()
