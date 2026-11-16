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


def plan(rows, record):
    """``{host: {"mediainfo": link, "nfo": link}}``: what to ask, and of whom.

    ``rows`` are the listings of one release; ``record`` is what is already
    kept for it. An indexer that is paused, or that was already asked, is
    left out.
    """
    keyed = {}
    for row in rows:
        for name in ("nfo_url", "media_ref"):
            link = _link(row, name)
            host = nfo_audio.host_of(link) if link else ""
            if host and host not in keyed and has_key(link):
                keyed[host] = link
    jobs = {}
    for row in rows:
        nfo_url = _link(row, "nfo_url")
        link = nfo_url or _link(row, "media_ref")
        host = nfo_audio.host_of(link) if link else ""
        if not host or nfo_audio.host_paused(host):
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
            key, release_docs.MEDIAINFO, host, mediainfo_audio.mediainfo_xml_of(body)
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
            release_docs.keep(key, release_docs.NFO, host, text)
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


def gather(rows, key, title, describe=None, wait=WAIT_SECONDS):
    """Ask every indexer listing the release for what it has; returns how
    many indexers were asked.

    The requests run on daemon threads, one per indexer, and this waits
    ``wait`` seconds for them. A slower answer is still kept when it comes.
    """
    if not key:
        return 0
    jobs = plan(rows, release_docs.load(key))
    threads = [
        threading.Thread(
            target=_ask, args=(key, host, job, title, describe), daemon=True
        )
        for host, job in jobs.items()
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + max(0.0, wait)
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return len(threads)
