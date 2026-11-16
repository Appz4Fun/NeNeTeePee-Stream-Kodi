# SPDX-License-Identifier: GPL-3.0-or-later
"""Which listed releases their own REPACK or PROPER replaces.

A group that finds a fault in its release posts it again as a REPACK (or a
RERIP, which the parser reads as one), or as a PROPER. Both stay listed: the
first file and its replacement are different releases to the same-release
rule (``release_match.same_release``), so neither is a copy of the other.
The picker strikes the replaced one through.

A row is replaced when the list holds another row that is the same release
in everything but the REPACK / PROPER tag (title, year or episode, edition,
resolution, HDR, audio, channels, codec, source, HYBRID, group, and the
rest of ``release_match.release_profile``), carries one of the two tags, and
the row itself carries neither. A group is needed on both, as everywhere the
same-release rule is used: another group's PROPER is a different encode, not
this file fixed. A REPACK and a PROPER of one release do not replace each
other, and a REPACK2 beside a REPACK is left alone: the parser keeps no
count, so the two read as one release already.
"""

from resources.lib.release_display import PROPER_LABEL, REPACK_LABEL

_REVISION_FLAGS = ("repack", "proper")


def revision_label(meta):
    """ "REPACK", "PROPER" or "" for a row's parsed metadata.

    A name with both tags reads as a REPACK, the later of the two.
    """
    if not isinstance(meta, dict):
        return ""
    if meta.get("repack"):
        return REPACK_LABEL
    return PROPER_LABEL if meta.get("proper") else ""


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


def _replacement(row, revised):
    """The tag of the first of ``revised`` that replaces ``row``, or ""."""
    from resources.lib.release_match import same_release

    untagged = _untagged(row)
    for fixed in revised:
        if same_release(untagged, _untagged(fixed)):
            return revision_label(fixed.get("_meta"))
    return ""


def _candidates(rows):
    """The rows of ``rows`` that can take part: parsed, and not synthetic."""
    from resources.lib import fallback_streams as _fs

    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("_display_title"):
            continue
        try:
            out.append((row, revision_label(_fs._result_meta(row))))
        except Exception:  # pylint: disable=broad-except
            continue
    return out


def superseded_rows(rows):
    """``{id(row): "REPACK" | "PROPER"}`` for each row of ``rows`` replaced
    by another row of ``rows``; the value is the replacement's tag.

    Fail-closed: a row that cannot be read is never struck through, and a
    synthetic row (the local season pack) takes no part. A list with no
    REPACK or PROPER in it, which is most of them, costs one look at each
    row's parsed tags.
    """
    candidates = _candidates(rows)
    if not any(label for _row, label in candidates):
        return {}
    buckets = {}
    for row, label in candidates:
        try:
            key = _bucket_key(row)
        except Exception:  # pylint: disable=broad-except
            continue
        if key is None:
            continue
        plain, revised = buckets.setdefault(key, ([], []))
        (revised if label else plain).append(row)
    out = {}
    for plain, revised in buckets.values():
        if not plain or not revised:
            continue
        for row in plain:
            try:
                label = _replacement(row, revised)
            except Exception:  # pylint: disable=broad-except
                label = ""
            if label:
                out[id(row)] = label
    return out
