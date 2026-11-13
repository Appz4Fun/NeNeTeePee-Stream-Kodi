# SPDX-License-Identifier: GPL-3.0-or-later
"""Precise media-tag recognition shared by normal and fallback title parsing."""

import re

from resources.lib.filter_audio import main_audio
from resources.lib.filter_languages import is_language_word, title_languages
from resources.lib.release_subject import release_name_from_subject


def _pattern(expression):
    return re.compile(r"(?<![a-z0-9])(?:" + expression + r")(?![a-z0-9])", re.I)


_HDR_PLUS = _pattern(r"hdr(?:10)?(?:[. _-]?plus|[. _-]?\+)|hdr(?:10)?p")
_HDR = _pattern(r"hdr(?:10)?")
_DV = _pattern(r"dv|dovi|dolby[. _-]?vision")
_HLG = _pattern(r"hlg")
_RESOLUTION = _pattern(r"\d{3,4}x\d{3,4}|\d{3,4}[pi]|[48]k|qhd")

_AUDIO_EXPRESSIONS = (
    ("Atmos", r"atmos"),
    ("TrueHD", r"true[. _-]?hd"),
    ("DTS:X", r"dts[. :_-]?x"),
    ("DTS-HD MA", r"dts[. _-]?hd[. _-]?(?:ma|master(?:[. _-]?audio)?)"),
    ("DTS-HD HR", r"dts[. _-]?hd[. _-]?(?:hra?|high[. _-]?resolution)"),
    ("DTS", r"dts(?![. _:-]?(?:hd|x)\b)"),
    (
        "DD+",
        r"e[. _-]?ac[. _-]?3(?:x\d+)?|dd2?(?:p|\+)(?:[1-9][. _]?\d)?"
        r"|dd[. _-]?plus"
        r"|dolby[. _-]?digital[. _-]?plus",
    ),
    (
        "DD",
        r"ac[. _-]?3(?:x\d+)?|dd(?:[1257][. _]?\d)?|dolby[. _-]?(?:digital|d)",
    ),
    ("AAC", r"(?:he[. _-]?|q{1,2})?aac(?:[1257][. _]?\d|2)?(?:x\d+)?"),
    ("FLAC", r"flac(?:[1257][. _]?\d)?(?:x\d+)?"),
    ("PCM", r"l?pcm(?:[1257][. _]?\d)?"),
    ("OPUS", r"opus(?:[1257][. _]?\d)?"),
    ("MP3", r"mp3"),
    ("ALAC", r"alac"),
    ("Vorbis", r"vorbis"),
    ("MP2", r"mp2"),
    ("WMA", r"wma(?:[. _-]?pro)?"),
    ("AC-4", r"ac[. _-]?4"),
)
_AUDIO_PATTERNS = tuple(
    (name, _pattern(expression)) for name, expression in _AUDIO_EXPRESSIONS
)

_AUDIO_ORDER = tuple(name for name, _pattern_ in _AUDIO_PATTERNS)
# "DDPA5.1", "DD+A", "DDPAtmos5.1": Dolby Digital Plus with Atmos as one tag.
_DDP_ATMOS = _pattern(r"dd(?:p|\+)a(?:tmos)?(?:[1-9][. _]?\d)?")
# "DD_5.1" in a dotted name, "DD 5.1" in a dotted or underscored one: the
# "+" of "DD+5.1" replaced by an indexer or a URL decode. Group 1 is the
# stand-in character.
_DD_LOST_PLUS = _pattern(r"dd([ _])[1-9][. _]?\d")


def _channels_after(expression):
    """A pattern for the channel count written right after an audio tag.

    An "Atmos" in between at most: "FLAC.1.0", "TrueHD.Atmos.7.1", "DDP5.1",
    "DDPA5.1", "Opus 2 0". Groups 1 and 2 are the two digits.
    """
    return re.compile(
        r"(?<![a-z0-9])(?:"
        + expression
        + r")(?:[. _-]?atmos|(?<=[p+])a)?[. _-]?([1-9])[. _]?([0-2])(?![a-z0-9])",
        re.I,
    )


_CHANNELS_AFTER = {
    name: _channels_after(expression) for name, expression in _AUDIO_EXPRESSIONS
}
_CHANNELS_AFTER_ANY = _channels_after(
    "|".join(expression for _name, expression in _AUDIO_EXPRESSIONS)
)
_HYBRID = _pattern(r"hybrid")
# Where a name's title ends and its tags begin: a year, SxxEyy or a
# resolution. A "Hybrid" before it is a title word ("Hybrid.2007.1080p").
_TAGS_START = _pattern(
    r"(?:19|20)\d{2}|s\d{1,2}(?:e\d{1,3})*|\d{1,2}x\d{2,3}|\d{3,4}[pi]|[48]k"
)

_CODEC_PATTERNS = tuple(
    (name, _pattern(expression))
    for name, expression in [
        ("VVC", r"vvc|h[. _-]?266"),
        ("x265/HEVC", r"[xh][. _-]?265|hevc(?:10(?:bit)?)?"),
        ("x264/AVC", r"[xh][. _-]?264|avc"),
        ("AV1", r"av1"),
        ("VP9", r"vp[. _-]?9"),
        ("VP8", r"vp[. _-]?8"),
        ("VC-1", r"vc[. _-]?1|wvc1"),
        ("MPEG-4 ASP", r"xvid|divx|mpeg[. _-]?4(?:[. _-]?asp)?"),
        ("MPEG-2", r"mpeg[. _-]?2|h[. _-]?262"),
        ("MPEG-1", r"mpeg[. _-]?1"),
        ("WMV", r"wmv[139]?"),
        ("H.263", r"h[. _-]?263"),
        ("MJPEG", r"mjpeg|mjpg"),
        ("Theora", r"theora"),
    ]
)


_MEDIA_EXTENSION = re.compile(
    r"\.(?:mkv|mp4|m4v|avi|ts|m2ts|wmv|mov|webm|iso|nzb|nfo|par2|rar|7z|zip"
    r"|r\d{2}|\d{3})$",
    re.I,
)
# Tags posted AFTER the group ("-HONE.REMASTERED", "-GRP[rarbg]",
# "(... - HONE) [REMASTERED]") and the posting/indexer suffixes that replace
# it ("-GRP-Obfuscated", "-GRP-postbot"). Each pass strips one from the end.
# Edition and posting tags, also seen in trailing parentheses ("(Extended)").
_EDITION_TAGS = (
    r"extended|remastered|proper|repack\d?|rerip|internal|unrated|uncut|uncensored"
    r"|imax|theatrical|criterion|limited|hybrid|3d|directors?[ ._-]?cut"
    r"|special[ ._-]?edition|anniversary[ ._-]?edition"
)
_TRAILING_TAG = re.compile(
    r"(?:\s*\[[^\[\]]*\]"
    r"|[ ._]?\((?:" + _EDITION_TAGS + r")\)"
    r"|[ ._](?:remastered|proper|repack\d?|rerip|internal|readnfo|real|dirfix"
    r"|nfofix|subfix|sample|hybrid)"
    r"|-(?:obfuscated|scrambled|postbot|xpost|rakuv\w*|whiterev|buymore"
    r"|asrequested|alternativetorequested|repackpost|chamele0n|4p|4planet"
    r"|altezachen|gerov|z0ids3n|nzbgeek|sample|pre|rp))\s*$",
    re.I,
)
# Groups may hold hyphens ("GROUP-NAME", "D-Z0N3"); _group_candidate drops a
# leading source/codec segment such as the "DL" of "WEB-DL-GRP".
_GROUP_AT_END = re.compile(r"(?:-|\s-\s?)([a-z0-9][\w&@-]*)\)?\s*$", re.I)
# A whole group in trailing parentheses: "x264.(D-Z0N3)", "(GROUP-NAME)".
_PAREN_GROUP_AT_END = re.compile(r"[ ._-]\(([a-z0-9][\w&@-]*)\)\s*$", re.I)
_LEADING_GROUP = re.compile(r"^\[([^\[\]]+)\]")
# A trailing parenthesized block of space-separated tags; its last word may
# be the group ("(1080p BluRay x265 10bit AAC 5.1 Tigole)").
_PAREN_TAG_BLOCK = re.compile(r"\(([^()]*\s[a-z0-9][\w&@-]*)\)\s*$", re.I)
_NOT_A_GROUP = re.compile(
    r"\d+|\d+v\d+|\d+/\d+|s\d+(?:e\d+)*|e\d+|\d+x\d+|dl|rip|web(?:rip|dl)?|hd(?:tv|rip)?"
    r"|sd|uhd|blu-?ray|bd(?:rip|remux)?|dvd(?:rip)?|remux|hdr\d*|sdr|dv|hlg"
    r"|\d{3,4}[pi]|[48]k|[xh]\d{3}|hevc|avc|multi|dual|dubbed|subbed"
    r"|(?:\d{1,2}-?)?bits?|[a-z]{0,3}subs?|" + _EDITION_TAGS,
    re.I,
)


_TAG_PATTERNS = tuple(pattern for _name, pattern in _AUDIO_PATTERNS + _CODEC_PATTERNS)


def _valid_group(value):
    """Whether ``value`` can be a group: not a source, codec or audio tag."""
    value = str(value or "").strip()
    return (
        bool(value)
        and "/" not in value
        and not _NOT_A_GROUP.fullmatch(value)
        and not any(pattern.fullmatch(value) for pattern in _TAG_PATTERNS)
    )


def _group_candidate(value):
    """The trailing hyphen segments of ``value`` up to the first tag-like one.

    Keeps "GROUP-NAME" and "D-Z0N3" whole, but drops the "DL" of "WEB-DL-GRP"
    and never returns a slug run such as "matrix-1999-1080p-x264-grp".
    """
    kept = []
    for part in reversed(value.split("-")):
        if not _valid_group(part) or len(kept) == 3:
            break
        kept.insert(0, part)
    return "-".join(kept)


# Source and format tags written with a hyphen ("Blu-Ray", "WEB-DL",
# "DVD-Rip", "10-bit", "DTS-ES", "DD-EX").
_HYPHENATED_SOURCE = _pattern(
    r"blu-ray|web-(?:dl|rip)|hd-(?:dvd|tv|rip)|dvd-(?:rip|r)|bd-(?:rip|remux)"
    r"|\d{1,2}-bits?|dts-(?:es|hr)|dd-?ex"
)


def _without_hyphenated_tags(name):
    """``name`` with hyphenated source/HDR/audio/codec tags blanked.

    Splitting the trailing "-" run would otherwise read "MA-FGT" or "MA" as
    the group of "x264.DTS-HD-MA-FGT", and "Ray-GRP" as that of
    "x264.Blu-Ray-GRP".
    """
    for pattern in (_HYPHENATED_SOURCE, _HDR_PLUS, _DV) + _TAG_PATTERNS:
        name = pattern.sub(
            lambda match: " " if "-" in match.group(0) else match.group(0), name
        )
    return name


def _release_name(title):
    """The release name inside ``title`` (see ``release_subject``), no extension."""
    name = release_name_from_subject(title)
    return _MEDIA_EXTENSION.sub("", name.strip().rstrip("/"))


def detect_release_group(title, parsed_group=""):
    """Return the release group, correcting PTT where its group is wrong.

    PTT only reads a group at the very end of a name, so a tag posted after
    the group ("-HONE.REMASTERED"), a parenthesized name ("(... - HONE)
    [REMASTERED]"), an indexer suffix ("-GRP-Obfuscated") or a yEnc subject
    (``[1/3] - "Name-GRP.mkv" yEnc``, read as group "1/3") loses it. Strip
    those from the end and read the "-GROUP" left there; else keep PTT's
    group when it is plausible, else a leading "[Group]" (anime style).
    Unknown stays "".
    """
    title = str(title or "")
    name = _release_name(title)
    previous = None
    while previous != name:
        previous = name
        name = _TRAILING_TAG.sub("", name)
    name = _without_hyphenated_tags(name)
    parenthesized = _PAREN_GROUP_AT_END.search(name)
    if parenthesized and _group_candidate(parenthesized.group(1)):
        return _group_candidate(parenthesized.group(1))
    leading = _LEADING_GROUP.match(name)
    leading = leading.group(1).strip() if leading else ""
    leading = leading if _valid_group(leading) else ""
    match = _GROUP_AT_END.search(name)
    group = _group_candidate(match.group(1)) if match else ""
    # "[SubsPlease] Show - NCOP [1080p]": with a leading [Group], a spaced
    # " - word" at the end is the episode or special, not the group.
    if group and not (leading and match.group(0)[:1].isspace()):
        return group
    if _valid_group(parsed_group) and not _TRAILING_TAG.search("-" + str(parsed_group)):
        return str(parsed_group)
    return leading or _tag_block_group(name)


def _tag_block_group(name):
    """The group ending a trailing parenthesized tag block, else "".

    "Movie (2023) (2160p BluRay x265 HEVC 10bit HDR AAC 7.1 Tigole)": the
    block holds a resolution and its last word is no tag and no language.
    """
    block = _PAREN_TAG_BLOCK.search(name)
    if not block or not _RESOLUTION.search(block.group(1)):
        return ""
    last = block.group(1).split()[-1]
    if _valid_group(last) and not is_language_word(last):
        return last
    return ""


def detect_hdr(title):
    """HDR formats tagged in the title, or ``["SDR"]`` when none is.

    The HDR types are Dolby Vision, HDR10, HDR10+ (HDR+, HDRP, HDRPLUS,
    HDR10P) and HLG. Anything else is SDR, so an explicit "SDR" tag adds
    nothing and is ignored: it only states the absence of HDR.
    """
    values = ["Dolby Vision"] if _DV.search(title) else []
    plus = bool(_HDR_PLUS.search(title))
    if plus:
        values.append("HDR10+")
    # Remove HDR+ tokens before looking for a separately declared HDR base.
    if _HDR.search(_HDR_PLUS.sub("", title)):
        values.append("HDR10")
    if _HLG.search(title):
        values.append("HLG")
    return values or ["SDR"]


def _main_separator(name):
    """The character a name separates its words with: ".", " " or "_"."""
    return max(". _", key=name.count)


def detect_audio(title):
    """Audio formats tagged in the title, in ``_AUDIO_EXPRESSIONS`` order.

    Specific tags are consumed before broad ones, so "DTS-HD.MA" is not also
    "DTS" and "DDP5.1" is not also "DD". Three Dolby spellings are read as
    Dolby Digital Plus (E-AC-3):

    * "DDPA5.1" / "DD+A": Dolby Digital Plus with Atmos;
    * "DD_5.1" or "DD 5.1" where the name separates its words with another
      character: the "+" of "DD+5.1" was replaced on the way here;
    * "DD" beside "Atmos" with no TrueHD: Atmos only rides on TrueHD or on
      Dolby Digital Plus, never on plain Dolby Digital (AC-3).
    """
    found = set()
    remaining = title
    if _DDP_ATMOS.search(remaining):
        found.update(("Atmos", "DD+"))
        remaining = _DDP_ATMOS.sub(" ", remaining)
    separator = _main_separator(title)
    for match in _DD_LOST_PLUS.finditer(remaining):
        if match.group(1) != separator:
            found.add("DD+")
            remaining = remaining.replace(match.group(0), " ")
    for name, pattern in _AUDIO_PATTERNS:
        if pattern.search(remaining):
            found.add(name)
            remaining = pattern.sub(" ", remaining)
    if "Atmos" in found and "DD" in found and "TrueHD" not in found:
        found.discard("DD")
        found.add("DD+")
    return [name for name in _AUDIO_ORDER if name in found]


def _count(match):
    return "{}.{}".format(match.group(1), match.group(2))


def detect_channels(title):
    """Every distinct channel count written right after an audio tag."""
    return list(dict.fromkeys(_count(m) for m in _CHANNELS_AFTER_ANY.finditer(title)))


def main_track_channels(title, audio, quality="", parsed=""):
    """The channel count of the release's main audio track, or "".

    A name can tag several tracks ("TrueHD.7.1.Atmos.DD5.1"), so the count
    is the one written right after the main track's own tag
    (``filter_audio.main_audio``). A "DD" tag read as Dolby Digital Plus
    ("DD_5.1", "DD5.1.Atmos") is looked up as written. With no count by
    that tag, the name's only count is used; several counts and none by the
    main track is ambiguous and gives "". ``parsed`` is PTT's value, used
    when no count follows any audio tag ("stereo", "x264.5.1").
    """
    main = main_audio(audio, quality)
    tags = [main]
    if main == "DD+" and "DD" not in audio:
        tags.append("DD")
    for tag in tags:
        pattern = _CHANNELS_AFTER.get(tag)
        match = pattern.search(title) if pattern else None
        if match:
            return _count(match)
    counts = detect_channels(title)
    if not counts:
        return str(parsed or "")
    return counts[0] if len(counts) == 1 else ""


def detect_hybrid(title):
    """Whether the name carries the HYBRID tag.

    A hybrid release combines sources: one disc's video with another's
    audio, or HDR10+ and Dolby Vision layers from different releases. Only a
    "Hybrid" after the year, episode or resolution counts; before it, the
    word belongs to the title.
    """
    start = _TAGS_START.search(title)
    return bool(start and _HYBRID.search(title, start.end()))


def detect_codec(title):
    """Return the first explicit codec tag, retaining unknown when absent."""
    return next(
        (name for name, pattern in _CODEC_PATTERNS if pattern.search(title)), ""
    )


def normalize_resolution(value):
    """Normalize dimensions, 4K/8K, and interlaced aliases into filter buckets."""
    value = value.lower().rstrip("p") if "x" in value.lower() else value.lower()
    if "x" in value:
        width, _, height = value.partition("x")
        value = {
            "7680": "4320",
            "3840": "2160",
            "2560": "1440",
            "1920": "1080",
            "1280": "720",
        }.get(width, height) + "p"
    value = {"4k": "2160p", "8k": "4320p", "qhd": "1440p"}.get(value, value)
    return value[:-1] + "p" if value.endswith("i") else value


def supplement_metadata(title, meta):
    """Correct media metadata consistently without modifying vendored PTT."""
    match = _RESOLUTION.search(title)
    meta["resolution"] = normalize_resolution(
        match.group() if match else meta["resolution"]
    )
    meta["hdr"] = detect_hdr(title)
    audio = detect_audio(title)
    if audio:
        # Correct PTT's DTS/DD family labels, but retain other formats and
        # aliases that its parser recognizes beyond the supplemental patterns.
        replaced = set(audio)
        if any(value.startswith("DTS") for value in audio):
            replaced.update(("DTS", "DTS-HD MA", "DTS-HD HR", "DTS:X"))
        if replaced.intersection(("DD", "DD+")):
            replaced.update(("DD", "DD+"))
        meta["audio"] = audio + [
            value for value in meta["audio"] if value not in replaced
        ]
    meta["channels"] = main_track_channels(
        title, meta["audio"], meta.get("quality", ""), meta.get("channels", "")
    )
    meta["hybrid"] = detect_hybrid(title)
    meta["codec"] = detect_codec(title) or meta["codec"]
    meta["languages"] = title_languages(title, meta["languages"])
    meta["group"] = detect_release_group(title, meta.get("group", ""))
    return meta
