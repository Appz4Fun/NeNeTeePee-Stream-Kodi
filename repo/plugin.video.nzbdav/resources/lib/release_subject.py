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

import collections
import re

_QUOTED = re.compile(r'"([^"]*)"')
_BRACKETED = re.compile(r"\[([^\[\]]*)\]")
# The WtFnZb style: "[PRiVATE]-[WtFnZb]-[Real.Name.mkv]-[1/7] - "" yEnc",
# with or without a poster prefix ("[N3wZ] \\id\\::[PRiVATE]-..."). The name
# is the chain bracket right before the "[n/m]" counter. It may hold brackets
# of its own, but never a "]-[" chain link.
_BRACKET_NAME = re.compile(
    r"\[((?:(?!\]\s*-\s*\[).)+)\]\s*-\s*\[\s*\d+\s*(?:/|of)\s*\d+\s*\]",
    re.I,
)
_CHAIN_BRACKET = re.compile(
    r"\[([^\[\]]*)\](?=\s*-\s*\[)|(?<=\]-)\[([^\[\]]*)\]|(?<=\] - )\[([^\[\]]*)\]"
)
_YENC = re.compile(r"\byEnc\b", re.I)
_COUNTER = re.compile(r"[\[(]\s*\d+\s*(?:/|of)\s*\d+\s*[\])]", re.I)

# File suffixes a posted file carries, outermost first: a par2 volume
# (".vol003+04.par2"), a split archive (".part01.rar", ".7z.001", ".r00",
# ".001"), or a side file (".nfo", ".sfv"). Not the media extension.
_FILE_SUFFIX = re.compile(
    r"(?:\.vol\d+[+-]\d+)?\.par2$"
    r"|(?:\.part\d+)?\.rar$"
    r"|\.(?:r\d{2}|(?!26[456]$)\d{3})$"
    r"|\.(?:7z|zip|iso|nzb|nfo|sfv|srr|srt|sub|idx|jpg|png)$",
    re.I,
)
# A media extension is kept on the name: PTT reads the container from it.
_MEDIA_EXTENSION = re.compile(r"\.(?:mkv|mp4|m4v|avi|ts|m2ts|wmv|mov|webm)$", re.I)
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
# codec, audio, HDR, episode, year, the 3D/dub/hardsub/cut variants, and
# language tags) when a subject carries several.
_DETAIL_TAG = re.compile(
    r"(?<![a-z0-9])(?:s\d{1,2}(?:e\d{1,3})*|\d{3,4}[pi]|[48]k|(?:19|20)\d{2}"
    r"|web-?(?:dl|rip)?|blu-?ray|hdtv|remux|uhd|[xh]\.?26[456]|hevc|avc|av1"
    r"|ddp?\d?|dts|aac\d?|atmos|truehd|flac|hdr\d*|dv|sdr"
    r"|3d|h?sbs|h?ou|extended|unrated|uncensored|remastered|dubbed|multi|hc"
    r"|proper|repack"
    r"|english|german|french|truefrench|spanish|castellano|latino|italian|dutch"
    r"|flemish|russian|ukrainian|polish|czech|hungarian|portuguese|brazilian"
    r"|swedish|danish|norwegian|finnish|nordic|turkish|greek|hebrew|arabic|hindi"
    r"|tamil|telugu|japanese|korean|chinese|mandarin|cantonese|thai|vietnamese"
    r"|vostfr|vff|vfq|vf2|eng|ger|fre|spa|ita|rus|dual)(?![a-z0-9])",
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
_TOKEN_SPLIT = re.compile(r"[\s._\-\[\]()\\]+")


_YEAR = re.compile(r"(?:19|20)\d{2}")


def _numeric_title(tokens):
    """A leading number or letter that is the title.

    "300.2006", "1917.2019", "9.2009", "M.1931", "X.2022". A year-shaped title
    (1917) or a single letter needs a year after it.
    """
    if len(tokens) < 2:
        return False
    first = tokens[0]
    has_year_after = any(_YEAR.fullmatch(token) for token in tokens[1:])
    if len(first) == 1 and first.isalpha():
        return has_year_after
    if not first.isdigit():
        return False
    if _YEAR.fullmatch(first):
        return has_year_after
    return not _DETAIL_TAG.fullmatch(first)


def _has_title_word(name):
    """Whether ``name`` holds a word that isn't a tag (a title, not just tags)."""
    tokens = [token for token in _TOKEN_SPLIT.split(name) if token]
    if _numeric_title(tokens):
        return True
    for token in tokens:
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


_EPISODE_NUMBER = re.compile(r"(?<=\d)e\d{1,3}", re.I)


def _detail(name):
    """How much release detail ``name`` carries.

    Distinct tags, plus each episode number past the first of an SxxEyy run
    (so "S01E01E02" beats "S01E01"), plus one for a trailing group.
    """
    tags = {tag.lower() for tag in _DETAIL_TAG.findall(name)}
    extra_episodes = sum(max(0, len(_EPISODE_NUMBER.findall(tag)) - 1) for tag in tags)
    return len(tags) + extra_episodes + (1 if _ENDS_WITH_GROUP.search(name) else 0)


def _unwrap_if_release(inner):
    return inner if _looks_like_release(inner) else " "


def _subject_remainder(title):
    """``title`` with quoted filenames, counters, sizes and the yEnc tail removed."""
    remainder = _QUOTED.sub(" ", title)
    previous = None
    while previous != remainder:
        previous = remainder
        remainder = _SUBJECT_NOISE.sub(" ", remainder)
    # A "[a]-[b]-[c]" wrapper chain (WtFnZb style) loses its wrapper brackets
    # ("[PRiVATE]", "[WtFnZb]"); a chain bracket holding the release name is
    # unwrapped. Other brackets ("[SubsPlease] Show") stay.
    remainder = _CHAIN_BRACKET.sub(
        lambda m: _unwrap_if_release(next(g for g in m.groups() if g is not None)),
        remainder,
    )
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
    named = _BRACKET_NAME.search(title)
    chain_name = _strip_file_suffix(named.group(1)) if named else ""
    if chain_name:
        candidates.append(chain_name)
    candidates += [_strip_file_suffix(b) for b in _BRACKETED.findall(title)]
    remainder = _subject_remainder(title)
    # A WtFnZb chain that names the release needs no remainder; a chain
    # bracket holding anything else ("[foo]-[1/5]") leaves it in play.
    if not (chain_name and _looks_like_release(chain_name)):
        candidates.append(remainder)
    best = None
    for candidate in candidates:
        if candidate and _looks_like_release(candidate):
            if best is None or _richer(candidate, best):
                best = candidate
    return best or remainder or title


def without_media_extension(name):
    """``name`` without a trailing media extension (".mkv", ".mp4", ...)."""
    return _MEDIA_EXTENSION.sub("", str(name or ""))


def _tokens(name):
    """The words of ``name`` as a multiset, so a dropped repeated word counts."""
    return collections.Counter(
        token.lower()
        for token in _TOKEN_SPLIT.split(_MEDIA_EXTENSION.sub("", name))
        if token
    )


def _strictly_contains(outer, inner):
    """Whether multiset ``outer`` holds all of ``inner`` and more."""
    return outer != inner and not inner - outer


def _richer(candidate, best):
    """Whether ``candidate`` carries more of the release than ``best``.

    A name holding every word of the other plus words outside brackets wins
    (an outer name beats an archive filename that dropped a title word, a
    language, a variant or an episode); a strict subset never wins; otherwise
    the one with more recognized detail wins.
    """
    mine, theirs = _tokens(candidate), _tokens(best)
    if mine != theirs and not mine - theirs:  # a strict subset never wins
        return False
    if _strictly_contains(mine, theirs):
        # Extra words inside brackets are wrappers ("[PRiVATE]", "[N3wZ]");
        # extra words outside them are release detail an archive name dropped.
        wrapped = collections.Counter()
        for inner in _BRACKETED.findall(candidate):
            wrapped |= _tokens(inner)
        if (mine - theirs) - wrapped:
            return True
    return _detail(candidate) > _detail(best)
