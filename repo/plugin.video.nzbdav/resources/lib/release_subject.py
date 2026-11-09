# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover the release name from a raw Usenet subject before title parsing.

Raw-search indexers (NZBIndex, Binsearch, some Newznab feeds) list the posting
SUBJECT rather than a release name, for example::

    [1/3] - "Show.S01E01.1080p.WEB-DL.DDP5.1.H.264-GRP.mkv" yEnc  4400443301
    [PRiVATE]-[WtFnZb]-[Show.S01E01.1080p.WEB.h264-GRP]-[1/7] - "" yEnc (1/45)
    Show.S01E01.1080p.WEB.h264-GRP [01/50] - "Show.S01E01.1080p.part01.rar" yEnc

PTT reads those wrappers as title, group, or episode data (group "1/3", a
part number as an episode). ``release_name_from_subject`` returns the
release name inside, and returns a normal release title unchanged.
"""

import re

_QUOTED = re.compile(r'"([^"]*)"')
_BRACKETED = re.compile(r"\[([^\[\]]*)\]")
# The WtFnZb style: "[PRiVATE]-[WtFnZb]-[Real.Name.mkv]-[1/7] - "" yEnc". The
# name may hold brackets of its own, so take everything up to "]-[n/m]".
_BRACKET_NAME = re.compile(
    r"^\s*(?:\[[^\[\]]*\]\s*-\s*)*\[(.+?)\]\s*-\s*\[\s*\d+\s*(?:/|of)\s*\d+\s*\]",
    re.I,
)
_YENC = re.compile(r"\byEnc\b", re.I)
_COUNTER = re.compile(r"[\[(]\s*\d+\s*(?:/|of)\s*\d+\s*[\])]", re.I)

# File extensions a posted file carries, outermost first: a par2 volume
# (".vol003+04.par2"), a split archive (".part01.rar", ".7z.001", ".r00",
# ".001"), or the media file itself.
_FILE_SUFFIX = re.compile(
    r"(?:\.vol\d+[+-]\d+)?\.par2$"
    r"|(?:\.part\d+)?\.rar$"
    r"|\.(?:7z|zip|rar|mkv|mp4|avi|ts)\.\d{3}$"
    r"|\.(?:r\d{2}|(?!26[456]$)\d{3})$"
    r"|\.(?:7z|zip|mkv|mp4|m4v|avi|ts|m2ts|wmv|mov|webm|iso|nzb|nfo|sfv|srr"
    r"|srt|sub|idx|jpg|png)$",
    re.I,
)
# Subject tails and wrappers left once the name is taken out.
_SUBJECT_NOISE = re.compile(
    r"\byEnc\b.*$"
    r"|[\[(]\s*\d+\s*(?:/|of)\s*\d+\s*[\])]"
    r"|\b\d+(?:[.,]\d+)?\s*[KMGT]i?B\b",
    re.I,
)
# A release name has a recognizable tag: SxxEyy, a resolution, a year, or a
# source. An obfuscated filename ("a8f3c9e1b2d4f6a7.mkv") has none.
_RELEASE_TAG = re.compile(
    r"(?<![a-z0-9])(?:s\d{1,2}(?:e\d{1,3})+|\d{1,2}x\d{2,3}|\d{3,4}[pi]|[48]k"
    r"|(?:19|20)\d{2}|web-?(?:dl|rip)|blu-?ray|hdtv|remux|x26[45]|h\.?26[45]"
    r"|hevc|season[ ._]?\d+)(?![a-z0-9])",
    re.I,
)
_NAME_CHARS = re.compile(r"[ ._-]")
# Tags that make one candidate name richer than another (resolution, source,
# codec, audio, HDR, episode, year) when a subject carries several.
_DETAIL_TAG = re.compile(
    r"(?<![a-z0-9])(?:s\d{1,2}(?:e\d{1,3})*|\d{3,4}[pi]|[48]k|(?:19|20)\d{2}"
    r"|web-?(?:dl|rip)?|blu-?ray|hdtv|remux|uhd|[xh]\.?26[456]|hevc|avc|av1"
    r"|ddp?\d?|dts|aac\d?|atmos|truehd|flac|hdr\d*|dv|sdr)(?![a-z0-9])",
    re.I,
)
_ENDS_WITH_GROUP = re.compile(r"-[a-z0-9]\w*$", re.I)


def is_usenet_subject(title):
    """Whether ``title`` looks like a raw posting subject, not a release name."""
    title = str(title or "")
    if _YENC.search(title) or _COUNTER.search(title):
        return True
    return any(_FILE_SUFFIX.search(q.strip()) for q in _QUOTED.findall(title))


def _strip_file_suffix(name):
    previous = None
    while previous != name:
        previous = name
        name = _FILE_SUFFIX.sub("", name)
    return name.strip(" -")


# Tokens that are tags, not title words, beyond the ones _DETAIL_TAG knows.
_TAG_WORDS = frozenset(
    (
        "dl",
        "rip",
        "bit",
        "10bit",
        "8bit",
        "amzn",
        "nf",
        "dsnp",
        "hmax",
        "atvp",
        "multi",
        "proper",
        "repack",
        "remastered",
        "internal",
        "sbs",
        "hsbs",
    )
)
_TOKEN_SPLIT = re.compile(r"[\s._\-\[\]()]+")


def _has_title_word(name):
    """Whether ``name`` holds a word that isn't a tag (a title, not just tags)."""
    for token in _TOKEN_SPLIT.split(name):
        if (
            sum(c.isalpha() for c in token) >= 2
            and token.lower() not in _TAG_WORDS
            and not _DETAIL_TAG.fullmatch(token)
            and not _RELEASE_TAG.fullmatch(token)
        ):
            return True
    return False


def _looks_like_release(name):
    """A recognizable tag plus a title word: "Dune.2021", not "1080p WEB-DL x265"."""
    return (
        bool(_RELEASE_TAG.search(name))
        and bool(_NAME_CHARS.search(name))
        and _has_title_word(name)
    )


def _detail(name):
    """How much release detail ``name`` carries: distinct tags, then a group."""
    tags = {tag.lower() for tag in _DETAIL_TAG.findall(name)}
    return len(tags) + (1 if _ENDS_WITH_GROUP.search(name) else 0)


def _subject_remainder(title):
    """``title`` with quoted filenames, counters, sizes and the yEnc tail removed."""
    remainder = _QUOTED.sub(" ", title)
    previous = None
    while previous != remainder:
        previous = remainder
        remainder = _SUBJECT_NOISE.sub(" ", remainder)
    remainder = " ".join(remainder.split()).strip(' -"')
    return _strip_file_suffix(remainder)


def release_name_from_subject(title):
    """Return the release name inside a raw Usenet subject.

    The candidates are the quoted filenames, the WtFnZb bracket name, the
    other bracketed segments, and the subject with its counters, size, quotes
    and ``yEnc`` tail removed. The one that looks like a release and carries
    the most detail wins, so a full name outside the quotes beats a short
    archive name inside them ("Name.2020.part01.rar"), and an obfuscated
    quoted filename never wins. Ties keep that order. A title that is not a
    subject comes back unchanged.
    """
    title = str(title or "")
    if not is_usenet_subject(title):
        return title
    candidates = [_strip_file_suffix(q) for q in _QUOTED.findall(title)]
    named = _BRACKET_NAME.match(title)
    if named:
        candidates.append(_strip_file_suffix(named.group(1)))
    candidates += [_strip_file_suffix(b) for b in _BRACKETED.findall(title)]
    remainder = _subject_remainder(title)
    candidates.append(remainder)
    best = None
    for candidate in candidates:
        if candidate and _looks_like_release(candidate):
            if best is None or _detail(candidate) > _detail(best):
                best = candidate
    return best or remainder or title
