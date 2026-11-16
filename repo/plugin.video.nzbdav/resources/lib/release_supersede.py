# SPDX-License-Identifier: GPL-3.0-or-later
"""Which listed releases their own REPACK or PROPER replaces.

A group that finds a fault in its release posts it again as a REPACK (or a
RERIP, which reads as one), or as a PROPER. Both stay listed: the first file
and its replacement are different releases to the same-release rule
(``release_match.same_release``), so neither is a copy of the other. The
picker strikes the replaced one through.

A row is replaced when the list holds another row that carries the tag
(``filter_tags.detect_revision``: written as a tag, not a word of an episode
title) and is the same release in everything else: title, year or episode,
edition, resolution, HDR, audio, channels, codec, source, HYBRID, group and
the rest of ``release_match.release_profile``; and the row itself carries no
such tag. A group is needed on both, as everywhere the same-release rule is
used: another group's PROPER is a different encode, not this file fixed. A
REPACK and a PROPER of one release do not replace each other, and a REPACK2
beside a REPACK is left alone: no count is kept, so the two read as one
release already.
"""

from resources.lib.release_display import PROPER_LABEL, REPACK_LABEL

_REVISION_FLAGS = ("repack", "proper")
_LABELS = {"repack": REPACK_LABEL, "proper": PROPER_LABEL}


def revision_label(row, meta=None):
    """ "REPACK", "PROPER" or "": the re-release tag of a picker row.

    ``meta["revision"]`` when the parse stored it, else read from the name:
    metadata parsed before the tag was kept (a cached search), or built by
    hand, has no such key.
    """
    if not isinstance(meta, dict):
        from resources.lib import fallback_streams as _fs

        meta = _fs._result_meta(row)
    stored = meta.get("revision")
    if not isinstance(stored, str):
        from resources.lib.filter_tags import detect_revision
        from resources.lib.release_subject import release_name_from_subject

        stored = detect_revision(release_name_from_subject(row.get("title")))
    return _LABELS.get(stored, "")


def _untagged(row):
    """A copy of ``row`` with the REPACK and PROPER tags taken off.

    ``same_release`` on two such copies is the same-release rule with the
    two tags left out of the comparison, and nothing else changed.
    """
    from resources.lib import fallback_streams as _fs
    from resources.lib.release_match import _PROFILE_CACHE_KEY

    meta = dict(_fs._result_meta(row))
    for flag in _REVISION_FLAGS:
        meta[flag] = False
    copy = dict(row)
    copy.pop(_PROFILE_CACHE_KEY, None)
    copy["_meta"] = meta
    return copy


def _bucket_key(row):
    """What two rows of one release share, or None for a row that has none.

    The release profile with the two tags left out, and the episode set, as
    the picker's own grouping keys rows (``results_groups._grouping_key``);
    the title and year checks stay in ``same_release``. Read from the row's
    own cached profile, which the picker's grouping has filled already.
    """
    from resources.lib import fallback_streams as _fs
    from resources.lib.release_match import release_profile

    profile = tuple(
        (name, False if name in _REVISION_FLAGS else value)
        for name, value in release_profile(row)
    )
    if not dict(profile).get("group"):
        return None
    _title, _year, seasons, episodes, _part = _fs._release_identity(row)
    return (
        profile,
        tuple(episodes),
        tuple(seasons) if episodes else None,
        bool(row.get("_season_pack")),
    )


def _candidates(rows):
    """``(row, tag)`` for the rows of ``rows`` that can take part: readable,
    and not synthetic."""
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("_display_title"):
            continue
        try:
            out.append((row, revision_label(row)))
        except Exception:  # pylint: disable=broad-except
            continue
    return out


def _replaced_in(plain, revised):
    """``{id(row): tag}`` for the rows of ``plain`` a row of ``revised``
    replaces. ``revised`` is ``(row, tag)`` pairs of one bucket.

    When a REPACK and a PROPER both replace a row, the REPACK is named, as
    on a single name that carries both tags.
    """
    from resources.lib.release_match import same_release

    # One copy of each: its release profile is worked out once, not per pair.
    fixed = sorted(
        ((_untagged(row), tag) for row, tag in revised),
        key=lambda pair: pair[1] != REPACK_LABEL,
    )
    out = {}
    for row in plain:
        untagged = _untagged(row)
        for candidate, tag in fixed:
            if same_release(untagged, candidate):
                out[id(row)] = tag
                break
    return out


def superseded_rows(rows):
    """``{id(row): "REPACK" | "PROPER"}`` for each row of ``rows`` replaced
    by another row of ``rows``; the value is the replacement's tag.

    Fail-closed: a row that cannot be read is never struck through, and a
    synthetic row (the local season pack) takes no part. A list with no
    REPACK or PROPER in it, which is most of them, costs one look at each
    row's parsed tag.
    """
    candidates = _candidates(rows)
    if not any(tag for _row, tag in candidates):
        return {}
    buckets = {}
    for row, tag in candidates:
        try:
            key = _bucket_key(row)
        except Exception:  # pylint: disable=broad-except
            continue
        if key is None:
            continue
        plain, revised = buckets.setdefault(key, ([], []))
        if tag:
            revised.append((row, tag))
        else:
            plain.append(row)
    out = {}
    for plain, revised in buckets.values():
        if not plain or not revised:
            continue
        try:
            out.update(_replaced_in(plain, revised))
        except Exception:  # pylint: disable=broad-except
            continue
    return out
