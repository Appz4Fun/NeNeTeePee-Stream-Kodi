# SPDX-License-Identifier: GPL-3.0-or-later
"""Which releases are tagged as an upscale: by AI, or by any other means.

An upscale is video stretched to a higher resolution than its source, so it
is not a genuine release of the resolution its name states. Only a tag
counts. "AI" in the title ("A.I. Artificial Intelligence", "The AI Doc") or
inside a word ("Air", "Samurai", the group "RAiN") is not one.
"""

import re

from resources.lib.release_subject import release_name_from_subject


def _pattern(expression):
    return re.compile(r"(?<![a-z0-9])(?:" + expression + r")(?![a-z0-9])", re.I)


_SEP = r"[ ._-]*"
_UPSCALE = r"upscal(?:e[dr]?|ing)|upres(?:sed)?|upconvert(?:ed)?"
_AI = r"a\.?i"

# "AI Upscale", "A.I. Upscaled", "Upscaled by AI": no title reads so, so the
# pair counts wherever it stands in the name.
_AI_UPSCALE = _pattern(
    _AI
    + _SEP
    + r"(?:"
    + _UPSCALE
    + r")|(?:"
    + _UPSCALE
    + r")"
    + _SEP
    + r"(?:(?:by|with|via|using)"
    + _SEP
    + r")?"
    + _AI
)
# The group that posts AI upscales of older films and anime.
_AI_RAWS = _pattern(r"ai[-_. ]?raws")
# A release group that is "AI" or has it as a word of its name: "AI",
# "AI-Raws", "Upscale_AI". "RAiN" and "AiR" hold the letters, not the word.
_AI_GROUP = re.compile(r"(?:.*[-_. ])?ai(?:[-_. ].*)?", re.I)

# Where a name's title ends and its tags begin: a year, SxxEyy or a
# resolution (as ``filter_tags._TAGS_START``).
_TAGS_START = _pattern(
    r"(?:19|20)\d{2}|s\d{1,2}(?:e\d{1,3})*|\d{1,2}x\d{2,3}|\d{3,4}[pi]|[48]k"
)
# An upscale tag with no "AI" beside it: "UPSCALED", "UpsUHD". After the
# tags begin only; before, the word belongs to the title.
_PLAIN_UPSCALE = _pattern(_UPSCALE + r"|ups(?:uhd|fhd|hd|4k)")

# A bare "AI" is a tag only where nothing but tags can stand: after the
# first technical tag, or between a film's year and that tag when a tag
# follows it directly ("1994.AI.2160p"; "Horizon.2023.AI.Revolution.1080p"
# goes on with its title). An episode's own title follows its number
# ("Show.S02E03.AI.1080p"), so there only the technical tags open that part.
_TECH = (
    r"\d{3,4}[pi]|\d{3,4}x\d{3,4}|[48]k|uhd|blu-?ray|bd(?:rip|remux)|remux"
    r"|web-?(?:dl|rip)|hdtv|dvdrip|[xh]\.?26[45]|hevc|av1"
)
_TECH_START = _pattern(_TECH)
_YEAR = _pattern(r"(?:19|20)\d{2}")
_EPISODE = re.compile(
    r"(?<![a-z0-9])(?:s\d{1,2}(?:[ ._-]?e\d{1,3})*|\d{1,2}x\d{2,3}"
    r"|(?:19|20)\d{2}[ ._-]\d{2}[ ._-]\d{2}|season|episode)(?![a-z0-9])"
    # "[Group] Show - 07 [1080p]"
    r"|(?<=[ ._])-[ ._]?\d{1,4}(?:v\d)?(?=[ ._\[(]|$)",
    re.I,
)
# "AI subs", "AI dub": machine-made subtitles or voices, not upscaled video.
_BARE_AI = _pattern(
    r"ai(?!" + _SEP + r"(?:sub|dub|voice|translat|narrat|audio|tts|caption))"
)
# A bare "AI" with a tag right after it: "AI.2160p", "AI.Remaster".
_AI_BEFORE_TAG = _pattern(
    r"ai(?=[ ._()\[\]-]*(?:"
    + _TECH
    + r"|remaster(?:ed)?|enhanced?|restor(?:ed|ation))(?![a-z0-9]))"
)


def _bare_ai_tag(name):
    """Whether a bare "AI" stands among the name's technical tags."""
    tech = _TECH_START.search(name)
    if tech and _BARE_AI.search(name, tech.end()):
        return True
    if not tech or _EPISODE.search(name):
        return False
    # The year of a film, not a number its title opens with ("2001: A Space
    # Odyssey").
    year = next((m for m in _YEAR.finditer(name) if 0 < m.start() < tech.start()), None)
    return bool(year and _AI_BEFORE_TAG.search(name, year.end(), tech.end()))


def detect_ai_upscale(name, group=""):
    """Whether the release name is tagged as an upscale.

    ``name`` is the release name (not a raw Usenet subject) and ``group``
    its parsed release group. True for an AI-upscale group ("-AI-Raws",
    or "-AI" ending a name that has tags), "AI" beside an upscale word
    anywhere, an upscale tag ("UPSCALED") after the year, episode or
    resolution, and a bare "AI" among the technical tags ("2160p.AI.x265").
    "AI" in the title is not a tag.
    """
    name = str(name or "")
    if _AI_UPSCALE.search(name) or _AI_RAWS.search(name):
        return True
    tags = _TAGS_START.search(name)
    if tags and _PLAIN_UPSCALE.search(name, tags.end()):
        return True
    # What ends a name with no tag in it ("Ghost in the Shell - AI") is
    # read as a group, and is more likely the end of the title.
    tagged = bool(tags or _TECH_START.search(name))
    if tagged and _AI_GROUP.fullmatch(str(group or "").strip()):
        return True
    return _bare_ai_tag(name)


def is_ai_upscale(row):
    """``_meta["ai_upscale"]`` when the parse stored it, else read the title.

    Metadata built before the tag was parsed (or by hand) has no such key.
    """
    meta = row.get("_meta")
    meta = meta if isinstance(meta, dict) else {}
    stored = meta.get("ai_upscale")
    if isinstance(stored, bool):
        return stored
    return detect_ai_upscale(
        release_name_from_subject(row.get("title") or ""), meta.get("group")
    )
