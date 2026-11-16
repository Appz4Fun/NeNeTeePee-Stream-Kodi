# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A release's audio and video, read from its MediaInfo or NFO for the top
releases of a search.

A release name can leave its audio out ("Movie.2004.MULTi.1080p.BluRay.
x264-GRP"), and then the release ranks as "audio unknown". Its NFO usually
says: ``Audio.........: English DTS-HD MA 5.1 @ 3739 kb/s``.

Most indexers send, with a search result, a flag that the release has an NFO
and a link to it (``newznab_attrs.nfo_link``); NZBHydra2 passes both through.
In direct-indexer mode the add-on holds the indexer's key and builds the
calls itself (``newznab_attrs.direct_media_links``). What each indexer
offers is in ``indexer_media``. For the first few releases of a search that
offer something, the add-on asks, reads the answer and remembers it:

* only the top releases (``nfo_audio_releases``, 3 by default, 0 for off):
  each fetch is an API call on the user's account with that indexer;
* an indexer that refuses a read (a client it blocks, a limit reached) is
  left alone for a day, not asked again for every new release;
* each release is asked for once. The answer, or that there was none, is
  kept in ``nfo_audio.json`` in the add-on's data folder and applies to every
  listing of that release name from then on, whichever indexer lists it;
* the picker waits a short while for the answers (``_WAIT_SECONDS``) and
  opens without them when an indexer is slow. They are stored when they
  arrive and used the next time.

Where the indexer also serves MediaInfo it is asked for first and the NFO is
the fallback (``mediainfo_audio``). A release listed by several indexers is
asked about on the one that serves MediaInfo, and its NFO is read from an
indexer that has one (``_listing``).

What was read is preferred over what the name says: it replaces the name's
formats and channel count (``apply_known``). The one thing kept from the
name is an Atmos or DTS:X tag on the track a hand-written NFO leads with,
since such NFOs often leave the word out; MediaInfo is used as it is.

MediaInfo also describes the video (``mediainfo_video``): the HDR format,
the Dolby Vision profile and the bit depth. They are taken from an indexer's
MediaInfo call, or from an NFO that is MediaInfo text and names this
release, and put on the rows the same way.

That holds for an NFO that is this release's. Indexers attach NFOs by
matching, so each one is checked against the release's name (``whose_nfo``):
one that names another release is not used, and one that names no release
only adds to what the name says.

The links carry the user's API key for that indexer, so they are never
logged.
"""

import json
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

import xbmc
import xbmcvfs

from resources.lib import mediainfo_audio, mediainfo_video, search_progress
from resources.lib.filter_audio import main_audio
from resources.lib.filter_tags import detect_audio, detect_channels
from resources.lib.http_util import http_get, redact_text
from resources.lib.indexer_media import has_key, serves_mediainfo, with_key_from
from resources.lib.mediainfo_audio import (
    CHANNEL_COUNT,
    OBJECT_FORMATS,
    merge_tracks,
    readable_formats,
)

SETTING = "nfo_audio_releases"
DEFAULT_RELEASES = 3
MAX_RELEASES = 5
# On a row's metadata, once its release's audio has been read:
# the formats the release has that its name does not tag,
FROM_NFO_KEY = "audio_from_nfo"
# what the name itself tags ({"audio": [...], "channels": "5.1"}),
NAME_AUDIO_KEY = "audio_in_name"
# and where the answer came from: "mediainfo", "nfo", or ``UNCONFIRMED`` for
# an NFO that does not name the release.
SOURCE_KEY = "audio_source"
# The dynamic range the name tags, once MediaInfo's has taken its place.
NAME_HDR_KEY = "hdr_in_name"
# ``filter_atmos.INFERRED_KEY``: Atmos a row took from a labelled copy.
_INFERRED_KEY = "atmos_inferred"

_PROFILE_PATH = "special://profile/addon_data/plugin.video.nzbdav/"
_STORE_NAME = "nfo_audio.json"
_STORE_VERSION = 1
_MAX_STORED = 3000
# A fetch that failed is not repeated for this long.
_RETRY_AFTER_SECONDS = 24 * 3600
_FETCH_TIMEOUT = 6
_MAX_NFO_BYTES = 262144
_MAX_MEDIAINFO_BYTES = 2097152
# An indexer found to have no MediaInfo call is not asked again for this long.
_NO_API_SECONDS = 30 * 24 * 3600
# How long the picker waits for the answers before opening without them.
_WAIT_SECONDS = 2.0

# A line labelled as the audio ("Audio.........:", "AUDiO #1 :", "Ton:"), in
# the languages NFOs are written in. The word must be the label, ended by a
# colon, an equals sign or a row of dots ("AUDiO 1........ German EAC3 5.1"):
# a sentence that merely mentions audio is not read. Box-drawing art before
# the label is skipped.
_AUDIO_LABEL = re.compile(
    r"^\W*(?:audio|sound|tonspur|ton|son|lyd|ljud|geluid)\b[\w #/-]{0,20}?"
    r"\s*(?:[:=]|\.{3,})",
    re.I,
)
# MediaInfo's layout: a line that is just "Audio" or "Audio #2" heads a block
# of labelled lines that ends at the next blank line.
_AUDIO_SECTION = re.compile(r"^\W*audio(?:\s*#?\s*\d+)?\W*$", re.I)
_SECTION_FIELD = re.compile(
    r"^\W*(?:format|commercial name|codec(?: id)?|channel)", re.I
)
# A line with a label of its own: an audio line's continuation ends here.
_LABELLED = re.compile(r"^\W*[A-Za-z][\w #/()-]{0,24}?[ .]*[:=]")
_CONTINUATION_LINES = 3
_PADDING = re.compile(r"[._]{2,}")
# A channel count that stands by itself on an audio line ("AUDiO CHANNEL: 2.0").
_BARE_COUNT = re.compile(r"(?<![\d.])([1-9]\.[01])(?![\d.])")
_CHANNEL_WORDS = re.compile(r"\b([1-9])\s*(?:ch\b|channels?\b)", re.I)

_LOCK = threading.Lock()
# Held for a whole save, so saves land in the order they were taken.
_SAVE_LOCK = threading.Lock()
_STORE = {}
# host -> when it last refused a read: it is left alone for a while.
_PAUSED_HOSTS = {}
# host -> whether it answers the MediaInfo call, and when that was learned.
_REST_HOSTS = {}
_LOADED = []


def _audio_tracks(text):
    """``(track, line)`` for the lines of an NFO that describe its audio.

    A line labelled as the audio is a track, and so is each unlabelled line
    right under it (a second track). The format lines of a MediaInfo "Audio"
    block are one track together. ``track`` numbers them in order.
    """
    continuation = 0
    in_section = False
    track = 0
    # splitlines: a CRLF is one line ending, not a line and a blank line.
    for line in str(text or "").splitlines():
        if not line.strip():
            continuation = 0
            in_section = False
        elif _AUDIO_SECTION.match(line):
            continuation = 0
            in_section = True
            track += 1
        elif _AUDIO_LABEL.match(line):
            continuation = _CONTINUATION_LINES
            in_section = False
            track += 1
            yield track, line
        elif in_section:
            if _SECTION_FIELD.match(line):
                yield track, line
        elif continuation and not _LABELLED.match(line):
            continuation -= 1
            track += 1
            yield track, line
        else:
            continuation = 0


def _line_channels(readable):
    """The channel count on an audio line: by a format, in words, or bare."""
    counts = detect_channels(readable)
    if counts:
        return counts[0]
    words = _CHANNEL_WORDS.search(readable)
    if words:
        return CHANNEL_COUNT.get(words.group(1), "")
    bare = _BARE_COUNT.search(readable)
    return bare.group(1) if bare else ""


def _join_bare_counts(entries):
    """Give a count that stands alone to the track named before it.

    "AUDiO CHANNEL....: 2.0" on its own line belongs to the track on the
    line above, when that one named a format and no count.
    """
    named = [entry for entry in entries if entry["audio"]]
    for index, entry in enumerate(entries):
        if entry["audio"] or not entry["channels"]:
            continue
        before = [e for e in entries[:index] if e["audio"]]
        if before and before[-1] in named and not before[-1]["channels"]:
            before[-1]["channels"] = entry["channels"]


def audio_from_nfo(text):
    """``{"audio": [...], "channels": "5.1"}`` read from an NFO's text.

    Only the lines that describe the audio tracks are read
    (``_audio_tracks``); the tracks are put together by
    ``mediainfo_audio.merge_tracks``. The audio list is empty when the NFO
    names no format the add-on knows.
    """
    tracks = {}
    for track, line in _audio_tracks(text):
        # A label's row of dots is padding. Left in, the tag reader takes the
        # dot for the name's word separator and reads "DD 5.1" as a "DD+5.1"
        # whose plus was lost, which only happens in release names.
        readable = readable_formats(_PADDING.sub(" ", line))
        entry = tracks.setdefault(track, {"audio": [], "channels": ""})
        for name in detect_audio(readable):
            if name not in entry["audio"]:
                entry["audio"].append(name)
        if not entry["channels"]:
            entry["channels"] = _line_channels(readable)
    entries = list(tracks.values())
    _join_bare_counts(entries)
    return merge_tracks(entries)


# --- what is known, on disk -------------------------------------------------


def _store_path():
    return os.path.join(xbmcvfs.translatePath(_PROFILE_PATH), _STORE_NAME)


def _load():
    """Read the store once per run; an unreadable file is an empty store."""
    with _LOCK:
        if _LOADED:
            return
        _LOADED.append(True)
        try:
            with open(_store_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            releases = data.get("releases") if isinstance(data, dict) else None
            if isinstance(releases, dict) and data.get("v") == _STORE_VERSION:
                for key, entry in releases.items():
                    if isinstance(key, str) and _valid_entry(entry):
                        _STORE[key] = entry
                hosts = data.get("hosts")
                for host, when in (hosts if isinstance(hosts, dict) else {}).items():
                    if isinstance(host, str) and isinstance(when, (int, float)):
                        _PAUSED_HOSTS[host] = when
                rest = data.get("rest")
                for host, state in (rest if isinstance(rest, dict) else {}).items():
                    if (
                        isinstance(host, str)
                        and isinstance(state, dict)
                        and isinstance(state.get("ok"), bool)
                        and isinstance(state.get("at"), (int, float))
                    ):
                        _REST_HOSTS[host] = {"ok": state["ok"], "at": state["at"]}
        except (OSError, TypeError, ValueError):
            return


def _valid_entry(entry):
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("audio"), list)
        and all(isinstance(name, str) for name in entry["audio"])
        and isinstance(entry.get("channels", ""), str)
        and isinstance(entry.get("at", 0), (int, float))
    )


def _save():
    """Write the store; a failed write only costs the next run a fetch.

    One writer at a time, from the snapshot to the replace: two fetches that
    finish together must not let the older snapshot land on the newer one.
    """
    with _SAVE_LOCK:
        with _LOCK:
            entries = sorted(_STORE.items(), key=lambda item: item[1].get("at", 0))
            for key, _entry in entries[: max(0, len(entries) - _MAX_STORED)]:
                del _STORE[key]
            data = {
                "v": _STORE_VERSION,
                "releases": dict(_STORE),
                "hosts": dict(_PAUSED_HOSTS),
                "rest": dict(_REST_HOSTS),
            }
        tmp_path = ""
        try:
            path = _store_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = "{}.{}.tmp".format(path, threading.get_ident())
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp_path, path)
        except (OSError, TypeError, ValueError, RuntimeError):
            try:
                if tmp_path:
                    os.remove(tmp_path)
            except OSError:
                # Nothing was left behind, or it cannot be removed.
                pass


def _known(key):
    _load()
    with _LOCK:
        return _STORE.get(key)


def _remember(key, audio, channels, failed=False, exact=False, sure=False, video=None):
    """Keep one release's answer.

    ``exact`` when MediaInfo gave it; ``sure`` when it is known to be about
    this release (MediaInfo, or an NFO that names the release). ``video`` is
    what MediaInfo said of the video (``mediainfo_video``), when it did.
    """
    _load()
    entry = {
        "audio": list(audio),
        "channels": channels,
        "at": int(time.time()),
        "failed": bool(failed),
        "exact": bool(exact),
        "sure": bool(exact or sure),
    }
    if video:
        entry["video"] = dict(video)
    with _LOCK:
        _STORE[key] = entry


def _settled(key):
    """Whether ``key`` needs no fetch: answered, or failed a short while ago."""
    entry = _known(key)
    if entry is None:
        return False
    if not entry.get("failed"):
        return True
    return time.time() - entry.get("at", 0) < _RETRY_AFTER_SECONDS


def _host(url):
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _pause_host(host):
    """Leave ``host`` alone for a while: it refused a read."""
    if host:
        _load()
        with _LOCK:
            _PAUSED_HOSTS[host] = int(time.time())


def _host_paused(host):
    _load()
    with _LOCK:
        when = _PAUSED_HOSTS.get(host)
    return when is not None and time.time() - when < _RETRY_AFTER_SECONDS


def forget_all():
    """Drop what was read this run (for tests)."""
    with _LOCK:
        _STORE.clear()
        _PAUSED_HOSTS.clear()
        _REST_HOSTS.clear()
        del _LOADED[:]


# --- putting it on the rows -------------------------------------------------


# Object audio rides on a bed track: these are the beds it can be on.
_BEDS_OF = {"Atmos": ("TrueHD", "DD+"), "DTS:X": ("DTS-HD MA", "DTS-HD HR", "DTS")}


def _name_audio(meta):
    """``(formats, channels)`` as the release's name tags them.

    Once what was read has replaced them they are kept under
    ``NAME_AUDIO_KEY``, so a later pass starts from the name again. Atmos a
    row took from a labelled copy (``filter_atmos``) is a guess, not the
    name's, and is left out.
    """
    kept = meta.get(NAME_AUDIO_KEY)
    if isinstance(kept, dict) and isinstance(kept.get("audio"), list):
        return list(kept["audio"]), kept.get("channels") or ""
    audio = meta.get("audio")
    audio = list(audio) if isinstance(audio, list) else []
    if meta.get(_INFERRED_KEY) is True:
        audio = [name for name in audio if name != "Atmos"]
    return audio, meta.get("channels") or ""


def _preferred(name_audio, entry, quality):
    """The release's formats: what was read, not what the name tags.

    MediaInfo lists every track with its object-audio marker, so it is used
    as it is. An NFO is written by hand, and a line such as ``Audio: English
    TrueHD 7.1`` often leaves "Atmos" out: the name's Atmos or DTS:X tag is
    kept when the NFO's main track is the bed the name put it on (or a bed
    it can be on, for a name that tags it alone). A name tagged
    ``DDP.5.1.Atmos`` whose NFO leads with a plain TrueHD 7.1 track does not
    keep it: the picker would read "TrueHD Atmos".
    """
    audio = list(entry["audio"])
    if entry.get("exact"):
        return audio
    main = main_audio(audio, quality)
    name_beds = [name for name in name_audio if name not in OBJECT_FORMATS]
    for name in name_audio:
        if name not in OBJECT_FORMATS or name in audio:
            continue
        if main in _BEDS_OF.get(name, ()) and (not name_beds or main in name_beds):
            audio.append(name)
    return audio


def _added(name_audio, name_channels, entry, quality):
    """``(formats, channels)`` with what was read added to the name's.

    For an NFO that does not say which release it is about. The NFO's
    channel count is its main track's: it replaces the name's when what the
    NFO added becomes the release's main track ("DD5.1" in the name,
    "TrueHD 7.1" in the NFO), and fills in a missing one. The name's Atmos
    or DTS:X tag does not move onto a new main track the NFO names no
    object audio on; a name that tags the object audio alone keeps it.
    """
    added = [name for name in entry["audio"] if name not in name_audio]
    merged = name_audio + added
    new_main = main_audio(merged, quality) != main_audio(name_audio, quality)
    channels = name_channels
    if entry.get("channels") and (not name_channels or (added and new_main)):
        channels = entry["channels"]
    if not new_main or all(name in OBJECT_FORMATS for name in name_audio):
        return merged, channels
    main = main_audio(
        [name for name in merged if name not in OBJECT_FORMATS] or merged, quality
    )
    kept = [
        name
        for name in merged
        if name not in OBJECT_FORMATS or name == main or name in entry["audio"]
    ]
    return kept, channels


# The picker's order for dynamic range: Dolby Vision, HDR10+, HDR10, HLG.
_HDR_ORDER = ("Dolby Vision", "HDR10+", "HDR10", "HLG", "SDR")
_VIDEO_FIELDS = ("dv_profile", "bit_depth")


def _apply_audio(meta, entry):
    """Put the audio that was read on one row's metadata; True if it changed.

    An answer known to be about this release (MediaInfo, or an NFO that
    names the release) replaces what the name tags (``_preferred``). An NFO
    that does not say which release it is about only adds to the name
    (``_added``). The name's own stay under ``NAME_AUDIO_KEY``.
    """
    name_audio, name_channels = _name_audio(meta)
    quality = meta.get("quality", "")
    guessed = meta.get(_INFERRED_KEY) is True
    if entry.get("exact") or entry.get("sure"):
        source = "mediainfo" if entry.get("exact") else "nfo"
        audio = _preferred(name_audio, entry, quality)
        channels = entry.get("channels") or name_channels
        guessed = False
    else:
        source = UNCONFIRMED
        audio, channels = _added(name_audio, name_channels, entry, quality)
    named = [name for name in audio if name not in name_audio]
    if guessed and "Atmos" not in audio:
        # Atmos taken from a labelled copy stays beside what was added.
        audio = ["Atmos"] + audio
    same = meta.get("audio") == audio and (
        not channels or meta.get("channels") == channels
    )
    if same and meta.get(SOURCE_KEY) == source and NAME_AUDIO_KEY in meta:
        return False
    meta[NAME_AUDIO_KEY] = {"audio": name_audio, "channels": name_channels}
    meta[SOURCE_KEY] = source
    # What the name did not tag, so a later pass can tell the two apart.
    meta[FROM_NFO_KEY] = named
    if not guessed:
        meta.pop(_INFERRED_KEY, None)
    if same:
        return False
    meta["audio"] = audio
    if channels:
        meta["channels"] = channels
    return True


def _apply_video(meta, entry):
    """Put MediaInfo's video facts on one row's metadata; True if it changed.

    The Dolby Vision profile and the bit depth are added. The dynamic range
    replaces the name's when MediaInfo named the HDR format or showed the
    video to be SDR (``hdr_exact``); otherwise it adds to the name's. The
    name's own stays under ``NAME_HDR_KEY``, so a later pass starts from the
    name again.
    """
    video = entry.get("video")
    if not isinstance(video, dict):
        return False
    changed = False
    for field in _VIDEO_FIELDS:
        value = video.get(field)
        if isinstance(value, str) and value and meta.get(field) != value:
            meta[field] = value
            changed = True
    read = video.get("hdr")
    if not isinstance(read, list) or not read:
        return changed
    read = [name for name in read if name in _HDR_ORDER]
    if not read:
        return changed
    kept = meta.get(NAME_HDR_KEY)
    name_hdr = meta.get("hdr") if not isinstance(kept, list) else kept
    name_hdr = [name for name in (name_hdr or []) if isinstance(name, str)]
    if video.get("hdr_exact") is True:
        hdr = read
    else:
        hdr = [name for name in name_hdr if name != "SDR"]
        hdr = hdr + [name for name in read if name not in hdr]
    hdr = sorted(
        hdr, key=lambda name: _HDR_ORDER.index(name) if name in _HDR_ORDER else 99
    )
    if NAME_HDR_KEY not in meta:
        meta[NAME_HDR_KEY] = name_hdr
    if meta.get("hdr") != hdr:
        meta["hdr"] = hdr
        changed = True
    return changed


def apply_known(rows, key_of, derived_keys=()):
    """Give every listing of a release what was read for it.

    ``key_of`` gives a release name's key (``filter._name_form_key``): every
    spelling of a name, from any indexer, takes the same answer: its audio
    (``_apply_audio``) and what MediaInfo said of its video
    (``_apply_video``). A row that changes loses its ``derived_keys``
    (per-row caches built from its metadata). Returns how many rows changed.
    """
    changed = 0
    for row in rows:
        meta = row.get("_meta") if isinstance(row, dict) else None
        title = row.get("title") if isinstance(row, dict) else None
        if not isinstance(meta, dict) or not isinstance(title, str):
            continue
        entry = _known(key_of(title))
        if not entry:
            continue
        audio_changed = bool(entry["audio"]) and _apply_audio(meta, entry)
        if _apply_video(meta, entry) or audio_changed:
            for name in derived_keys:
                row.pop(name, None)
            changed += 1
    return changed


# --- is this NFO about this release? ----------------------------------------

CONFIRMED = "confirmed"
UNCONFIRMED = "unconfirmed"
WRONG = "wrong"

# A release name inside an NFO: dotted, with a year, an episode or a
# resolution in it ("Some.Movie.2004.1080p.BluRay.x264-GRP").
_NAME_IN_NFO = re.compile(r"[A-Za-z0-9][A-Za-z0-9+\-]*(?:[._][A-Za-z0-9+\-]+){3,}")
_NAME_MARK = re.compile(r"(?:19|20)\d\d|s\d{1,2}e\d{1,3}|\d{3,4}p", re.I)
_CONTAINER = re.compile(r"\.(?:mkv|mp4|avi|m2ts|ts|nzb|nfo)$", re.I)
_MAX_NAMES_CHECKED = 12
_MIN_NAME_LENGTH = 12


def _words(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _same_work(ours, theirs):
    """Whether two parsed names are the same film or the same episode."""
    if not ours.get("title") or ours.get("title") != theirs.get("title"):
        return False
    if ours.get("seasons") or ours.get("episodes"):
        return ours.get("seasons") == theirs.get("seasons") and ours.get(
            "episodes"
        ) == theirs.get("episodes")
    if theirs.get("seasons") or theirs.get("episodes"):
        return False
    return (
        not ours.get("year")
        or not theirs.get("year")
        or (ours["year"] == theirs["year"])
    )


def names_release(text, title):
    """Whether an NFO holds the release's own name, word for word.

    Stricter than ``whose_nfo``'s ``CONFIRMED``, which also accepts a name
    that parses to the same film or episode from the same group: that can be
    the group's other encode (its 1080p BluRay beside this 2160p WEB-DL),
    whose video is not this release's.
    """
    wanted = _words(_CONTAINER.sub("", str(title or "").strip()))
    return len(wanted) >= _MIN_NAME_LENGTH and wanted in _words(text)


def whose_nfo(text, title, describe):
    """Whether an NFO is about the release named ``title``.

    Indexers attach NFOs by matching, and a release can carry another
    release's NFO. ``CONFIRMED`` when the NFO holds the release's name, or
    a name that parses to the same film or episode from the same group.
    ``WRONG`` when it names releases and none of them is this film or
    episode, or the one that is comes from another group: its audio is
    another encode's. ``UNCONFIRMED`` when it names no release at all (a
    bare MediaInfo dump, a greeting).

    ``describe`` parses a release name into ``{"title", "year", "seasons",
    "episodes", "group"}`` (``filter.release_identity``); without it nothing
    can be compared and the NFO is unconfirmed.
    """
    name = _CONTAINER.sub("", str(title or "").strip())
    if names_release(text, title):
        return CONFIRMED
    if describe is None:
        return UNCONFIRMED
    names = []
    for found in _NAME_IN_NFO.findall(text):
        found = _CONTAINER.sub("", found)
        if _NAME_MARK.search(found) and found not in names:
            names.append(found)
        if len(names) >= _MAX_NAMES_CHECKED:
            break
    if not names:
        return UNCONFIRMED
    try:
        ours = describe(name)
        verdict = WRONG
        for found in names:
            theirs = describe(found)
            if not _same_work(ours, theirs):
                continue
            if not theirs.get("group"):
                verdict = UNCONFIRMED
            elif theirs["group"] == ours.get("group"):
                return CONFIRMED
        return verdict
    except Exception:  # pylint: disable=broad-except
        return UNCONFIRMED


# --- asking the indexers ----------------------------------------------------


def _releases_setting(settings_getter):
    try:
        raw = settings_getter(SETTING, str(DEFAULT_RELEASES))
        value = int(str(raw).strip())
    except Exception:  # pylint: disable=broad-except
        return DEFAULT_RELEASES
    return max(0, min(value, MAX_RELEASES))


def _candidates(rows, key_of, limit):
    """``[(key, nfo_urls, title, ref)]`` to fetch among the first ``limit``
    releases.

    A release is the rows that share a name key, in the order given (the
    picker's order). Only the first ``limit`` releases are looked at, so a
    search never asks for more however often it is opened: one of them that
    is already settled, or that no listing offers anything for, is not
    replaced by a release further down. ``nfo_urls`` are the release's NFO
    links to try, in order, and ``ref`` the link the MediaInfo call is built
    from (``_listing``).
    """
    seen = set()
    found = []
    keyed = None
    for row in rows:
        title = row.get("title") if isinstance(row, dict) else None
        if not isinstance(title, str) or not title:
            continue
        key = key_of(title)
        if not key or key in seen:
            continue
        if len(seen) >= limit:
            break
        seen.add(key)
        if _settled(key):
            continue
        if keyed is None:
            keyed = _keyed_links(rows)
        nfo_urls, ref = _listing(key, rows, key_of, keyed)
        if nfo_urls or ref:
            found.append((key, nfo_urls, title, ref))
    return found


def _row_link(row, name):
    value = row.get(name) if isinstance(row, dict) else None
    return value if isinstance(value, str) else ""


def _keyed_links(rows):
    """``{host: link}``: for each indexer, a link of this search with its key.

    A result that brings no link of its own borrows the key of another
    result of the same indexer (``indexer_media.with_key_from``).
    """
    keyed = {}
    for row in rows:
        for name in ("nfo_url", "media_ref"):
            link = _row_link(row, name)
            host = _host(link) if link else ""
            if host and host not in keyed and has_key(link):
                keyed[host] = link
    return keyed


# How many NFOs are read for one release, at most: a second indexer's when
# the first had none to give.
_MAX_NFO_READS = 2


def _listing(key, rows, key_of, keyed):
    """``(nfo_urls, ref)``: what to ask about the release ``key``, and where.

    A release can be listed by several indexers. Listings on a paused
    indexer are passed over. Of the others:

    * ``ref`` is the link the MediaInfo call is built from: a listing on an
      indexer that serves MediaInfo (MediaInfo is exact), with an NFO link or
      a ``media_ref``. Without one it is the first NFO link, whose indexer is
      probed when it is not in the table, or else a ``media_ref`` on an
      indexer that may have the call.
    * ``nfo_urls`` are the NFO links to fall back on, one per indexer and
      ``_MAX_NFO_READS`` at most. An indexer that flags only the releases it
      has an NFO for comes before the one that serves MediaInfo, which flags
      every release and often has no NFO to give.
    """
    nfo_first, nfo_last, hosts = [], [], set()
    ref, probe = "", ""
    for row in rows:
        title = row.get("title") if isinstance(row, dict) else None
        if not isinstance(title, str) or key_of(title) != key:
            continue
        nfo_url = _row_link(row, "nfo_url")
        link = nfo_url or _row_link(row, "media_ref")
        host = _host(link) if link else ""
        if not host or _host_paused(host):
            continue
        serves = serves_mediainfo(host) is True
        if nfo_url:
            if host not in hosts:
                hosts.add(host)
                (nfo_last if serves else nfo_first).append(nfo_url)
            if serves and not ref:
                ref = nfo_url
            continue
        if not has_key(link):
            link = with_key_from(link, keyed.get(host, ""))
        if not _may_ask_mediainfo(host) or not mediainfo_audio.request_for(link):
            continue
        if serves and not ref:
            ref = link
        elif not serves and not probe:
            probe = link
    nfo_urls = tuple((nfo_first + nfo_last)[:_MAX_NFO_READS])
    if not ref:
        ref = nfo_urls[0] if nfo_urls else probe
    return nfo_urls, ref


# HTTP statuses, and newznab error codes, that mean "not for you, not now":
# a wrong or blocked key, a client the indexer refuses, a limit reached.
_REFUSAL_STATUSES = frozenset((401, 403, 429))


def _is_refusal_code(code):
    return 100 <= code < 200 or 500 <= code < 600


# A newznab error reply, sent with HTTP 200 in place of the NFO.
_ERROR_REPLY = re.compile(
    r"^\s*(?:<\?xml[^>]*\?>)?\s*<error\b[^>]*\bcode=\"(\d+)\"", re.I
)
# The codes that say the release has no NFO to give: nothing to ask for again.
_NO_NFO_CODES = frozenset((300,))


def _error_code(text):
    """The code of a newznab error reply, or None when ``text`` is not one."""
    match = _ERROR_REPLY.match(str(text or "")[:512])
    return int(match.group(1)) if match else None


def _may_ask_mediainfo(host):
    """Whether ``host`` is worth asking for MediaInfo: it has the call, or might.

    An indexer in the table (``indexer_media.KNOWN``) is asked only when it
    has the call. One that is not is probed, and not again for a while once
    it was found to have none.
    """
    known = serves_mediainfo(host)
    if known is not None:
        return known
    _load()
    with _LOCK:
        state = _REST_HOSTS.get(host)
    if state is None or state["ok"]:
        return True
    return time.time() - state["at"] >= _NO_API_SECONDS


def _has_mediainfo_call(host):
    """Whether ``host`` has been seen to answer the MediaInfo call."""
    _load()
    with _LOCK:
        state = _REST_HOSTS.get(host)
    return bool(state and state["ok"])


def _note_rest(host, has_call):
    _load()
    with _LOCK:
        _REST_HOSTS[host] = {"ok": bool(has_call), "at": int(time.time())}


def _get_with_status(url, headers):
    """``(status, body)`` of a GET; the status is None when nothing answered."""
    try:
        body = http_get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers=headers,
            max_bytes=_MAX_MEDIAINFO_BYTES,
        )
        return 200, body
    except HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:  # pylint: disable=broad-except
            body = ""
        return exc.code, body
    except Exception:  # pylint: disable=broad-except
        return None, ""


def _fetch_mediainfo(key, link, title):
    """Ask the indexer for the release's MediaInfo, where it may have the call.

    ``link`` names the release and carries the key (``mediainfo_audio.
    request_for``). Returns ``(answer, video, what)``. ``answer`` is True or
    False when that settled the release (audio found, or the indexer
    refused), None when the NFO should be read: no such call there, no
    MediaInfo for this release, none of its audio recognised, or no answer.
    ``video`` is what the report said of the video. ``what`` is what the
    reply meant (``mediainfo_audio.TRACKS`` and the others), None when the
    indexer was not asked.
    """
    host = _host(link)
    request = mediainfo_audio.request_for(link)
    if request is None or not _may_ask_mediainfo(host):
        return None, {}, None
    status, body = _get_with_status(*request)
    what, tracks = mediainfo_audio.read_reply(status, body)
    in_table = serves_mediainfo(host) is True
    if (
        what == mediainfo_audio.REFUSED
        and status != 429
        and not in_table
        and not _has_mediainfo_call(host)
    ):
        # An indexer never seen to answer this call, refusing the path: that
        # is how one without the call may answer. Its NFO is still read.
        what = mediainfo_audio.NO_API
    if what == mediainfo_audio.NO_API and in_table:
        # An odd answer from an indexer known to have the call decides
        # nothing about it.
        what = mediainfo_audio.FAILED
    if what == mediainfo_audio.REFUSED:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: MediaInfo for '{}' was refused by the "
            "indexer".format(title),
            xbmc.LOGINFO,
        )
        _remember(key, [], "", failed=True)
        _pause_host(host)
        search_progress.details_result(
            host, search_progress.MEDIAINFO, search_progress.REFUSED
        )
        return False, {}, what
    if what == mediainfo_audio.NO_API:
        _note_rest(host, False)
        return None, {}, what
    if what == mediainfo_audio.FAILED:
        search_progress.details_result(
            host, search_progress.MEDIAINFO, search_progress.FAILED
        )
        return None, {}, what
    _note_rest(host, True)
    if what != mediainfo_audio.TRACKS:
        search_progress.details_result(
            host, search_progress.MEDIAINFO, search_progress.NOTHING
        )
        return None, {}, what
    video = mediainfo_video.from_reply(body)
    if video:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: MediaInfo for '{}' names video: {}".format(
                title, _video_words(video)
            ),
            xbmc.LOGINFO,
        )
    found = mediainfo_audio.merge_tracks(tracks)
    search_progress.details_result(
        host,
        search_progress.MEDIAINFO,
        search_progress.READ if found["audio"] or video else search_progress.NOTHING,
    )
    if not found["audio"]:
        return None, video, what
    _remember(key, found["audio"], found["channels"], exact=True, video=video)
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: MediaInfo for '{}' names audio: {}".format(
            title, ", ".join(found["audio"])
        ),
        xbmc.LOGINFO,
    )
    return True, video, what


def _video_words(video):
    """ "Dolby Vision (profile 8), HDR10, 10 bit" for the log."""
    names = []
    for name in video.get("hdr") or ():
        profile = video.get("dv_profile")
        if name == "Dolby Vision" and profile:
            name = "{} (profile {})".format(name, profile)
        names.append(name)
    if video.get("bit_depth"):
        names.append("{} bit".format(video["bit_depth"]))
    return ", ".join(names) or "nothing known"


def _fetch(key, nfo_urls, title, describe=None, ref=""):
    """Learn one release's audio and video: MediaInfo first, then its NFO.

    ``ref`` is the link the MediaInfo call is built from, the first NFO link
    when none is given. ``nfo_urls`` are the NFO links to fall back on: the
    next one is read when an indexer had no NFO for the release, or one that
    names no audio, and not after a read that failed. True when something
    was learned. The answer, or that there was none, is remembered either
    way.

    One case remembers nothing: a release with no NFO link whose indexer
    turned out to have no MediaInfo call, or was not asked. Nothing was
    learned about the release, and another indexer's listing of it may
    still bring an NFO.
    """
    if isinstance(nfo_urls, str):
        nfo_urls = (nfo_urls,) if nfo_urls else ()
    first = nfo_urls[0] if nfo_urls else ""
    answer, video, what = _fetch_mediainfo(key, ref or first, title)
    if answer is not None:
        return answer
    learned = False
    for nfo_url in nfo_urls:
        learned = _fetch_nfo(key, nfo_url, title, describe, video) or learned
        entry = _known(key) or {}
        if entry.get("audio") or entry.get("failed"):
            break
        # What was read of the video so far goes on to the next read.
        video = video or entry.get("video")
    if nfo_urls:
        return learned
    if what == mediainfo_audio.FAILED:
        _remember_no_audio(key, video, failed=True)
    elif what in (mediainfo_audio.TRACKS, mediainfo_audio.MISSING):
        _remember_no_audio(key, video)
    return bool(video)


def _remember_no_audio(key, video, failed=False):
    """Settle a release whose audio was not learned.

    What MediaInfo said of its video is kept, and is exact; without it a
    failed read is asked about again another day.
    """
    _remember(key, [], "", failed=failed and not video, exact=bool(video), video=video)


# nZEDb's info call returns the NFO with HTML line breaks, with or without
# the line ending after each: every break becomes one line ending.
_HTML_BREAK = re.compile(r"<br\s*/?>(?:\r?\n)?", re.I)


def _fetch_nfo(key, url, title, describe=None, video=None):
    """Fetch one NFO, read it and remember the answer; True when it told
    something.

    ``video`` is what the release's MediaInfo already said of its video.
    Without it, the video block of an NFO that is MediaInfo text is read,
    when the NFO holds this release's own name (``names_release``): the
    same group's other encode of the film has another video.
    """
    if _host_paused(_host(url)):
        # Its indexer refused a read a moment ago: not sent, asked another day.
        _remember_no_audio(key, video, failed=True)
        return bool(video)
    try:
        text = http_get(url, timeout=_FETCH_TIMEOUT, max_bytes=_MAX_NFO_BYTES)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' could not be read: {}".format(
                title, redact_text(str(exc))
            ),
            xbmc.LOGINFO,
        )
        _remember_no_audio(key, video, failed=True)
        refused = getattr(exc, "code", None) in _REFUSAL_STATUSES
        if refused:
            _pause_host(_host(url))
        search_progress.details_result(
            _host(url),
            search_progress.NFO,
            search_progress.REFUSED if refused else search_progress.FAILED,
        )
        return bool(video)
    error = _error_code(text)
    if error is not None and error not in _NO_NFO_CODES:
        # The indexer answered with an error, not an NFO (a bad key, a limit
        # reached): ask again another day instead of settling on "no audio".
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' was refused by the indexer "
            "(error {})".format(title, error),
            xbmc.LOGINFO,
        )
        _remember_no_audio(key, video, failed=True)
        if _is_refusal_code(error):
            _pause_host(_host(url))
        search_progress.details_result(
            _host(url), search_progress.NFO, search_progress.REFUSED
        )
        return bool(video)
    text = _HTML_BREAK.sub("\n", text)
    whose = whose_nfo(text, title, describe)
    if whose == WRONG:
        # Another release's NFO: what it says of the audio is not ours.
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' is about another release: "
            "not used".format(title),
            xbmc.LOGINFO,
        )
        _remember_no_audio(key, video)
        search_progress.details_result(
            _host(url), search_progress.NFO, search_progress.NOTHING
        )
        return bool(video)
    found = audio_from_nfo(text)
    if not video and names_release(text, title):
        video = mediainfo_video.from_nfo(text)
    _remember(
        key,
        found["audio"],
        found["channels"],
        sure=whose == CONFIRMED,
        video=video,
    )
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: NFO for '{}' names audio: {}{}{}".format(
            title,
            ", ".join(found["audio"]) or "none",
            "; video: {}".format(_video_words(video)) if video else "",
            "" if whose == CONFIRMED else " (the NFO does not name the release)",
        ),
        xbmc.LOGINFO,
    )
    told = bool(found["audio"]) or bool(video)
    search_progress.details_result(
        _host(url),
        search_progress.NFO,
        search_progress.READ if told else search_progress.NOTHING,
    )
    return told


def _first_host(nfo_urls, ref):
    """The indexer a release's first request goes to."""
    return _host(ref or (nfo_urls[0] if nfo_urls else ""))


def learn_for_top(rows, key_of, settings_getter, wait=_WAIT_SECONDS, describe=None):
    """Read the MediaInfo or NFO of the top releases of ``rows``; True if any
    told something.

    ``rows`` are the picker's rows in its order. The fetches run on daemon
    threads, one per indexer, and this waits ``wait`` seconds for them. One
    that is slower still finishes and is stored for the next search. True
    means the rows should be parsed again to take the new facts.
    ``describe`` parses a release name (see ``whose_nfo``).
    """
    try:
        limit = _releases_setting(settings_getter)
        if limit <= 0:
            search_progress.details_skipped(turned_off=True)
            return False
        candidates = _candidates(rows, key_of, limit)
    except Exception:  # pylint: disable=broad-except
        return False
    if not candidates:
        search_progress.details_skipped()
        return False
    results = []

    def _work(batch):
        # One indexer's reads, one after another: when it refuses one, the
        # rest are not sent.
        try:
            for key, nfo_urls, title, ref in batch:
                if _host_paused(_first_host(nfo_urls, ref)):
                    break
                results.append(_fetch(key, nfo_urls, title, describe, ref))
        finally:
            _save()

    by_host = {}
    for candidate in candidates:
        host = _first_host(candidate[1], candidate[3])
        by_host.setdefault(host, []).append(candidate)
    threads = [
        threading.Thread(target=_work, args=(batch,), daemon=True)
        for batch in by_host.values()
    ]
    search_progress.details_started(len(candidates), list(by_host))
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.0, wait)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    search_progress.details_finished()
    return any(results)
