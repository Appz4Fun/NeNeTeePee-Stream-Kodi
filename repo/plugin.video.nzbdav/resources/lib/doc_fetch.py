# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Read a release's NFO and MediaInfo from every indexer that lists it.

The search itself reads these for the top few releases only
(``nfo_audio``). The picker's viewer shows them for whichever release the
user opens it on, and names every indexer that carries each text, so when it
opens, each indexer that offers something for the release and was not asked
yet is asked once:

* for its MediaInfo, where it serves that (``indexer_media``);
* for its NFO, where the result came with a link to one.

That is what the release's own page on the indexer shows: one request per
indexer as a rule, two for an indexer that has both. What comes back, or
that there was nothing, is kept (``release_docs``), so a second opening
asks nobody. An indexer that refuses is left alone for a day, as in
``nfo_audio``. Links carry the user's key and are never logged.
"""

import threading
import time

import xbmc

from resources.lib import mediainfo_audio, nfo_audio, release_docs
from resources.lib.http_util import http_get, redact_text
from resources.lib.indexer_media import has_key, serves_mediainfo, with_key_from

_FETCH_TIMEOUT = 6
_MAX_NFO_BYTES = 262144
# How long the viewer waits for the indexers before opening with what it has.
WAIT_SECONDS = 8.0


def _link(row, name):
    value = row.get(name) if isinstance(row, dict) else None
    return value if isinstance(value, str) else ""


_RUNNING_LOCK = threading.Lock()
# (release key, host) -> the thread asking that indexer about that release.
_RUNNING = {}


def running(key):
    """How many indexers are being asked about the release ``key`` right now."""
    with _RUNNING_LOCK:
        for pair in [
            pair for pair, thread in _RUNNING.items() if not thread.is_alive()
        ]:
            del _RUNNING[pair]
        return sum(1 for release, _host in _RUNNING if release == key)


def wait(key, seconds):
    """Wait up to ``seconds`` for the indexers asked about ``key``; returns
    how many have still not answered."""
    deadline = time.monotonic() + max(0.0, seconds)
    with _RUNNING_LOCK:
        threads = [
            thread for (release, _host), thread in _RUNNING.items() if release == key
        ]
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return running(key)


def asking(rows, key):
    """How many indexers have yet to answer for the release ``key``: the
    ones ``gather`` would ask, and the ones being asked."""
    if not key:
        return 0
    return len(plan(rows, release_docs.load(key), key)) + running(key)


def plan(rows, record, key=""):
    """``{host: {"mediainfo": link, "nfo": link}}``: what to ask, and of whom.

    ``rows`` are the listings of one release; ``record`` is what is already
    kept for it. An indexer that is paused, or that was already asked, is
    left out, and so is one that is being asked about the release ``key``
    at this moment: closing the viewer and opening it again sends nothing
    twice.
    """
    keyed = {}
    for row in rows:
        for name in ("nfo_url", "media_ref"):
            link = _link(row, name)
            host = nfo_audio.host_of(link) if link else ""
            if host and host not in keyed and has_key(link):
                keyed[host] = link
    with _RUNNING_LOCK:
        busy = {pair for pair, thread in _RUNNING.items() if thread.is_alive()}
    jobs = {}
    for row in rows:
        nfo_url = _link(row, "nfo_url")
        link = nfo_url or _link(row, "media_ref")
        host = nfo_audio.host_of(link) if link else ""
        if not host or nfo_audio.host_paused(host) or (key, host) in busy:
            continue
        job = jobs.setdefault(host, {})
        if nfo_url and "nfo" not in job:
            if not release_docs.settled(record, release_docs.NFO, host):
                job["nfo"] = nfo_url
        if serves_mediainfo(host) is True and "mediainfo" not in job:
            if not release_docs.settled(record, release_docs.MEDIAINFO, host):
                keyed_link = (
                    link if has_key(link) else with_key_from(link, keyed.get(host, ""))
                )
                if keyed_link and mediainfo_audio.request_for(keyed_link):
                    job["mediainfo"] = keyed_link
    return {host: job for host, job in jobs.items() if job}


def offered(rows, key):
    """``(nfo, mediainfo)``: what the picker can say a release has.

    An NFO is one that is kept, or one a listing links to on an indexer not
    yet found to have none. A MediaInfo report is only known once it is
    kept: the indexers that serve one do not say which releases have it.
    Cheap, for every row of the picker: it reads ``release_docs``' index.
    """
    has_nfo, has_mediainfo = release_docs.kinds_known(key)
    if not has_nfo:
        none = release_docs.asked_none(key)
        for row in rows:
            link = _link(row, "nfo_url")
            host = nfo_audio.host_of(link) if link else ""
            if host and "{} {}".format(release_docs.NFO, host) not in none:
                has_nfo = True
                break
    return has_nfo, has_mediainfo


def _read_mediainfo(key, host, link):
    """Ask ``host`` for the report; True when it was kept, None on a refusal."""
    status, body = nfo_audio.get_with_status(*mediainfo_audio.request_for(link))
    what, _tracks = mediainfo_audio.read_reply(status, body)
    if what == mediainfo_audio.TRACKS:
        release_docs.keep(
            key,
            release_docs.MEDIAINFO,
            host,
            nfo_audio.without_keys(mediainfo_audio.mediainfo_xml_of(body), link),
        )
        return True
    if what == mediainfo_audio.MISSING:
        release_docs.mark_none(key, release_docs.MEDIAINFO, host)
    if what == mediainfo_audio.REFUSED:
        nfo_audio.pause_host(host)
        return None
    return False


def _read_nfo(key, host, link, title, describe):
    """Fetch one NFO and keep it; False when the indexer refused."""
    try:
        body = http_get(link, timeout=_FETCH_TIMEOUT, max_bytes=_MAX_NFO_BYTES)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NFO for '{}' could not be read: {}".format(
                title, redact_text(str(exc))
            ),
            xbmc.LOGINFO,
        )
        if getattr(exc, "code", None) in nfo_audio.REFUSAL_STATUSES:
            nfo_audio.pause_host(host)
            return False
        return True
    kind, text = nfo_audio.read_nfo_reply(body)
    if kind == "pause":
        nfo_audio.pause_host(host)
        return False
    if kind == "none" or (kind == "nfo" and not text.strip()):
        # No NFO there, said as an error or as an empty answer.
        release_docs.mark_none(key, release_docs.NFO, host)
    elif kind == "nfo":
        if nfo_audio.shown_for(text, title, describe):
            release_docs.keep(
                key, release_docs.NFO, host, nfo_audio.without_keys(text, link)
            )
        else:
            # Another release's NFO, attached by the indexer's matching.
            release_docs.mark_none(key, release_docs.NFO, host)
    return True


def _ask(key, host, job, title, describe):
    """One indexer's requests, one after the other."""
    try:
        if "mediainfo" in job:
            if _read_mediainfo(key, host, job["mediainfo"]) is None:
                # The indexer refused: it is asked nothing more today.
                return
        if "nfo" in job:
            _read_nfo(key, host, job["nfo"], title, describe)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: reading release details failed: {}".format(
                redact_text(str(exc))
            ),
            xbmc.LOGWARNING,
        )
    finally:
        with _RUNNING_LOCK:
            if _RUNNING.get((key, host)) is threading.current_thread():
                del _RUNNING[(key, host)]


def gather(rows, key, title, describe=None, wait_seconds=WAIT_SECONDS):
    """Ask every indexer listing the release for what it has; returns how
    many indexers were newly asked.

    The requests run on daemon threads, one per indexer, and this waits
    ``wait_seconds`` for every indexer being asked about the release, the
    ones an earlier call started included. A slower answer is still kept
    when it comes: ``running`` says how many are outstanding, and ``wait``
    waits for them.
    """
    if not key:
        return 0
    jobs = plan(rows, release_docs.load(key), key)
    started = 0
    for host, job in jobs.items():
        thread = threading.Thread(
            target=_ask, args=(key, host, job, title, describe), daemon=True
        )
        with _RUNNING_LOCK:
            if (key, host) in _RUNNING and _RUNNING[(key, host)].is_alive():
                continue
            _RUNNING[(key, host)] = thread
        thread.start()
        started += 1
    wait(key, wait_seconds)
    return started
