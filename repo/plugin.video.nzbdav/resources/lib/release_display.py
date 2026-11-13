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

from resources.lib.filter_audio import audio_label
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
# A subtitle tag ("KORSUB", "ESubs", "SUBBED", "VOSTFR"): the languages the
# name lists are then read, not heard.
_SUBBED = re.compile(
    r"(?<![a-z0-9])(?:[a-z]{0,3}subs?|subbed|vostfr)(?![a-z0-9])", re.I
)
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
    """ "S01", "S01-S03", "E05", "E01-E02" for a sorted number list."""
    code = "{}{:02d}".format(letter, numbers[0])
    if numbers[-1] > numbers[0]:
        code += "-{}{:02d}".format(letter, numbers[-1])
    return code


def _episode_code(seasons, episodes):
    """ "S02E01", "S02E01-E02", "E05" (no season), "S01-S03" (a pack), or ""."""
    if episodes:
        season = "S{:02d}".format(seasons[0]) if seasons else ""
        return season + _range("E", episodes)
    if seasons:
        return _range("S", seasons)
    return ""


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


def _heading(title, identity, meta):
    """ "Title (Year)", then " - S02E01" for an episode, then " {Edition}"."""
    out = title
    year = _year(identity, meta)
    if year and not title.endswith("({})".format(year)):
        out += " ({})".format(year)
    code = _episode_code(
        _int_list(identity.get("seasons")), _int_list(identity.get("episodes"))
    )
    if code:
        out += " - " + code
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
        return text + " REPACK"
    if meta.get("proper"):
        return text + " Proper"
    return text


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

    Nothing for a subtitled release: its language tags name the subtitles.
    """
    if _MULTI.search(_MULTI_SUBS.sub(" ", name)):
        return "MULTi"
    if _SUBBED.search(name):
        return ""
    codes = [str(code) for code in meta.get("languages") or [] if code]
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
        audio_label(meta.get("audio"), meta.get("channels"), meta.get("quality")),
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
    out = _heading(title, identity, meta) + " " + _specs(cleaned, meta)
    group = _collapse(meta.get("group"))
    if group:
        out += "-" + group
    return out.strip()
