# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Result filtering and sorting using PTT for title parsing."""

import collections
import datetime
import itertools
import math
import os
import re
import statistics
import time
from copy import deepcopy
from types import SimpleNamespace

import xbmc
import xbmcaddon  # noqa: F401  pylint: disable=unused-import  (module-scope Kodi imports are the repo convention; tests patch <mod>.xbmcaddon and the thread-safety contract tests forbid its use on no-getter paths)
import xbmcgui  # re-exported via __all__ for callers/tests; not used here

from resources.lib import nfo_audio, parse_store, release_index, telemetry
from resources.lib.filter_atmos import share_atmos
from resources.lib.filter_audio import audio_tier
from resources.lib.filter_fallback import (
    _fallback_audio,
    _fallback_hdr,
    _fallback_parse,
    _fallback_quality,
    _fallback_year,
)
from resources.lib.filter_groups import ALL_RELEASE_GROUPS, configure_groups_dialog
from resources.lib.filter_languages import language_filter_values
from resources.lib.filter_normalize import (
    _normalize_fallback_meta,
    _normalize_parsed_meta,
)
from resources.lib.filter_options import (
    AUDIO_SETTINGS as _AUDIO_SETTINGS,
)
from resources.lib.filter_options import (
    BOOLEAN_SETTINGS,
    UNKNOWN_SETTINGS,
    values_filter_pass,
)
from resources.lib.filter_options import (
    CODEC_SETTINGS as _CODEC_SETTINGS,
)
from resources.lib.filter_options import (
    HDR_SETTINGS as _HDR_SETTINGS,
)
from resources.lib.filter_options import (
    LANGUAGE_SETTINGS as _LANGUAGE_SETTINGS,
)
from resources.lib.filter_options import (
    RESOLUTION_SETTINGS as _RESOLUTION_SETTINGS,
)
from resources.lib.filter_payload import ADOPTED_KEY as _ADOPTED_KEY
from resources.lib.filter_payload import group_names, unify_one_payload
from resources.lib.filter_remux import (
    DEFAULT_REMUX_GROUPS,
    compile_remux_tiers,
    pinned_group_rank,
    read_remux_tiers,
    remux_group_rank,
    remux_priority,
)
from resources.lib.filter_tags import (
    restore_glued_tags,
    supplement_metadata,
    trailing_word,
)
from resources.lib.filter_upscale import is_ai_upscale
from resources.lib.release_subject import name_form_key as _name_form_key

# Re-exported so ``resources.lib.filter.<name>`` keeps resolving for callers
# and tests after the fallback parser, groups dialog, and metadata
# normalization moved to sibling modules (``filter_fallback`` /
# ``filter_groups`` / ``filter_normalize``).
__all__ = [
    "xbmcgui",
    "ALL_RELEASE_GROUPS",
    "configure_groups_dialog",
    "_fallback_audio",
    "_fallback_hdr",
    "_fallback_parse",
    "_fallback_quality",
    "_fallback_year",
    "_normalize_fallback_meta",
    "_normalize_parsed_meta",
]

# The complete set of keys produced by ``parse_title_metadata``. A cached
# ``_meta`` dict is only safe to reuse (skipping a reparse) when it satisfies
# this FULL contract—downstream consumers such as
# ``fallback_streams_identity`` trust any dict found in ``_meta`` and never
# reparse, so a partial dict would silently drop quality/edition/year/
# upscaled/container/etc. from the fallback pipeline.
_FILTER_META_STR_KEYS = (
    "resolution",
    "codec",
    "group",
    "quality",
    "edition",
    "channels",
    "container",
)
_FILTER_META_LIST_KEYS = ("hdr", "audio", "languages")
_FILTER_META_BOOL_KEYS = ("proper", "repack", "upscaled")
_FILTER_META_INT_KEYS = ("year",)
_FILTER_META_KEYS = frozenset(
    _FILTER_META_STR_KEYS
    + _FILTER_META_LIST_KEYS
    + _FILTER_META_BOOL_KEYS
    + _FILTER_META_INT_KEYS
)

DEFAULT_EXCLUDED_GROUPS = {
    "4KDVS",
    "B0MBARDiERS",
    "Ben The Men",
    "BHDstudio",
    "BiTOR",
    "c0kE",
    "ENDSTATiON",
    "Gungnir",
    "HDS",
    "HSaber",
    "NUXWIO",
    "Ralphy",
    "SESKAPiLE",
    "SPx",
    "STRiKES",
    "SURCODE",
    "TW",
    "WiKi",
    "ZAX",
}


def _collect_enabled(addon, pairs):
    """Return labels for settings that are enabled (true).

    Args:
        addon: Kodi addon instance
        pairs: list of (setting_id, label) tuples
    """
    return [
        label
        for setting_id, label in pairs
        if (addon.getSetting(setting_id) or "true").lower() == "true"
    ]


def _csv_setting(addon, key):
    """Read a comma-separated setting into a stripped list."""
    val = addon.getSetting(key).strip()
    if not val:
        return []
    return [x.strip() for x in val.split(",") if x.strip()]


def _int_setting(addon, key, default):
    """Read an integer Kodi setting with a safe fallback.

    Tries plain ``int()`` first so a clean integer string like "500"
    parses without floating-point noise; on failure (for example, user typed
    "1.5" because the size field accepts decimals on some Kodi
    skins), falls through to ``int(float(raw))`` so the caller sees
    a clear truncated value (1) instead of the silent default (0).
    """
    raw = addon.getSetting(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    try:
        parsed = float(raw)
        if not math.isfinite(parsed):
            return default
        return int(parsed)
    except (OverflowError, TypeError, ValueError):
        return default


def _resolve_size_bounds(addon):
    """Read min/max size, disabling the filter on an inverted range."""
    min_size = _int_setting(addon, "filter_min_size", 0)
    max_size = _int_setting(addon, "filter_max_size", 0)
    if 0 < max_size < min_size:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: filter_min_size={} exceeds filter_max_size={}; "
            "disabling size filter".format(min_size, max_size),
            xbmc.LOGWARNING,
        )
        min_size = 0
        max_size = 0
    return min_size, max_size


def _get_filter_settings(settings_getter=None):
    """Read filter settings from Kodi addon config."""
    if settings_getter is None:
        # Disk read, not the xbmcaddon binding: filter_results runs on
        # fallback-loader worker threads, where concurrent binding reads
        # race Kodi's lazy settings load (TinyXML SIGSEGV).
        from resources.lib.router import _get_script_setting as settings_getter
    addon = SimpleNamespace(
        getSetting=lambda key: settings_getter(
            key, "true" if key in BOOLEAN_SETTINGS else ""
        )
    )

    resolutions = _collect_enabled(addon, _RESOLUTION_SETTINGS)
    hdr = _collect_enabled(addon, _HDR_SETTINGS)
    audio = _collect_enabled(addon, _AUDIO_SETTINGS)
    codecs = _collect_enabled(addon, _CODEC_SETTINGS)
    languages = _collect_enabled(addon, _LANGUAGE_SETTINGS)

    min_size, max_size = _resolve_size_bounds(addon)

    return {
        **{
            name: (addon.getSetting(key) or "true").lower() == "true"
            for name, key in UNKNOWN_SETTINGS.items()
        },
        "remux_tiers": read_remux_tiers(
            settings_getter or (lambda key, default: addon.getSetting(key))
        ),
        "resolutions": resolutions,
        "hdr": hdr,
        "audio": audio,
        "codecs": codecs,
        "languages": languages,
        "exclude_keywords": [
            k.lower() for k in _csv_setting(addon, "filter_exclude_keywords")
        ],
        "require_keywords": [
            k.lower() for k in _csv_setting(addon, "filter_require_keywords")
        ],
        "exclude_release_group": [
            g.lower() for g in _csv_setting(addon, "filter_exclude_release_group")
        ],
        "min_size": min_size,
        "max_size": max_size,
        # Off unless switched on: a missing value reads as "".
        "hide_ai_upscales": (addon.getSetting("filter_hide_ai_upscales") or "").lower()
        == "true",
        "sort_order": _int_setting(addon, "sort_order", 0),
        "max_results": _int_setting(addon, "max_results", 25),
    }


_REVISION_TAG = re.compile(
    r"(?:s\d{1,2}e\d{1,3}|[\s._-]\d{1,4})\s?v([2-9])(?![a-z0-9])", re.IGNORECASE
)


_BRACKETED_EPISODE = re.compile(
    r"(?<=[^\W\d_])[ ._]?-[ ._]?(\d{1,4})(?:v\d)?[ ._]*(?=[\[(])", re.IGNORECASE
)
_INTERNAL_TAG = re.compile(r"(?<![a-z0-9])internal(?![a-z0-9])", re.IGNORECASE)
_MINI_TAG = re.compile(r"(?<![a-z0-9])m(?:720|1080|2160)p(?![a-z0-9])", re.IGNORECASE)


def parse_title_metadata(title):
    """Parse a scene title and return normalized metadata dict.

    A raw Usenet subject (``[1/3] - "Name.mkv" yEnc``) is parsed as the
    release name inside it. ``meta["variant"]`` holds PTT's 3D, dub, hardsub
    and cut flags (``release_match.variant_tags``), so the same-release rule
    never reparses the title. ``meta["hybrid"]`` is the HYBRID tag
    (``filter_tags.detect_hybrid``).
    """
    from resources.lib.release_match import variant_tags
    from resources.lib.release_subject import release_name_from_subject

    name = release_name_from_subject(title)
    # "HDR10x265": the parser reads season 10, episode 265 unless the lost
    # separator is put back first.
    title = restore_glued_tags(name)
    try:
        from resources.lib.ptt import parse_title

        parsed = parse_title(title)
    except Exception as e:
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: PTT parse failed for '{}': {}").format(title, e),
            xbmc.LOGERROR,
        )
        parsed = _fallback_parse(title)
    if (
        title.startswith("[")
        and not parsed.get("seasons")
        and not parsed.get("episodes")
        and not parsed.get("date")
    ):
        # "[Group].Show.Name-07.[1080p...]": the parser reads the episode in
        # "Show Name - 07" but not when the name is written with dots.
        numbered = _BRACKETED_EPISODE.search(title)
        if numbered:
            parsed = dict(parsed, episodes=[int(numbered.group(1))])
    variant = variant_tags(parsed)
    revision = _REVISION_TAG.search(title)
    if revision:
        # "- 05v2" / "S01E05 v2": a re-release that replaces the first file,
        # so not a copy of it (PROPER and REPACK are compared the same way).
        variant = variant + ["v" + revision.group(1)]
    if _INTERNAL_TAG.search(title):
        # A group's iNTERNAL release is a second, separate release.
        variant = variant + ["internal"]
    if _MINI_TAG.search(title):
        # "m1080p": a smaller encode posted beside the full-size one.
        variant = variant + ["mini"]
    variant = sorted(variant)
    # Keyed on the name as listed: that is what the stored identity is
    # looked up by (``fallback_streams._stored_ptt_identity``).
    identity = _ptt_identity(name, parsed)

    if not parsed.get("resolution") and not parsed.get("codec"):
        # PTT returned empty, try fallback
        fallback = _fallback_parse(title)
        if fallback.get("resolution") or fallback.get("codec"):
            parsed = fallback

    # The normalization assumes PTT returned typed data matching its
    # documented contract. If the vendored PTT drifts from that contract
    # (or a custom transformer returns, for example, a dict for hdr), the
    # comprehensions explode with TypeError. Catch that so a single bad
    # release name doesn't stop the whole search; fall back to the
    # regular-expression-only metadata extractor.
    try:
        meta = supplement_metadata(title, _normalize_parsed_meta(parsed))
    except (TypeError, AttributeError, KeyError) as e:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: PTT metadata normalisation failed for '{}': {}; "
            "falling back to regex parse".format(title, e),
            xbmc.LOGWARNING,
        )
        meta = supplement_metadata(
            title, _normalize_fallback_meta(_fallback_parse(title))
        )
    meta["variant"] = variant
    meta["ptt_identity"] = identity
    return meta


def _ptt_identity(name, parsed):
    """The PTT fields ``fallback_streams._release_identity`` needs, for reuse.

    Sorting and grouping compare each row many times; storing these lets the
    content gate skip a second PTT parse of the same name. ``name`` is the
    release name PTT parsed (the subject-cleaned title).
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    try:
        year = int(parsed.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    return {
        "name": name,
        "title": parsed.get("title") or "",
        "year": year,
        "seasons": list(parsed.get("seasons") or []),
        "episodes": list(parsed.get("episodes") or []),
        # The air date of a daily show ("2026-11-12"); such a name has no
        # season or episode, so the date is what marks it as an episode.
        "date": str(parsed.get("date") or ""),
        # A whole-series pack ("Complete.Series") names no season either.
        "complete": bool(parsed.get("complete")),
    }


def matches_filters(result, meta, settings):
    """True iff every configured filter accepts this result.

    Args:
        result: Indexer result dict with at least ``title`` and ``size``.
        meta: Parsed-metadata dict produced by ``parse_title_metadata``
            (``resolution``, ``hdr`` list, ``audio`` list, ``codec``,
            ``languages`` list).
        settings: Filter-settings dict produced by ``_get_filter_settings``
            (label lists, CSV-keyword lists, size bounds).

    Returns:
        ``True`` when the result satisfies every enabled filter,
        ``False`` the first time any filter excludes it. Pure function—
    does not mutate any input.
    """
    return first_rejecting_filter(result, meta, settings) is None


def _group_filter_passes(meta, settings):
    excluded = settings["exclude_release_group"]
    return not (meta["group"] and meta["group"].lower() in excluded)


def _upscale_filter_passes(result, meta, settings):
    """False for a release tagged as an upscale when those are hidden."""
    if not settings.get("hide_ai_upscales"):
        return True
    return not is_ai_upscale({"title": result.get("title"), "_meta": meta})


def first_rejecting_filter(result, meta, settings):
    """Name of the first filter that rejects this result, or ``None``.

    Checks run in ``matches_filters``' historical short-circuit order, so
    the reported reason is the filter that would have rejected first. The
    labels are technical tokens rendered verbatim in the picker's
    ``FILTERED:`` chip (not localized, mirroring the ASCII ``DL`` tag).

    A table of lazy checks (rather than a chain of ``if`` statements) keeps
    this function's own cyclomatic complexity low -- the branching lives in
    each named predicate, which is measured separately.
    """
    checks = (
        (lambda: _resolution_filter_passes(meta, settings), "resolution"),
        (lambda: _hdr_filter_passes(meta, settings), "HDR"),
        (lambda: _audio_filter_passes(meta, settings), "audio"),
        (lambda: _codec_filter_passes(meta, settings), "codec"),
        (lambda: _language_filter_passes(meta, settings), "language"),
        (lambda: _keyword_filters_pass(result["title"].lower(), settings), "keyword"),
        (lambda: _group_filter_passes(meta, settings), "group"),
        (lambda: _upscale_filter_passes(result, meta, settings), "AI upscale"),
        (lambda: _size_filter_passes(result, settings), "size"),
    )
    for passes, label in checks:
        if not passes():
            return label
    return None


def _resolution_filter_passes(meta, settings):
    return values_filter_pass(
        [meta["resolution"]] if meta["resolution"] else [],
        settings["resolutions"],
        _RESOLUTION_SETTINGS,
        settings.get("unknown_resolution", True),
    )


def _hdr_filter_passes(meta, settings):
    return values_filter_pass(
        meta["hdr"],
        settings["hdr"],
        _HDR_SETTINGS,
        settings.get("unknown_hdr", True),
    )


def _audio_filter_passes(meta, settings):
    return values_filter_pass(
        meta["audio"],
        settings["audio"],
        _AUDIO_SETTINGS,
        settings.get("unknown_audio", True),
    )


def _codec_filter_passes(meta, settings):
    return values_filter_pass(
        [meta["codec"]] if meta["codec"] else [],
        settings["codecs"],
        _CODEC_SETTINGS,
        settings.get("unknown_codec", True),
    )


def _language_filter_passes(meta, settings):
    return values_filter_pass(
        language_filter_values(meta["languages"]),
        settings["languages"],
        _LANGUAGE_SETTINGS,
        settings.get("unknown_language", True),
    )


def _keyword_filters_pass(title_lower, settings):
    """True iff exclude/require keyword filters accept this title."""
    for kw in settings["exclude_keywords"]:
        if kw in title_lower:
            return False
    for kw in settings["require_keywords"]:
        if kw not in title_lower:
            return False
    return True


def _size_filter_passes(result, settings):
    """True iff the result's size is within the configured bounds.

    A 0-byte / missing-size placeholder result used to skip both bounds
    because `if result.get("size"):` is falsy for "0" and "". That let
    unparseable / placeholder rows slip past min_size when the user
    wanted to filter them out. Now: reach the size check unconditionally;
    treat unparseable size as 0 MB so a min_size>0 filter rejects it.
    Closes TODO.md §H.3.
    """
    raw_size = result.get("size", "")
    try:
        size_mb = int(raw_size) / 1048576 if raw_size not in (None, "") else 0
    except (ValueError, TypeError):
        size_mb = 0
    if settings["min_size"] > 0 and size_mb < settings["min_size"]:
        return False
    if 0 < settings["max_size"] < size_mb:
        return False
    return True


def _is_str_list(value):
    """True when value is a list whose every item is a str."""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_str(value):
    """True when value is a str."""
    return isinstance(value, str)


def _is_bool(value):
    """True when value is a bool."""
    return isinstance(value, bool)


def _is_int_not_bool(value):
    """True when value is an int but not a bool (bool subclasses int)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _all_match(meta, keys, predicate):
    """True iff ``predicate`` accepts ``meta.get(key)`` for every key."""
    return all(predicate(meta.get(key)) for key in keys)


def _has_filter_metadata_shape(meta):
    """Return True when cached metadata satisfies the full parse contract.

    Validates the complete set of fields produced by
    ``parse_title_metadata`` (not just the ones ``filter_results`` indexes)
    so a partial cached ``_meta`` is reparsed instead of being blindly
    reused and propagated to ``fallback_streams_identity``.
    """
    if not isinstance(meta, dict) or not _FILTER_META_KEYS <= set(meta):
        return False
    # bool is a subclass of int; ``_is_int_not_bool`` rejects a stray bool year.
    return (
        _all_match(meta, _FILTER_META_STR_KEYS, _is_str)
        and _all_match(meta, _FILTER_META_LIST_KEYS, _is_str_list)
        and _all_match(meta, _FILTER_META_BOOL_KEYS, _is_bool)
        and _all_match(meta, _FILTER_META_INT_KEYS, _is_int_not_bool)
    )


def _resolve_result_meta(result, parsed_by_title):
    """Return the metadata for a result, reusing the per-title cache."""
    meta = result.get("_meta")
    title = result["title"]
    if _has_filter_metadata_shape(meta):
        # A parse taken from another listing of the row's payload is this
        # row's alone, not what its name parses to.
        if title not in parsed_by_title and not meta.get(_ADOPTED_KEY):
            parsed_by_title[title] = meta
        return meta
    cached_meta = parsed_by_title.get(title)
    if cached_meta is not None:
        return deepcopy(cached_meta)
    # A name seen in an earlier search is not parsed again (``parse_store``).
    meta = parse_store.parsed(title, parse_title_metadata)
    parsed_by_title[title] = meta
    return meta


# Per-row caches derived from ``_meta``; dropped when the metadata is replaced.
_META_DERIVED_ROW_KEYS = (
    "_release_profile",
    "_fallback_identity",
    "_fallback_identity_title",
    "_fallback_title_tokens",
    "_fallback_title_tokens_title",
)


def _parse_detail(meta):
    """How much a parse recovered; the most detailed parse of a name wins.

    A known group first, then a known episode, then the tags a lost plus sign
    hides (``HDR10+`` reads as ``HDR10``, ``DD+`` as ``DD``), then how many
    fields are set at all.
    """
    identity = meta.get("ptt_identity")
    identity = identity if isinstance(identity, dict) else {}
    return (
        bool(meta.get("group")),
        bool(
            identity.get("seasons") or identity.get("episodes") or identity.get("date")
        ),
        "HDR10+" in (meta.get("hdr") or ()),
        "DD+" in (meta.get("audio") or ()),
        sum(
            1 for key in _FILTER_META_STR_KEYS + _FILTER_META_LIST_KEYS if meta.get(key)
        ),
    )


def _meta_for_spelling(best_meta, row):
    """``best_meta`` for another spelling of the same name.

    The copy keeps this row's own container (a ``.mkv`` and a ``.mp4`` of one
    name are different files) and the name its stored identity is keyed on.
    """
    from resources.lib.release_subject import (
        release_name_from_subject,
        without_media_extension,
    )

    meta = deepcopy(best_meta)
    own = row.get("_meta")
    if isinstance(own, dict):
        meta["container"] = own.get("container", "")
    identity = meta.get("ptt_identity")
    if isinstance(identity, dict):
        if not identity.get("title"):
            # With no parsed title the identity falls back to the whole name,
            # which differs between spellings; pin it to the winning one's.
            identity["title"] = without_media_extension(identity.get("name") or "")
        identity["name"] = release_name_from_subject(row.get("title"))
    return meta


def _parses_agree(meta, best_meta):
    """Whether two spellings already parse the same, container and name aside."""

    def comparable(value):
        value = dict(value)
        value.pop("container", None)
        identity = value.get("ptt_identity")
        if isinstance(identity, dict):
            value["ptt_identity"] = {k: v for k, v in identity.items() if k != "name"}
        return value

    return comparable(meta) == comparable(best_meta)


def _leading_spelling(spellings, known):
    """The rows of the spelling whose parse the other spellings take.

    The most detailed parse. Among equals the one whose group more names of
    the list carry (``known()`` maps a group to that number): "HONE" over
    "DiscoD HONE", "PsychoLogic" over "PsychoLogic arc", and "Goki-TAoE"
    over "Goki" where the list has "GokiTAoE". Then the spelling more
    indexers list, then the one that sorts first, which is the one written
    with capitals ("Movie.2020-GRP" before "movie.2020-grp"): the same
    spelling whatever order the rows are in.
    """
    from resources.lib.release_match import title_key

    details = {
        title: _parse_detail(same[0]["_meta"]) for title, same in spellings.items()
    }
    most = max(details.values())
    leading = [same for title, same in spellings.items() if details[title] == most]
    if len(leading) == 1:
        return leading[0]
    counts = known()
    best = None
    for same in leading:
        group = title_key(same[0]["_meta"].get("group"))
        rank = (counts.get(group, 0), len(same))
        if (
            best is None
            or rank > best[0]
            or (rank == best[0] and same[0]["title"] < best[1][0]["title"])
        ):
            best = (rank, same)
    return best[1]


def _pin_identity_title(rows):
    """Give the leading spelling's rows the title its other spellings take.

    With no parsed title, the identity falls back to the whole name, and
    ``_meta_for_spelling`` pins the other spellings to the leading one's.
    The leading rows carry the same title, so a later pass (a search read
    back from the cache) finds every spelling agreeing and changes none.
    """
    from resources.lib.release_subject import without_media_extension

    for row in rows:
        identity = row["_meta"].get("ptt_identity")
        if isinstance(identity, dict) and not identity.get("title"):
            identity["title"] = without_media_extension(identity.get("name") or "")
            for key in _META_DERIVED_ROW_KEYS:
                row.pop(key, None)


def _unify_name_forms(rows):
    """Give every spelling of one release name the same parsed metadata.

    The parser reads a name's punctuation: ``English-HONE`` has a group and
    ``English.HONE`` has none, ``DD+7.1`` is Dolby Digital Plus and ``DD_7.1``
    is not, ``[GbR] Show - 05 [...]`` has an episode and
    ``[GbR].Show-05.[...]`` has not. Those are one release listed twice, so
    they must filter, rank and group as one. Rows whose names are equal once
    case and punctuation are removed all take the most detailed parse among
    them (``_parse_detail``).
    """
    counted = []

    def known():
        # Read from the parses as they are before any spelling is changed.
        if not counted:
            counted.append(group_names(rows))
        return counted[0]

    forms = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("title")
        if not isinstance(title, str) or not isinstance(row.get("_meta"), dict):
            continue
        if row["_meta"].get(_ADOPTED_KEY):
            # Settled by its payload (``filter_payload``): the same name at
            # another size is another upload, and must not take this parse.
            continue
        key = _name_form_key(title)
        if key:
            forms.setdefault(key, {}).setdefault(title, []).append(row)
    for spellings in forms.values():
        if len(spellings) < 2:
            continue
        best = _leading_spelling(spellings, known)
        _pin_identity_title(best)
        best_meta = best[0]["_meta"]
        for same in spellings.values():
            if same is best or _parses_agree(same[0]["_meta"], best_meta):
                continue
            for row in same:
                row["_meta"] = _meta_for_spelling(best_meta, row)
                for key in _META_DERIVED_ROW_KEYS:
                    row.pop(key, None)


# Set on every row by one pass of ``_attach_metadata``: the pass's own token.
ATTACHED_KEY = "_attached"
# The passes this run made: token -> ``nfo_audio.known_version`` at the time.
# A token carries a random part, so one read back from another run (a row
# copied through the search cache) is never taken for one of ours.
_ATTACH_RUN = "{:012x}".format(int.from_bytes(os.urandom(6), "big"))
_ATTACH_NUMBERS = itertools.count(1)
_ATTACH_PASSES = collections.OrderedDict()
_ATTACH_PASSES_KEPT = 64


# A group must be on this many other rows of the list for a trailing word
# to be read as it.
_LIST_GROUP_ROWS = 2
# Set on a parse whose group ``_recover_list_groups`` read from the list.
_LIST_GROUP_KEY = "group_from_list"


def _recover_list_groups(rows):
    """Read a trailing word as the group when the list knows it as one.

    ``Show.S01.1080p.BluRay.x265.AAC.7.1.Japanese.Tigole`` ends in a word
    the name alone cannot tell from a language or a title word
    (``filter_tags.trailing_word``). When at least ``_LIST_GROUP_ROWS`` other
    rows of the same result list parse that word as their group, it is this
    row's group too. Returns how many rows gained one.
    """
    from resources.lib.release_match import title_key

    known = collections.Counter()
    bare = []
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        if not isinstance(meta, dict) or not isinstance(row.get("title"), str):
            continue
        group = title_key(meta.get("group"))
        if not group:
            bare.append((row, meta))
        elif not meta.get(_LIST_GROUP_KEY) and not meta.get(_ADOPTED_KEY):
            # Only groups names state count: one this step or
            # ``filter_payload`` added on an earlier pass (a row read back
            # from the search cache) would make this pass find more than
            # the first did.
            known[group] += 1
    if not known:
        return 0
    changed = 0
    for row, meta in bare:
        word = trailing_word(row["title"])
        if word and known[title_key(word)] >= _LIST_GROUP_ROWS:
            meta["group"] = word
            meta[_LIST_GROUP_KEY] = True
            for key in _META_DERIVED_ROW_KEYS:
                row.pop(key, None)
            changed += 1
    return changed


def _attached_already(results):
    """Whether one pass that still stands attached every row of ``results``.

    True when all the named rows carry the token of the same pass and their
    parse. Rows from two passes (two provider answers joined into one list)
    or a row added since need the pass again: spellings are unified and
    Atmos shared across the whole list.
    """
    token = None
    for result in results:
        if not isinstance(result, dict) or "title" not in result:
            continue
        mark = result.get(ATTACHED_KEY)
        if mark is None or not isinstance(result.get("_meta"), dict):
            return False
        if token is None:
            token = mark
        elif mark != token:
            return False
    if token is None:
        return False
    # ...and nothing was learned of any release's audio or video since.
    return _ATTACH_PASSES.get(token) == nfo_audio.known_version()


def _attach_metadata(results):
    """Parse every row's title into ``_meta`` and unify name spellings.

    Then the audio a release's NFO named is added (``nfo_audio``), and last,
    a WEB-DL whose name leaves Atmos out takes it from a labelled copy of the
    same source in ``results`` (``filter_atmos``).

    One picker open asks for this up to four times: when the provider
    answers, in the rules that keep the requested film or episode, and in
    ``filter_results``. The first pass marks its rows and the others return
    at once. A later pass over the same rows, or over some of them, changes
    nothing: a spelling already unified stays unified and a row that took
    Atmos keeps it. What can change the outcome is new knowledge of a
    release's audio or video (an NFO or MediaInfo read since), and a pass
    only stands while ``nfo_audio.known_version`` is what it was.
    """
    if _attached_already(results):
        return
    known = nfo_audio.known_version()
    parsed_by_title = {}
    for result in results:
        if isinstance(result, dict) and "title" in result:
            result["_meta"] = _resolve_result_meta(result, parsed_by_title)
    parse_store.save_soon()
    _unify_name_forms(results)
    # After the spellings: one that parses a group ("-DNU-TAoE") gives it to
    # the others, and only a name with no group in any spelling is asked
    # whether its last word is a group the list knows. What it finds goes to
    # the other spellings of the name too, as a group a pass read back from
    # the cache would carry already.
    if _recover_list_groups(results):
        _unify_name_forms(results)
    # A listing that lost its group takes the parse of its upload's name.
    other_names = unify_one_payload(
        results,
        _parse_detail,
        _meta_for_spelling,
        _parses_agree,
        _META_DERIVED_ROW_KEYS,
    )
    # What a release's NFO said of its audio, once it has been read: for its
    # own name, or for another name its upload is listed under.
    nfo_audio.apply_known(results, _name_form_key, _META_DERIVED_ROW_KEYS, other_names)
    share_atmos(results, _META_DERIVED_ROW_KEYS)
    _mark_pass(results, known)


def _mark_pass(results, known):
    """Mark every named row of ``results`` with a new pass's token.

    ``known`` is ``nfo_audio.known_version`` when the pass was made; the
    pass stands while that stays the same (``_attached_already``).
    """
    token = "{}-{}".format(_ATTACH_RUN, next(_ATTACH_NUMBERS))
    _ATTACH_PASSES[token] = known
    while len(_ATTACH_PASSES) > _ATTACH_PASSES_KEPT:
        _ATTACH_PASSES.popitem(last=False)
    for result in results:
        if isinstance(result, dict) and "title" in result:
            result[ATTACHED_KEY] = token


def _list_name_keys(results):
    """The release-name keys of the named rows of ``results``, sorted."""
    keys = set()
    for result in results:
        title = result.get("title") if isinstance(result, dict) else None
        if isinstance(title, str):
            keys.add(_name_form_key(title))
    keys.discard("")
    return sorted(keys)


def attached_record(results):
    """What a cache entry keeps so that reading it back skips the pass.

    None unless one pass that still stands attached every row of
    ``results`` (``_attached_already``). Otherwise the list's release-name
    keys and what was known of those releases (``nfo_audio.stamp_for``):
    the pass's outcome depends on nothing else but the rows and the code,
    and the cache already drops parses made by other code.
    """
    known = nfo_audio.known_version()
    if not _attached_already(results):
        return None
    keys = _list_name_keys(results)
    stamp = nfo_audio.stamp_for(keys)
    # An answer stored meanwhile may be in the stamp but not in the parses.
    return {"keys": keys, "nfo": stamp} if nfo_audio.known_version() == known else None


def resume_attached(results, record):
    """Mark rows read from the cache as attached when ``record`` still holds.

    ``record`` is what ``attached_record`` gave when the rows were stored,
    and the rows carry the parses stored with them. While nothing new is
    known of their releases the pass would come to the same parses, so the
    rows are marked as one pass of this run and every later
    ``attach_metadata`` over them, or over some of them, returns at once.
    Returns whether the rows were marked.
    """
    if not isinstance(record, dict):
        return False
    keys = record.get("keys")
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        return False
    named = [
        result for result in results if isinstance(result, dict) and "title" in result
    ]
    if not named or not all(isinstance(result.get("_meta"), dict) for result in named):
        return False
    known = nfo_audio.known_version()
    if record.get("nfo") != nfo_audio.stamp_for(keys):
        return False
    if nfo_audio.known_version() != known:
        # An answer was stored meanwhile: the pass may not stand for it.
        return False
    _mark_pass(named, known)
    return True


def learn_nfo_audio(rows, settings_getter):
    """Read the NFOs of the picker's top releases; True if the rows changed.

    ``rows`` are the filtered rows in the picker's order. True when an NFO
    named audio that was not known: the caller then runs ``filter_results``
    again, which adds it to the rows and sorts them afresh
    (``nfo_audio.learn_for_top``). What was learned changes
    ``nfo_audio.known_version``, so that second run does the whole pass.
    """
    return nfo_audio.learn_for_top(
        rows, _name_form_key, settings_getter, describe=release_identity
    )


def release_identity(name):
    """Which film or episode a release name is, and whose release.

    For telling whether an NFO is about a release (``nfo_audio.whose_nfo``).
    """
    from resources.lib.release_match import title_key

    meta = parse_title_metadata(name)
    identity = meta.get("ptt_identity")
    identity = identity if isinstance(identity, dict) else {}
    return {
        "title": title_key(identity.get("title")),
        "year": identity.get("year") or 0,
        "seasons": tuple(identity.get("seasons") or ()),
        "episodes": tuple(identity.get("episodes") or ()),
        "group": str(meta.get("group") or "").casefold(),
    }


def attach_metadata(results):
    """Parse the provider's rows before they are cached.

    The search cache stores each row's ``_meta`` so a repeat open of the
    picker skips the parsing (see ``cache``). Call this on the provider's
    whole answer: spellings of one name are unified across all of it.
    """
    _attach_metadata(results)


def _log_filter_summary(total, matched_count, shown):
    """Log the filter result counts, noting truncation when it occurred."""
    if shown < matched_count:
        message = (
            "NeNeTeePee-Stream-Kodi: Filtered {} -> {} results (showing {})"
        ).format(total, matched_count, shown)
    else:
        message = ("NeNeTeePee-Stream-Kodi: Filtered {} -> {} results").format(
            total, shown
        )
    xbmc.log(message, xbmc.LOGDEBUG)


def filter_results(results, settings_getter=None):
    """Apply filters, sort, truncate. Returns (filtered, all_parsed).

    Side effect: mutates each input dict by attaching ``_meta``
    (parsed-title metadata) and ``_filter_reject`` (the first rejecting
    filter's name, or ``None`` when the row passed). Callers that iterate
    ``results`` after this call see the extra fields. ``all_parsed`` is the
    same list of dicts (with ``_meta`` populated) in sorted order;
    ``filtered`` is the subset that passed every filter, truncated
    to ``settings["max_results"]`` if that is non-zero.
    """
    started = time.monotonic()
    settings = _get_filter_settings(settings_getter=settings_getter)

    _attach_metadata(results)

    all_parsed = []
    filtered = []
    for result in results:
        meta = result["_meta"]
        all_parsed.append(result)
        reject = first_rejecting_filter(result, meta, settings)
        result["_filter_reject"] = reject
        if reject is None:
            filtered.append(result)

    # The filtered view last: both sorts mark oddly sized copies on the same
    # row objects, and playback and backups read the rows of the filtered view.
    all_parsed = _sort_results(all_parsed, settings)
    if len(filtered) == len(all_parsed):
        # Nothing was rejected: the two views are the same rows in the same
        # starting order, so the second sort would repeat the first.
        filtered = list(all_parsed)
    else:
        filtered = _sort_results(filtered, settings)

    matched_count = len(filtered)
    max_results = settings["max_results"]
    if max_results > 0:
        filtered = filtered[:max_results]

    _log_filter_summary(len(all_parsed), matched_count, len(filtered))
    telemetry.log_timing(
        "filter_results",
        (time.monotonic() - started) * 1000.0,
        input=len(all_parsed),
        matched=matched_count,
        shown=len(filtered),
    )
    return filtered, all_parsed


def partition_series_rows(rows, requested_title):
    """Split rows into (matching, rest) by PTT-parsed show title.

    An episode search matches the query phrase ANYWHERE in the release
    name, so a series whose name collides with another show's EPISODE
    title pulls in that show's releases (live: searching the 2025 series
    "The Good the Bad and the Ugly" returned The.Rookie.S01E03.The.Good.
    the.Bad.and.the.Ugly...-NTb, and auto-select played The Rookie).
    A row matches when its normalized parsed show title equals, contains,
    or is contained in the normalized requested title. Rows whose title
    PTT cannot parse land in ``rest``.
    """
    if not requested_title:
        return list(rows), []
    # Lazy import, and through fallback_streams (not _identity directly):
    # the fallback modules import each other and this module's parse
    # helpers; fallback_streams is the only entry point that initializes
    # cleanly from a cold sys.modules, and it re-exports both helpers.
    from resources.lib.fallback_streams import (
        _normalize_title,
        _release_identity,
    )

    want = _normalize_title(requested_title)
    if not want:
        return list(rows), []
    matching = []
    rest = []
    for row in rows:
        got = _release_identity(row)[0]
        raw = row.get("title", "") if isinstance(row, dict) else ""
        if got == _normalize_title(raw) and got != want:
            # PTT could not isolate a show title and the identity fell
            # back to the WHOLE normalized release name — which contains
            # the searched phrase by construction (the indexer matched
            # it), so containment would pass every row. Only exact
            # equality counts for these.
            rest.append(row)
        elif (
            got
            and (got == want or got in want or _same_show_title(got, want))
            and not _other_regional_version(raw, want)
        ):
            matching.append(row)
        else:
            # Includes a title that is the requested one plus more words
            # ("The Office PL", "The Office Movers", "Narcos Mexico"): another
            # show that shares the name, which used to count as a match.
            rest.append(row)
    return matching, rest


# Words that never tell two titles apart, and the country a listing adds to
# the same show ("The.Office.US", "Shameless.UK").
_TITLE_FILLER = frozenset(("the", "a", "an", "and"))
_ARTICLES = frozenset(("the", "a", "an"))
_TITLE_QUALIFIERS = frozenset(("us", "uk", "au", "nz", "ca"))
_TITLE_YEAR = re.compile(r"(?:19|20)\d{2}")
# Past this share of a search's results, a rule that removes "other" titles is
# more likely misreading the requested title than finding strays.
_MAX_DROP_SHARE = 0.5


# Country codes a local remake carries right after the title
# ("The.Office.PL.S02E01"). The parser reads them as a language and takes them
# out of the title, so the title alone looks like the requested show. Codes
# that are also English words ("it", "in", "no", "be", "at") are left out.
_REMAKE_CODES = frozenset(
    "pl de fr es nl dk fi br pt ru jp kr tr cz hu gr il mx za ie ch ar".split()
)


def _other_regional_version(raw_title, want):
    """True when the name puts another country's code right after the title.

    ``want`` is the normalized requested title. "the office pl s02e01" is the
    Polish remake; "the office us s02e01" and "the office s02e01" are not
    judged here, and neither is a request that itself ends in the code.
    """
    from resources.lib.fallback_streams import _normalize_title
    from resources.lib.release_subject import release_name_from_subject

    wanted = want.split()
    if not wanted or wanted[-1] in _REMAKE_CODES:
        return False
    words = _normalize_title(release_name_from_subject(raw_title)).split()
    size = len(wanted)
    return words[:size] == wanted and len(words) > size and words[size] in _REMAKE_CODES


def _title_core(normalized):
    """The distinguishing words of a normalized title, as a frozenset."""
    return frozenset(
        word
        for word in normalized.split()
        if word not in _TITLE_FILLER
        and word not in _TITLE_QUALIFIERS
        and not _TITLE_YEAR.fullmatch(word)
    )


def _same_show_title(got, want):
    """Two normalized titles with the same distinguishing words.

    "the office us" and "the office 2005" are "the office"; "the office pl"
    and "narcos mexico" are other shows.
    """
    core = _title_core(want)
    if not core or _title_core(got) != core:
        return False
    # "A Teacher" and "The Teacher" are two shows; a listing that drops the
    # article ("Office" for "The Office") is still the show.
    articles = {words[0] for words in (got.split(), want.split()) if words}
    return len(articles & _ARTICLES) < 2


def _matched_outside_title(row, wanted):
    """True when the requested title is in the name but not in its title.

    ``wanted`` is ``(words, compact)``: the requested title's words, and the
    whole title with only letters and digits left. The compact form matches a
    title an apostrophe splits differently ("It's Always Sunny" lists as
    "Its.Always.Sunny").

    An indexer that matches text returns any post with the searched words in
    it: another show's episode titled like the requested show
    (``Sweet.Magnolias.S03E05.On.This.Foundation`` for *Foundation*,
    ``The.Sopranos.S02E01...Psychiatrists.Office`` for *The Office*) or a
    release group of that name (``Site.26.09.30...-Narcos`` for *Narcos*).
    The words leave out numbers, because the parser often takes one out of a
    title ("Station.19", "The.100"). A foreign-language title of the right show
    does not have the requested words anywhere, so it is not judged here.
    """
    from resources.lib.fallback_streams import _normalize_title, _release_identity
    from resources.lib.release_match import title_key
    from resources.lib.release_subject import (
        release_name_from_subject,
        without_media_extension,
    )

    words, compact = wanted
    title = _release_identity(row)[0]
    if words <= set(title.split()) or (compact and compact in title_key(title)):
        return False
    name = without_media_extension(release_name_from_subject(row.get("title")))
    return words <= set(_normalize_title(name).split())


def _without(results, is_other, what):
    """``results`` minus the rows ``is_other`` flags, unless that is most of them."""
    kept = [
        row
        for row in results
        if not (isinstance(row, dict) and "title" in row and is_other(row))
    ]
    dropped = len(results) - len(kept)
    if not dropped or dropped > len(results) * _MAX_DROP_SHARE:
        return results
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: dropped {} result(s) {}".format(dropped, what),
        xbmc.LOGINFO,
    )
    return kept


def drop_other_shows(results, requested_title):
    """Return episode ``results`` without rows matched outside their title.

    See ``_matched_outside_title``. Nothing is removed when the requested
    title has no usable words, or when the rule would remove more than half
    of the results (the parser is then more likely misreading the title than
    the indexers are returning strays). Side effect: attaches ``_meta``.
    """
    from resources.lib.fallback_streams import _normalize_title
    from resources.lib.filter_languages import is_language_word
    from resources.lib.release_match import title_key

    # A country code is left out like a number: the parser reads "UK" in
    # "The.Office.UK" as a language and takes it out of the title, so a
    # request for "The Office UK" would find "uk" only outside the title.
    words = {
        word
        for word in _normalize_title(str(requested_title or "")).split()
        if word not in _TITLE_FILLER
        and word not in _TITLE_QUALIFIERS
        and word not in _REMAKE_CODES
        and not word.isdigit()
        # "The English": the parser reads "English" as the language and takes
        # it out of the title, like a country code or a number.
        and not is_language_word(word)
    }
    if not words or not results:
        return results
    wanted = (words, title_key(requested_title))
    _attach_metadata(results)
    return _without(
        results,
        lambda row: _matched_outside_title(row, wanted),
        "that only mention '{}' outside their title".format(requested_title),
    )


def drop_tv_episodes(results):
    """Return movie ``results`` without rows that are an episode of a show.

    A movie search that falls back to text returns episodes named like the
    film (``The.Simpsons.S34E12...`` for a documentary). A row that parses
    both a season and an episode is not the movie, unless it is a special.
    """
    if not results:
        return results
    _attach_metadata(results)

    def _is_episode(row):
        identity = row["_meta"].get("ptt_identity")
        if not isinstance(identity, dict):
            return False
        seasons = _int_set(identity.get("seasons"))
        episodes = _int_set(identity.get("episodes"))
        # A special (season 0 or episode 0) can be what a movie entry points
        # at: "IT.Welcome.to.Derry.S01E00.A.Story.of.Derry" is listed as a film.
        return bool(seasons and episodes) and 0 not in seasons | episodes

    return _without(results, _is_episode, "that are a show's episode, not the movie")


def prefer_series_rows(rows, requested_title):
    """Order rows so parsed-show-title matches precede everything else.

    Auto-select takes ``rows[0]``, so this is the wrong-show guard for
    episode playback; manual pickers still see every row. When NOTHING
    matches (foreign titling, PTT quirks) the original order is kept
    rather than guessing.
    """
    matching, rest = partition_series_rows(rows, requested_title)
    if not matching or not rest:
        return list(rows)
    xbmc.log(
        "NZB-DAV: demoted {} result(s) whose parsed show title does not "
        "match '{}'".format(len(rest), requested_title),
        xbmc.LOGINFO,
    )
    return matching + rest


def _requested_episode_numbers(requested_episode):
    """Return ``(season, episode)`` as ints, or ``None`` when either is unknown."""
    if not isinstance(requested_episode, (list, tuple)) or len(requested_episode) != 2:
        return None
    season, episode = requested_episode
    try:
        return int(str(season).strip()), int(str(episode).strip())
    except (TypeError, ValueError):
        return None


def _int_set(values):
    """The ints in ``values``; anything that is not a whole number is skipped."""
    numbers = set()
    for value in values if isinstance(values, (list, tuple)) else ():
        try:
            numbers.add(int(value))
        except (TypeError, ValueError):
            continue
    return numbers


def _names_other_episode(meta, season, episode):
    """True unless the parsed release name can hold ``season``/``episode``.

    A row stays only when its name says it can be the requested episode:

    * it names seasons, the requested one is among them, and any episodes it
      names include the requested one (the episode itself, a multi-episode
      file that includes it, or a pack for the season);
    * it names an episode but no season, the name also parses as video (a
      resolution or codec, which a numbered music track lacks), and the
      number fits: the requested episode's own number, or from season 2 on an
      absolute count (anime) high enough to reach the requested season;
    * it names neither, but carries an air date (a daily show), is marked as
      a complete-series pack, or still parses as video. The last case covers
      names whose episode number PTT misses (anime such as
      ``Sousou no Frieren-05 [BD 1920x1080 x265]``), which may be the episode.

    Everything else is another episode, another season, or not an episode at
    all (a soundtrack, a disc image, an obfuscated post).
    """
    identity = meta.get("ptt_identity") if isinstance(meta, dict) else None
    if not isinstance(identity, dict):
        return False
    seasons = _int_set(identity.get("seasons"))
    episodes = _int_set(identity.get("episodes"))
    if seasons:
        if season not in seasons:
            return True
        return bool(episodes) and episode not in episodes
    looks_like_video = bool(meta.get("resolution") or meta.get("codec"))
    if episodes:
        # The season's own numbering ("Show (2023) - 05" for S02E05), or an
        # absolute count: every earlier season has at least one episode, so
        # that count is at least ``episode + season - 1``.
        fits = episode in episodes or (
            season > 1 and max(episodes) >= episode + season - 1
        )
        return not (fits and looks_like_video)
    return not (identity.get("date") or identity.get("complete") or looks_like_video)


def _air_date(value):
    """``value`` as a ``datetime.date`` when it starts ``YYYY-MM-DD``, else ``None``."""
    parts = str(value or "").strip()[:10].split("-")
    if len(parts) != 3 or len(parts[0]) != 4:
        return None
    try:
        return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _dated_row_days(rows):
    """Map ``id(row)`` to the air date of each row named by date alone."""
    days = {}
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        identity = meta.get("ptt_identity") if isinstance(meta, dict) else None
        if not isinstance(identity, dict):
            continue
        if identity.get("seasons") or identity.get("episodes"):
            continue
        day = _air_date(identity.get("date"))
        if day is not None:
            days[id(row)] = day
    return days


def _keep_requested_air_date(rows, air_date):
    """Among rows named by air date alone, keep the requested night.

    A daily show's releases carry a date and no season or episode, so the
    episode rule cannot tell one night from another. When the caller knows
    the episode's air date, rows dated that day are kept and the other nights
    are dropped. When no row has that exact date, the listing and the release
    names may disagree by a day (a late-night show listed under the next
    calendar day), so rows one day either side are kept instead. When even
    those are missing, every dated row is kept: hiding them all would leave
    nothing to pick from on a metadata mismatch.
    """
    wanted = _air_date(air_date)
    if wanted is None:
        return rows
    days = _dated_row_days(rows)
    if not days:
        return rows
    for tolerance in (0, 1):
        near = {
            key for key, day in days.items() if abs((day - wanted).days) <= tolerance
        }
        if near:
            return [row for row in rows if id(row) not in days or id(row) in near]
    return rows


def drop_other_episodes(results, requested_episode, air_date=None):
    """Return ``results`` without rows that name a different episode.

    An episode search trusts the indexer to honour the season and episode it
    was asked for, but a raw-subject indexer matches the show name alone and
    returns every episode it has (live: a Six Feet Under S02E01 search listed
    88 other episodes from S01 through S05, plus music tracks and disc images
    that only share the name). Those rows are not the requested content, so
    they are removed outright rather than marked as filtered: the picker's
    show-all view must not offer them either.

    ``requested_episode`` is ``(season, episode)``; anything that does not
    hold two whole numbers (a movie search passes ``None``) leaves ``results``
    untouched. ``air_date`` (``YYYY-MM-DD``, optional) narrows rows named by
    date alone to the requested night (see ``_keep_requested_air_date``).
    Side effect: attaches ``_meta`` to each row, which
    ``filter_results`` then reuses instead of parsing the title again.
    """
    wanted = _requested_episode_numbers(requested_episode)
    if wanted is None or not results:
        return results
    season, episode = wanted
    _attach_metadata(results)
    kept = []
    for result in results:
        if not isinstance(result, dict) or "title" not in result:
            kept.append(result)
            continue
        if not _names_other_episode(result["_meta"], season, episode):
            kept.append(result)
    kept = _keep_requested_air_date(kept, air_date)
    dropped = len(results) - len(kept)
    if dropped:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: dropped {} result(s) that name a different "
            "episode than S{:02d}E{:02d}".format(dropped, season, episode),
            xbmc.LOGINFO,
        )
    return kept


_YEAR_TOKEN = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])")


def _names_other_year(row, wanted, title_years=frozenset()):
    """True when a movie row is clearly dated for another year than ``wanted``.

    ``title_years`` are the year-like numbers in the requested title itself:
    with no release year in the name, the parser reads the "2049" of
    ``Blade.Runner.2049.2160p`` as the year, and that is the title, not a date.

    The parsed year must be more than one year off (festival and release years
    differ by one), and the requested year must not appear anywhere in the
    name. The second check keeps a title that is itself a year
    (``2001.A.Space.Odyssey.1968``, ``Blade.Runner.2049.2017``) whichever
    number the parser took for the year.
    """
    from resources.lib.release_subject import release_name_from_subject

    meta = row.get("_meta")
    identity = meta.get("ptt_identity") if isinstance(meta, dict) else None
    if not isinstance(identity, dict):
        return False
    if identity.get("seasons") or identity.get("episodes"):
        return False
    year = identity.get("year")
    if not isinstance(year, int) or isinstance(year, bool) or year <= 0:
        return False
    if abs(year - wanted) <= 1 or year in title_years:
        return False
    named = {
        int(token)
        for token in _YEAR_TOKEN.findall(release_name_from_subject(row.get("title")))
    }
    return not named & {wanted - 1, wanted, wanted + 1}


def drop_other_years(results, requested_year, requested_title=""):
    """Return movie ``results`` without rows dated for another year.

    A movie search by id still comes back with other films: an indexer that
    cannot search by id falls back to the title, so *Avatar* (2009) listed
    *Avatar: Fire and Ash* (2025) first. A row whose name is clearly dated for
    another year is removed outright, like another episode on an episode
    search. A row with no year, or within a year of the requested one, stays.
    ``requested_year`` that is not a four-digit year leaves ``results``
    untouched. A year-like number in ``requested_title`` is never a
    conflicting date. Side effect: attaches ``_meta`` to each row.
    """
    try:
        wanted = int(str(requested_year).strip())
    except (TypeError, ValueError):
        return results
    if not 1900 <= wanted <= 2100 or not results:
        return results
    _attach_metadata(results)
    title_years = frozenset(
        int(token) for token in _YEAR_TOKEN.findall(str(requested_title or ""))
    )
    kept = [
        row
        for row in results
        if not (
            isinstance(row, dict)
            and "title" in row
            and _names_other_year(row, wanted, title_years)
        )
    ]
    dropped = len(results) - len(kept)
    if not dropped:
        return results
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: dropped {} result(s) dated for another "
        "year than {}".format(dropped, wanted),
        xbmc.LOGINFO,
    )
    return kept


def _pubdate_sort_key(result):
    """Return a sortable datetime-derived key for RFC-822 pubdate.

    Sorting results by raw ``r.get("pubdate", "")`` gives LEXICOGRAPHIC
    order over strings like ``"Mon, 02 Jan 2006 15:04:05 GMT"``—which
    puts "Fri" < "Mon" < "Sun" < "Tue" chronologically wrong. Parse to
    a timestamp instead. Unparseable values sort at the epoch so
    malformed entries don't crash and don't jump to the top under
    descending sort.
    """
    from email.utils import parsedate_to_datetime

    raw = result.get("pubdate", "") or ""
    if not raw:
        return 0.0
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _size_sort_key(result):
    """Return an int-valued size key, tolerating malformed size fields.

    Indexers occasionally return non-numeric ``size`` values (for example, when
    the NZB's file list omitted byte totals). Previously a bare ``int()`` call
    would crash the entire sort on a single bad entry. Return 0 for
    anything non-parseable so the rest of the list still sorts cleanly.
    """
    raw = result.get("size", 0)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


# Resolution rank: highest first, from 8K down to SD, unknown worst.
_RES_RANK = {value: rank for rank, (_, value) in enumerate(_RESOLUTION_SETTINGS)}

# HDR rank: DV best (0), HDR10+ (1), HDR10 (2), HLG (3), SDR (4), none (5).
_HDR_RANK = {
    "Dolby Vision": 0,
    "HDR10+": 1,
    "HDR10": 2,
    "HLG": 3,
}


def _hdr_rank(hdr_list):
    """Best (lowest) HDR tier rank present, or 5 when none."""
    if not hdr_list:
        return 5  # no HDR = worst
    return min(_HDR_RANK.get(h, 4) for h in hdr_list)


def _make_relevance_key(remux_tiers=DEFAULT_REMUX_GROUPS):
    """Rank resolution, then the pinned groups (CiNEPHiLES, FraMeSToR), then
    genuine releases before upscales, named groups before none, then HDR,
    REMUX, audio tier (``filter_audio.audio_tier``), group tier, and size.

    A release tagged as an upscale (``filter_upscale``) is not really of the
    resolution its name states, so it ranks below every other release of
    that resolution, whatever HDR, REMUX or audio tags its name carries.

    Audio comes before the preferred-group tier: Dolby Digital Plus with
    Atmos from a group in no tier ranks above plain Dolby Digital Plus from a
    preferred one. The group tier then orders releases whose audio is in the
    same tier."""
    tier_patterns = compile_remux_tiers(remux_tiers)

    def _relevance_key(r):
        meta = r.get("_meta", {})
        res_rank = _RES_RANK.get(meta.get("resolution", ""), len(_RES_RANK))
        size = -_size_sort_key(r)  # larger = better, negate for ascending sort
        priority = remux_priority(r)
        return (
            res_rank,
            pinned_group_rank(r),  # CiNEPHiLES, then FraMeSToR: first in theirs
            1 if is_ai_upscale(r) else 0,  # an upscale: last in its resolution
            0 if meta.get("group") else 1,  # no release group: last in its resolution
            _hdr_rank(meta.get("hdr", [])),
            priority,
            audio_tier(meta.get("audio", []), meta.get("quality", "")),
            remux_group_rank(r, tier_patterns),
            size,
        )

    return _relevance_key


def _neg_size_sort_key(result):
    """Negated size key: largest first, preserving stable tie order."""
    return -_size_sort_key(result)


# Non-relevance sort orders: (key function, reverse). Order 1 negates the
# key rather than using reverse=True so equal-size ties keep their original
# relative order (reverse=True would flip ties under Python's stable sort).
_SORT_SPECS = {
    1: (_neg_size_sort_key, False),
    2: (_size_sort_key, False),
    3: (_pubdate_sort_key, True),
    4: (_pubdate_sort_key, False),
}


def _sort_results(results, settings):
    """Sort results by configured sort order, with preferred groups boosted.

    Sort orders:
        0 = Relevance (among rows tied before size, more NZB copies of the
            release first, then larger size, then original order)
        1 = Size (largest first)
        2 = Size (smallest first)
        3 = Age (newest first) -- pubdate descending
        4 = Age (oldest first) -- pubdate ascending

    Relevance groups the rows into releases once (``release_index``); the
    copy counts and the odd-size check both read that grouping.
    """
    spec = _SORT_SPECS.get(settings["sort_order"])
    if spec is not None:
        key, reverse = spec
        return sorted(results, key=key, reverse=reverse)
    relevance_key = _make_relevance_key(
        settings.get("remux_tiers", DEFAULT_REMUX_GROUPS)
    )
    index = release_index.build(results)
    key = _with_copies(relevance_key, _nzb_copy_counts(results, index))
    return _demote_size_outliers(sorted(results, key=key), index)


# Where the single-copy element goes in the relevance key: after resolution,
# the pinned groups, "is an upscale" and "has a release group", before HDR
# and the rest.
_SINGLE_COPY_KEY_INDEX = 4


def _with_copies(relevance_key, counts):
    """``relevance_key`` with the release's copy count added in two places.

    * A release listed once ranks late. A real release is listed by several
      indexers; what shows up as one NZB from one indexer is usually a
      mislabelled or stray post, and it reached the top of a resolution
      whenever its name carried a better HDR, REMUX or audio tag than the
      well-listed releases. A release with a single NZB (``counts``, from
      ``_nzb_copy_counts``) ranks below every release of its resolution that
      has two or more, and above the releases with no group and the
      upscales. CiNEPHiLES and FraMeSToR stay pinned first whatever their
      count. When every release of a resolution has one copy, nothing
      changes.
    * Among rows that tie on everything before size (resolution, pinned
      group, HDR, REMUX, audio tier, group tier), more copies rank first,
      then the larger size as before. A stable sort keeps the original
      order when both tie.
    """

    def _key(row):
        base = relevance_key(row)
        copies = counts.get(id(row), 1)
        return (
            base[:_SINGLE_COPY_KEY_INDEX]
            + (0 if copies > 1 else 1,)
            + base[_SINGLE_COPY_KEY_INDEX:-1]
            + (-copies, base[-1])
        )

    return _key


# A copy this many times larger or smaller than its release's typical copy is
# not a normal listing of it. NZB sizes of one release differ by the repair
# data each poster added, which stays well inside this.
_SIZE_OUTLIER_FACTOR = 1.35
# With fewer copies there is no telling which size is the typical one.
_SIZE_OUTLIER_MIN_COPIES = 3


def _row_size(row):
    """The row's size in bytes, or 0 when it is missing or unreadable."""
    try:
        return max(int(row.get("size") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _sized_copies(ordered, index):
    """For each release with enough sized copies, their positions in ``ordered``.

    The releases are ``index``'s groups, so a copy is measured against the
    rows the picker lists with it: a ``.mkv`` and a ``.mp4`` both named, or a
    1990 film and its 2020 remake, are separate releases there and an
    ordinary copy of one is never an odd copy of the other. Season packs and
    rows without a size are left out.
    """
    position = {id(row): at for at, row in enumerate(ordered)}
    for group in index.groups:
        if len(group) < _SIZE_OUTLIER_MIN_COPIES or group[0].get("_season_pack"):
            continue
        indexes = sorted(
            position[id(row)] for row in group if id(row) in position and _row_size(row)
        )
        if len(indexes) >= _SIZE_OUTLIER_MIN_COPIES:
            yield indexes


def _demote_size_outliers(ordered, index=None):
    """Move oddly sized copies of a release below its normally sized ones.

    One release name can cover very different posts: a 10 GB episode listed
    42 times and once at 91 GB (a mislabelled pack), or once at 60 MB (a
    broken post). Relevance puts the largest copy first, so the odd one led
    the release and was what the picker played. A copy more than
    ``_SIZE_OUTLIER_FACTOR`` away from the median size of its release is
    marked ``_size_outlier`` and moved to just after the release's last
    normally sized copy; everything else keeps its order.

    ``index`` is the ``release_index`` of these rows when the caller has
    one; it is built from ``ordered`` otherwise.
    """
    for row in ordered:
        if isinstance(row, dict):
            row.pop("_size_outlier", None)
    if index is None:
        index = release_index.build(ordered)
    follow = {}
    moved = set()
    for indexes in _sized_copies(ordered, index):
        median = statistics.median(_row_size(ordered[i]) for i in indexes)
        low, high = median / _SIZE_OUTLIER_FACTOR, median * _SIZE_OUTLIER_FACTOR
        normal = [i for i in indexes if low <= _row_size(ordered[i]) <= high]
        if not normal or len(normal) == len(indexes):
            continue
        anchor = normal[-1]
        for index_at in indexes:
            if low <= _row_size(ordered[index_at]) <= high:
                continue
            ordered[index_at]["_size_outlier"] = True
            if index_at < anchor:
                follow.setdefault(anchor, []).append(index_at)
                moved.add(index_at)
    if not moved:
        return ordered
    result = []
    for index_at, row in enumerate(ordered):
        if index_at in moved:
            continue
        result.append(row)
        result.extend(ordered[i] for i in follow.get(index_at, ()))
    return result


def _nzb_copy_counts(rows, index=None):
    """Map ``id(row)`` to how many NZBs of its release ``rows`` hold.

    A release is a group of ``release_index`` (``index`` when the caller has
    built it for these rows), so every spelling of a release carries the
    same count, and it is the count the picker shows for that row. A
    groupless row counts only itself.

    A count is the separate uploads among the distinct NZB links of the
    release (``upload_count.count_copies``): several indexers listing one
    upload count once, a link listed twice counts once and a row without a
    link adds nothing (every row counts at least 1).
    """
    if index is None:
        index = release_index.build(rows)
    counts = {}
    for group in index.groups:
        copies = index.copies_in(group)
        for row in group:
            counts[id(row)] = copies
    return counts
