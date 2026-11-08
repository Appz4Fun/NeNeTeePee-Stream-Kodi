# SPDX-License-Identifier: GPL-3.0-or-later
"""The one rule for "these NZBs are the same release".

NZBGet duplicate backups (``router_play._same_release_backups``, the loader
extras in ``nzbget_resolver_dupes``) and the picker's copy grouping all ask
``same_release``. Two rows are the same release when:

* they are the same content (``fallback_streams._same_content``: title, year
  or SxxEyy, part, edition, PROPER/REPACK), with a movie's year equal on both
  sides, absent included; and
* every parsed release field is equal (``release_profile``): resolution,
  HDR set, audio set, channels, codec, quality, edition, PROPER, REPACK, the
  3D / dubbed / hardcoded-subs / cut flags, and the group; and
* the group is known. A row with no group is never grouped with another
  (a literal ``NOGRP`` or ``P2P`` tag is a group like any other).

Languages and soft subtitles are not compared: they don't make a different
video. An unknown value only equals another unknown: a row that names a codec
is a different release from one that names none.
"""

_VARIANT_FLAGS = (
    "3d",
    "dubbed",
    "hardcoded",
    "extended",
    "unrated",
    "uncensored",
    "remastered",
)
_STRING_FIELDS = (
    "resolution",
    "channels",
    "codec",
    "quality",
    "edition",
    "group",
)
_SET_FIELDS = ("hdr", "audio")
_FLAG_FIELDS = ("proper", "repack")


def _fold(value):
    return str(value or "").strip().casefold()


def _variant_flags(title):
    """PTT's 3D, dub, hardsub and cut flags for the release name in ``title``."""
    from resources.lib.ptt import parse_title
    from resources.lib.release_subject import release_name_from_subject

    parsed = parse_title(release_name_from_subject(title))
    return tuple(bool(parsed.get(key)) for key in _VARIANT_FLAGS)


def release_profile(row):
    """The parsed release fields of ``row`` that ``same_release`` compares.

    A hashable tuple of ``(name, value)`` pairs; equal profiles mean equal
    resolution, HDR, audio, channels, codec, quality, edition, PROPER/REPACK,
    variant flags and group.
    """
    from resources.lib import fallback_streams as _fs

    meta = _fs._result_meta(row)
    fields = [(key, _fold(meta.get(key))) for key in _STRING_FIELDS]
    for key in _SET_FIELDS:
        values = meta.get(key) or []
        if isinstance(values, str):
            values = [values]
        fields.append((key, tuple(sorted({_fold(v) for v in values if v}))))
    fields.extend((key, bool(meta.get(key))) for key in _FLAG_FIELDS)
    fields.append(("variant", _variant_flags(row.get("title"))))
    return tuple(fields)


def _movie_years_match(pick, row):
    """For a movie pick, both parsed years must be equal -- absent included.

    ``_same_content`` lets a missing year match any year because the stream
    proxy byte-verifies a switch; an NZBGet failover has no such check, so a
    yearless ``Dune.1080p`` must never back up ``Dune.1984``. Episodes keep
    the content gate's rules.
    """
    from resources.lib import fallback_streams as _fs

    _title, pick_year, seasons, episodes, _part = _fs._release_identity(pick)
    if seasons or episodes:
        return True
    return pick_year == _fs._release_identity(row)[1]


def same_release(pick, row):
    """Whether ``row`` is the same release as ``pick``. Fail-closed."""
    from resources.lib import fallback_streams as _fs

    try:
        if not (_fs._same_content(pick, row) and _movie_years_match(pick, row)):
            return False
        profile = release_profile(pick)
        return bool(dict(profile)["group"]) and profile == release_profile(row)
    except Exception:  # pylint: disable=broad-except
        return False
