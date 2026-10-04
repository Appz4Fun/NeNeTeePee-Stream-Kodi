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
import zlib
from concurrent.futures import ThreadPoolExecutor

import xbmc

from resources.lib.http_util import pubdate_to_epoch, redact_text

SAME_LISTING_WINDOW_SECONDS = 120
PREFETCH_WINDOW = 4

# Fingerprints keep every article hash below this count, else a deterministic
# 1-in-_SAMPLE_MOD sample (the same Message-ID always hashes the same way, so
# two samples of one posting overlap exactly as the full sets would).
_FULL_FINGERPRINT_BELOW = 512
_SAMPLE_MOD = 16
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
            and _variant_signature(pick.get("title"))
            == _variant_signature(row.get("title"))
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
    """Return ``(article_count, frozenset_of_crc32)`` for an NZB, or None.

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
    count = len(hashes)
    if count >= _FULL_FINGERPRINT_BELOW:
        hashes = {value for value in hashes if value % _SAMPLE_MOD == 0}
    return count, frozenset(hashes)


def _sampled(hashes):
    return frozenset(value for value in hashes if value % _SAMPLE_MOD == 0)


def same_posting(left, right):
    """Whether two fingerprints share more than 1% of the smaller article set."""
    if not left or not right:
        return False
    left_count, left_hashes = left
    right_count, right_hashes = right
    if left_count >= _FULL_FINGERPRINT_BELOW or right_count >= _FULL_FINGERPRINT_BELOW:
        # At least one side is a sample: compare like with like.
        left_hashes = _sampled(left_hashes)
        right_hashes = _sampled(right_hashes)
    smaller = min(len(left_hashes), len(right_hashes))
    if smaller == 0:
        return False
    return len(left_hashes & right_hashes) > smaller * _SAME_POSTING_SHARE


class FleetDedup:
    """Listings and postings already covered by one fleet (the pick included)."""

    def __init__(self, pick=None):
        self._listings = [pick] if isinstance(pick, dict) else []
        self._fingerprints = []

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
        return any(same_posting(fingerprint, known) for known in self._fingerprints)

    def remember_posting(self, fingerprint):
        if fingerprint:
            self._fingerprints.append(fingerprint)


def _fetch_cluster(cluster, fetch, cancel_event):
    """Fetch the first listing of ``cluster`` that downloads.

    Returns ``(member, body)``; ``body`` is None when every listing failed (the
    head is returned so the caller can still try a plain append).
    """
    for member in cluster:
        if cancel_event is not None and cancel_event.is_set():
            break
        body = _try_fetch(fetch, member["link"])
        if body:
            return member, body
    return cluster[0], None


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


class _Done:  # pylint: disable=too-few-public-methods
    """A finished fetch, shaped like the ``Future`` it stands in for."""

    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _start_fetch(pool, cluster, fetch, cancel_event):
    """Start fetching ``cluster`` on the pool, or fetch it inline.

    ``pool`` is a one-item list holding the executor (or None). Inline is the
    fallback when the pool cannot start a thread (thread exhaustion on a small
    box), so prefetching degrades to sequential fetches instead of failing the
    fleet. The pool is dropped after the first failure: CPython's ``submit``
    queues the work item before starting a thread, so retrying it could run a
    cluster twice (a duplicate indexer grab).
    """
    if pool[0] is not None:
        try:
            return pool[0].submit(_fetch_cluster, cluster, fetch, cancel_event)
        except Exception:  # pylint: disable=broad-except
            pool[0].shutdown(wait=False)
            pool[0] = None
    return _Done(_fetch_cluster(cluster, fetch, cancel_event))


def prefetched_clusters(clusters, fetch, cancel_event=None, window=PREFETCH_WINDOW):
    """Yield ``(member, body)`` per cluster in order, fetching ``window`` ahead.

    Fetches run on a small thread pool so indexer round-trips overlap, while
    results are consumed strictly in rank order (DupeScores stay rank-ordered)
    and at most ``window`` bodies are held in memory at once. Stops early once
    ``cancel_event`` fires.
    """
    window = max(1, window)
    pending = collections.deque()
    remaining = iter(clusters)
    try:
        pool = [
            ThreadPoolExecutor(
                max_workers=window, thread_name_prefix="nzbdav-nzbget-prefetch"
            )
        ]
    except Exception:  # pylint: disable=broad-except
        pool = [None]

    def _submit_next():
        cluster = next(remaining, None)
        if cluster is None:
            return False
        pending.append(_start_fetch(pool, cluster, fetch, cancel_event))
        return True

    try:
        while len(pending) < window and _submit_next():
            pass
        while pending:
            member, body = pending.popleft().result()
            if cancel_event is not None and cancel_event.is_set():
                return
            _submit_next()
            yield member, body
    finally:
        if pool[0] is not None:
            pool[0].shutdown(wait=False)
