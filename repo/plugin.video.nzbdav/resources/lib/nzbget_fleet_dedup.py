# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Same-release selection and same-posting dedup for the NZBGet duplicate fleet.

The NZBGet backend submits every NZB of the picked release as a Smart-Duplicates
backup (#372). Two NZBs that describe the SAME Usenet posting add nothing as a
backup, so this module collapses them before submission:

* **Same listing** (no fetch): several indexers list one posting with the same
  size and a post date within ``SAME_LISTING_WINDOW_SECONDS``. Only one listing
  per posting is fetched, which saves scarce indexer grabs; the others stay as
  fallbacks for a grab that fails.
* **Same posting** (after fetch): two NZBs sharing more than 1% of their article
  Message-IDs (measured against the smaller set) are the same posting -- an
  indexer re-lists a posting with one re-uploaded segment, while distinct
  postings share none. A byte-identical repost with 0% shared IDs is a
  DIFFERENT posting and the best kind of backup, so it is kept.

Release identity never uses size: postings of one release can differ by GBs.
"""

import collections
import os
import shutil
import tempfile
import threading
import zlib
from array import array

import xbmc

from resources.lib.http_util import pubdate_to_epoch, redact_text

SAME_LISTING_WINDOW_SECONDS = 120
PREFETCH_WINDOW = 4

# A fingerprint keeps EVERY article's CRC32 (no sampling, so the 1% rule is
# exact) as a sorted ``array('I')``: 4 bytes per article, so even a large
# remux fleet stays a few tens of MiB on a CoreELEC box.
_SAME_POSTING_SHARE = 0.01


# Release variants the fallback-stream gates leave open (the stream proxy's
# byte checks cover them there; an NZBGet failover plays whatever it gets).
_VARIANT_FLAGS = (
    "3d",
    "dubbed",
    "subbed",
    "hardcoded",
    "extended",
    "unrated",
    "uncensored",
    "remastered",
)


def _variant_signature(title):
    """PTT variant flags plus sorted languages for a release title."""
    from resources.lib.ptt import parse_title

    parsed = parse_title(str(title or ""))
    flags = tuple(bool(parsed.get(key)) for key in _VARIANT_FLAGS)
    languages = tuple(
        sorted(str(lang).lower() for lang in parsed.get("languages") or [])
    )
    return flags, languages


def same_variant(pick, row):
    """Whether ``row`` has the pick's 3D, dub/sub, hardsub, cut, and languages.

    The NZBGet-only half of ``same_release``: the fallback loader's
    same-content extras skip it upstream (the stream proxy byte-verifies a
    switch there), but an NZBGet failover plays whatever it promotes.
    Fail-closed on a parse error.
    """
    try:
        return _variant_signature(pick.get("title")) == _variant_signature(
            row.get("title")
        )
    except Exception:  # pylint: disable=broad-except
        return False


def same_release(pick, row):
    """Whether ``row`` is the same release as ``pick`` (fail-closed).

    Reuses the hardened fallback-stream gates: ``_same_content`` (title, year or
    SxxEyy, part, edition, PROPER/REPACK) plus the same-group profile match
    (group and resolution parsed and equal, no conflicting profile, HDR, or
    audio fields). Those gates are deliberately loose for the stream proxy,
    which byte-verifies a fallback before switching; an NZBGet failover has no
    such check, so the 3D, dubbed/MULTi, subbed, hardcoded-subs, cut flags and
    the language set must match exactly too. Any parse error rejects.
    """
    from resources.lib import fallback_streams as _fs

    try:
        return (
            bool(_fs._same_content(pick, row))
            and bool(_fs._metadata_profiles_match(pick, row, require_same_group=True))
            and same_variant(pick, row)
        )
    except Exception:  # pylint: disable=broad-except
        return False


def _size_bytes(row):
    try:
        return int(str(row.get("size") or "").strip())
    except (TypeError, ValueError):
        return 0


def _posted_epoch(row):
    epoch = row.get("_posted_epoch")
    if isinstance(epoch, int) and epoch > 0:
        return epoch
    pubdate = row.get("pubdate")
    return pubdate_to_epoch(pubdate) if pubdate else None


def same_listing(left, right):
    """Whether two indexer rows list the same posting (equal size, close post date).

    Rows missing a size or a parseable post date never match.
    """
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    size = _size_bytes(left)
    if size <= 0 or size != _size_bytes(right):
        return False
    left_epoch = _posted_epoch(left)
    right_epoch = _posted_epoch(right)
    if left_epoch is None or right_epoch is None:
        return False
    return abs(left_epoch - right_epoch) <= SAME_LISTING_WINDOW_SECONDS


def posting_fingerprint(nzb_bytes):
    """Return a sorted ``array('I')`` of every article's CRC32, or None.

    None for unparseable XML or an NZB without any segment Message-IDs.
    """
    from resources.lib.nzb_manifest import _children_by_name, _parse_nzb_root

    root = _parse_nzb_root(nzb_bytes)
    if root is None:
        return None
    hashes = set()
    for file_elem in _children_by_name(root, "file"):
        for segments in _children_by_name(file_elem, "segments"):
            for segment in _children_by_name(segments, "segment"):
                msgid = (segment.text or "").strip().strip("<>").lower()
                if msgid:
                    hashes.add(zlib.crc32(msgid.encode("utf-8")))
    if not hashes:
        return None
    return array("I", sorted(hashes))


def _shares_posting(probe, probe_len, other):
    """Whether set ``probe`` shares more than 1% of the smaller article set."""
    smaller = min(probe_len, len(other))
    if smaller == 0:
        return False
    # set.intersection walks the array in C: cheap even for large postings.
    return len(probe.intersection(other)) > smaller * _SAME_POSTING_SHARE


def same_posting(left, right):
    """Whether two fingerprints share more than 1% of the smaller article set."""
    if not left or not right:
        return False
    return _shares_posting(set(left), len(left), right)


class FleetDedup:
    """Listings and postings already covered by one fleet (the pick included).

    ``spool_base`` is the directory the fleet's NzbSpool folders go under
    (resolved on the resolve thread; None = the system temp directory).
    """

    def __init__(self, pick=None, spool_base=None):
        self._listings = [pick] if isinstance(pick, dict) else []
        self._fingerprints = []
        self.spool_base = spool_base

    def clusters(self, candidates):
        """Group ``candidates`` into same-listing clusters, in rank order.

        A candidate that lists an already-known posting (the pick, or a member
        of an earlier cluster from this fleet) is dropped. Each returned cluster
        is a list whose head is its best-ranked listing; the rest are fallbacks
        for a failed grab. Every member is remembered as known.
        """
        clusters = []
        for candidate in candidates:
            if any(same_listing(candidate, known) for known in self._listings):
                continue
            home = None
            for cluster in clusters:
                if same_listing(candidate, cluster[0]):
                    home = cluster
                    break
            if home is None:
                clusters.append([candidate])
            else:
                home.append(candidate)
        for cluster in clusters:
            self._listings.extend(cluster)
        return clusters

    def known_posting(self, fingerprint):
        """Whether ``fingerprint`` is the same posting as one already submitted."""
        if not fingerprint or not self._fingerprints:
            return False
        probe = set(fingerprint)
        return any(
            _shares_posting(probe, len(fingerprint), known)
            for known in self._fingerprints
        )

    def remember_posting(self, fingerprint):
        if fingerprint:
            self._fingerprints.append(fingerprint)


class NzbSpool:
    """Unique NZB bodies held on disk until every one has been sent to NZBGet.

    One private ``nzbdav-fleet-*`` folder per batch, under ``base_dir`` (Kodi's
    temp folder) or the system temp directory. A body that cannot be written
    (no folder, disk full) is kept in memory instead, so a spool failure never
    drops a backup. ``close`` deletes the folder and everything in it.
    """

    def __init__(self, base_dir=None):
        self._dir = None
        self._count = 0
        for parent in (base_dir, None):
            try:
                self._dir = tempfile.mkdtemp(prefix="nzbdav-fleet-", dir=parent)
                break
            except (OSError, TypeError, ValueError):
                continue

    def save(self, body):
        """Store ``body``; returns a handle for ``load`` (a path, or the bytes)."""
        if self._dir is None:
            return body
        self._count += 1
        path = os.path.join(self._dir, "{:05d}.nzb".format(self._count))
        try:
            with open(path, "wb") as handle:
                handle.write(body)
        except OSError:
            return body
        return path

    @staticmethod
    def load(handle):
        """The stored body, or None when it can no longer be read."""
        if handle is None or isinstance(handle, (bytes, bytearray)):
            return handle
        try:
            with open(handle, "rb") as stored:
                return stored.read()
        except OSError:
            return None

    def close(self):
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None


def _stopped(events):
    return any(event is not None and event.is_set() for event in events)


def _fetch_cluster(cluster, fetch, stop_events):
    """Fetch the first listing of ``cluster`` that returns a valid NZB.

    Returns ``(member, body, fingerprint)``. A body counts only when it parses
    as an NZB with article Message-IDs, so an HTTP-200 login or rate-limit page
    falls through to the next listing. ``body`` and ``fingerprint`` are None
    when every listing failed (the head is returned so the caller can still try
    a plain append). Stops between listings once any of ``stop_events`` fires.
    """
    for member in cluster:
        if _stopped(stop_events):
            break
        body = _try_fetch(fetch, member["link"])
        fingerprint = posting_fingerprint(body) if body else None
        if fingerprint:
            return member, body, fingerprint
    return cluster[0], None, None


def _try_fetch(fetch, url):
    """One listing's NZB body, or None (logged) when the indexer fetch fails."""
    try:
        return fetch(url)
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup NZB fetch failed, "
            "trying the next listing: {}".format(redact_text(str(exc))),
            xbmc.LOGDEBUG,
        )
        return None


class _Fetch:
    """One cluster's fetch, run on its own daemon thread or inline.

    A daemon thread per fetch (never a ``ThreadPoolExecutor``, whose workers
    are non-daemon and joined at interpreter exit) keeps an in-flight indexer
    request from delaying Kodi shutdown. When a thread cannot start (thread
    exhaustion on a small box) the fetch runs inline instead: no work item is
    ever queued anywhere, so it can never run twice.
    """

    def __init__(self, cluster, fetch, stop_events):
        self._args = (cluster, fetch, stop_events)
        self._done = threading.Event()
        self._value = (cluster[0], None, None)

    def start(self):
        try:
            threading.Thread(
                target=self._run, name="nzbdav-nzbget-prefetch", daemon=True
            ).start()
        except Exception:  # pylint: disable=broad-except
            self._run()
        return self

    def _run(self):
        try:
            self._value = _fetch_cluster(*self._args)
        finally:
            self._done.set()

    def result(self):
        self._done.wait()
        return self._value


def prefetched_clusters(clusters, fetch, cancel_event=None, window=PREFETCH_WINDOW):
    """Yield ``(member, body, fingerprint)`` per cluster in order, ``window`` ahead.

    Fetches overlap on daemon threads while results are consumed strictly in
    rank order (DupeScores stay rank-ordered) and at most ``window`` bodies are
    held in memory at once. Stops early once ``cancel_event`` fires. Closing
    the generator (a cancel, or the caller's cap being met) stops every
    in-flight fetch from moving on to its cluster's next listing and starts no
    new ones.
    """
    window = max(1, window)
    stop = threading.Event()
    stop_events = (cancel_event, stop)
    pending = collections.deque()
    remaining = iter(clusters)

    def _submit_next():
        cluster = next(remaining, None)
        if cluster is None:
            return False
        pending.append(_Fetch(cluster, fetch, stop_events).start())
        return True

    try:
        while len(pending) < window and _submit_next():
            pass
        while pending:
            item = pending.popleft().result()
            if _stopped(stop_events):
                return
            yield item
            # Refill only once the caller asks for more: a caller that stops
            # here (cap met) never pays for another grab, and at most
            # ``window`` bodies (the yielded one included) are ever held.
            _submit_next()
    finally:
        stop.set()
