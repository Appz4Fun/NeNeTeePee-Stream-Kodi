# SPDX-License-Identifier: GPL-3.0-or-later
"""The one rule for "these NZBs are the same release".

NZBGet duplicate backups (``router_play._same_release_backups``, the loader
extras in ``nzbget_resolver_dupes``) and the picker's copy grouping all ask
``same_release``. Two rows are the same release when:

* they are the same content (``fallback_streams._same_content``: title, year
  or SxxEyy, part, edition, PROPER/REPACK), with a movie's year equal on both
  sides, absent included; and
* every parsed release field is equal (``release_profile``): resolution,
  HDR set, audio set, channels, codec, quality, edition, PROPER, REPACK,
  HYBRID, the 3D / dubbed / hardcoded-subs / cut flags, and the group; and
* the group is known. A row with no group is never grouped with another
  (a literal ``NOGRP`` or ``P2P`` tag is a group like any other); and
* a daily show's air date is equal. A dated name ("Show.2026.11.12") has no
  season or episode for the content gate to compare, so two nights of one
  show would otherwise be one release.

A container (".mkv", ".mp4") splits a release only when both rows name one.
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
# Per-row cache of (title, profile): sorting compares one row many times.
_PROFILE_CACHE_KEY = "_release_profile"


def _fold(value):
    return str(value or "").strip().casefold()


def title_key(title):
    """A parsed title with everything but letters and digits removed.

    ``Journey's End`` normalizes to "journey s end" and ``Journeys End`` to
    "journeys end"; both are "journeysend" here, so an apostrophe an indexer
    dropped does not make two titles.
    """
    return "".join(ch for ch in str(title or "").casefold() if ch.isalnum())


def variant_tags(parsed):
    """The 3D, dub, hardsub and cut flags set in a PTT result, as sorted names.

    ``filter.parse_title_metadata`` stores these as ``meta["variant"]`` so
    ``same_release`` never reparses a title.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    return sorted(key for key in _VARIANT_FLAGS if parsed.get(key))


def _variant_flags(row, meta):
    """``meta["variant"]`` when the parse stored it, else a PTT parse of the title."""
    stored = meta.get("variant")
    if isinstance(stored, list):
        return tuple(sorted(stored))
    from resources.lib.ptt import parse_title
    from resources.lib.release_subject import release_name_from_subject

    return tuple(variant_tags(parse_title(release_name_from_subject(row.get("title")))))


def is_hybrid(row, meta):
    """``meta["hybrid"]`` when the parse stored it, else read from the title.

    Metadata built before HYBRID was parsed (or by hand) has no such key;
    reading the title keeps both sides of a comparison on the same answer.
    """
    stored = meta.get("hybrid")
    if isinstance(stored, bool):
        return stored
    from resources.lib.filter_tags import detect_hybrid
    from resources.lib.release_subject import release_name_from_subject

    return detect_hybrid(release_name_from_subject(row.get("title")))


def air_date(row, meta):
    """The air date of a dated release with no SxxEyy ("2026-11-12"), or "".

    Read from the stored PTT identity, else from a PTT parse of the title.
    A row that names an episode is told apart by it, so its date is ignored:
    one listing adding the date to "S01E02" is still the same episode.
    """
    from resources.lib import fallback_streams as _fs

    _title, _year, seasons, episodes, _part = _fs._release_identity(row)
    if seasons or episodes:
        return ""
    stored = meta.get("ptt_identity")
    if isinstance(stored, dict) and isinstance(stored.get("date"), str):
        return stored["date"]
    from resources.lib.ptt import parse_title
    from resources.lib.release_subject import release_name_from_subject

    parsed = parse_title(release_name_from_subject(row.get("title")))
    return str(parsed.get("date") or "") if isinstance(parsed, dict) else ""


def _network(row, meta):
    """The streaming service of a WEB release, read from the name if unparsed."""
    if "network" in meta:
        return _fold(meta.get("network"))
    from resources.lib.filter_tags import detect_network
    from resources.lib.release_subject import release_name_from_subject

    return _fold(detect_network(release_name_from_subject(row.get("title"))))


def release_profile(row):
    """The parsed release fields of ``row`` that ``same_release`` compares.

    A hashable tuple of ``(name, value)`` pairs; equal profiles mean equal
    resolution, HDR, audio, channels, codec, quality, edition, PROPER/REPACK,
    HYBRID, air date, variant flags and group.
    """
    from resources.lib import fallback_streams as _fs

    title = row.get("title")
    cached = row.get(_PROFILE_CACHE_KEY)
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == title:
        return cached[1]
    meta = _fs._result_meta(row)
    fields = [(key, _fold(meta.get(key))) for key in _STRING_FIELDS]
    fields = [
        # A remux is taken from a disc, so one name saying "Remux" and another
        # "BluRay.Remux" name the same source.
        (key, "bluray remux" if key == "quality" and value == "remux" else value)
        for key, value in fields
    ]
    fields.append(("network", _network(row, meta)))
    for key in _SET_FIELDS:
        values = meta.get(key) or []
        if isinstance(values, str):
            values = [values]
        fields.append((key, tuple(sorted({_fold(v) for v in values if v}))))
    fields.extend((key, bool(meta.get(key))) for key in _FLAG_FIELDS)
    fields.append(("hybrid", is_hybrid(row, meta)))
    fields.append(("date", air_date(row, meta)))
    fields.append(("variant", _variant_flags(row, meta)))
    profile = tuple(fields)
    row[_PROFILE_CACHE_KEY] = (title, profile)
    return profile


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


def _containers_conflict(pick, row):
    """Both rows name a container (".mkv", ".mp4") and they differ.

    Most listings name none, so an unknown container never splits a release;
    a known .mkv and a known .mp4 are different files.
    """
    from resources.lib import fallback_streams as _fs

    left = _fold(_fs._result_meta(pick).get("container"))
    right = _fold(_fs._result_meta(row).get("container"))
    return bool(left and right and left != right)


def _titles_differ(pick, row):
    """The two names carry different parsed titles.

    ``_same_content`` compares titles loosely, because the stream proxy checks
    a switch byte for byte: one extra word on either side passes when the
    episode numbers agree. Here nothing checks afterwards, and that let one
    group's ``The.Sopranos.S02E01`` and ``The.Office.US.S02E01`` (or ``House``
    and ``House.of.the.Dragon``) count as one release, so one show could be
    listed as, or sent to NZBGet as a backup of, the other. A group names its
    own release one way, so the titles must be equal.
    """
    from resources.lib import fallback_streams as _fs

    return title_key(_fs._release_identity(pick)[0]) != title_key(
        _fs._release_identity(row)[0]
    )


def same_release(pick, row):
    """Whether ``row`` is the same release as ``pick``. Fail-closed."""
    from resources.lib import fallback_streams as _fs

    try:
        # The cached profile comparison is cheap and rejects most pairs, so it
        # runs before the content gate.
        profile = release_profile(pick)
        if not dict(profile)["group"] or profile != release_profile(row):
            return False
        if _containers_conflict(pick, row) or _titles_differ(pick, row):
            return False
        return bool(_fs._same_content(pick, row) and _movie_years_match(pick, row))
    except Exception:  # pylint: disable=broad-except
        return False
