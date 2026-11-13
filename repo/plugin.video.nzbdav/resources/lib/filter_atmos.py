# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Atmos a release name leaves out, taken from a labelled copy of its source.

A streaming service delivers one audio track for a title at a given
resolution and video codec, and a WEB-DL keeps it untouched. Groups name it
differently: one writes ``DD+5.1.Atmos``, another ``DDP5.1`` for the same
Amazon stream. Ranked by its name alone, the second sat a whole audio tier
below the first.

So a WEB-DL tagged Dolby Digital Plus without Atmos is treated as Atmos when
another row of the same search is the same content from the same service, at
the same resolution, video codec and channel count, and is tagged Dolby
Digital Plus with Atmos. The row then shows and ranks as Atmos.

It is deliberately narrow:

* WEB-DL only (and the scene's bare ``WEB``). A WEBRip is re-encoded, and a
  disc's encodes carry whatever audio each encoder chose.
* Both rows must name the same service; a row that names none takes nothing.
* Both rows must tag the same audio languages, or none, and the same
  dubbed, MULTi and cut flags: a dub is another track.
* A HYBRID release neither gives nor takes: it combines sources, so its
  audio may come from somewhere else.
* Only a tag written in a name gives. A row that took Atmos here never
  passes it on.
"""

_WEB_DL = frozenset(("web-dl", "web"))
_BED = "DD+"
_OBJECTS = "Atmos"
# Set on a row's metadata when its Atmos came from another row.
INFERRED_KEY = "atmos_inferred"


def _audio(meta):
    audio = meta.get("audio")
    return audio if isinstance(audio, list) else []


def _languages(meta):
    """The audio languages a name tags, as a comparable key.

    A dub is another audio track: a German copy of a stream says nothing
    about whether its track has Atmos. Names that tag no language (most
    English releases) only match each other.
    """
    languages = meta.get("languages")
    if not isinstance(languages, (list, tuple)):
        return ()
    return tuple(sorted({str(value).lower() for value in languages if value}))


def _variant(meta):
    """The dubbed, MULTi, cut, 3D and re-release flags of a name.

    A MULTi or dubbed release carries other tracks, and a different cut may
    carry another mix. A group's iNTERNAL mark says nothing about the audio.
    """
    variant = meta.get("variant")
    if not isinstance(variant, (list, tuple)):
        return ()
    return tuple(sorted(str(flag) for flag in variant if flag != "internal"))


def _source_key(row, meta):
    """What must be equal for two rows to carry the same stream, or None."""
    from resources.lib import fallback_streams as _fs
    from resources.lib.release_match import air_date, title_key

    if meta.get("hybrid") or str(meta.get("quality") or "").lower() not in _WEB_DL:
        return None
    network = str(meta.get("network") or "")
    channels = str(meta.get("channels") or "")
    resolution = str(meta.get("resolution") or "")
    codec = str(meta.get("codec") or "")
    if not (network and channels and resolution and codec):
        return None
    try:
        title, year, seasons, episodes, part = _fs._release_identity(row)
        date = air_date(row, meta)
    except Exception:  # pylint: disable=broad-except
        return None
    if not title:
        return None
    return (
        title_key(title),
        year,
        tuple(seasons or ()),
        tuple(episodes or ()),
        part,
        date,
        str(meta.get("edition") or "").lower(),
        _languages(meta),
        _variant(meta),
        network,
        resolution,
        codec,
        channels,
    )


def share_atmos(rows, derived_keys=()):
    """Mark unlabelled Dolby Digital Plus WEB-DLs as Atmos; see the module.

    ``rows`` carry ``_meta``. A row that takes Atmos gets it in its
    ``audio`` list, ``INFERRED_KEY`` set, and its ``derived_keys`` (per-row
    caches built from the metadata) dropped. Returns how many rows changed.
    """
    givers = set()
    takers = []
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        if not isinstance(meta, dict):
            continue
        audio = _audio(meta)
        if _BED not in audio:
            continue
        labelled = _OBJECTS in audio
        if labelled and meta.get(INFERRED_KEY) is True:
            continue
        key = _source_key(row, meta)
        if key is None:
            continue
        if labelled:
            givers.add(key)
        else:
            takers.append((key, row, meta))
    changed = 0
    for key, row, meta in takers:
        if key not in givers:
            continue
        meta["audio"] = [_OBJECTS] + _audio(meta)
        meta[INFERRED_KEY] = True
        for name in derived_keys:
            row.pop(name, None)
        changed += 1
    return changed
