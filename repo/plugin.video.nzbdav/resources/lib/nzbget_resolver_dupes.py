# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
# pylint: disable=cyclic-import

"""NZBGet Smart-Duplicates fleet engine: dedupe, spool, send, widen, rescue (#372).

Cohesive helper group split out of ``nzbget_resolver`` to keep every module
under Codacy's 500-NLOC file gate (same split idiom as
``resolver_fallback_jobs``). References to names that live in (or are patched
via) ``nzbget_resolver`` -- including the sibling helpers themselves, which the
suite patches as ``resources.lib.nzbget_resolver.<name>`` -- are resolved at
call time through ``import resources.lib.nzbget_resolver as _core`` so those
``@patch`` decorators keep intercepting, with no top-level import cycle. Every
moved name is re-exported from ``nzbget_resolver``.
"""

import threading

import resources.lib.nzbget_resolver as _core  # noqa: F401  pylint: disable=unused-import
from resources.lib.nzbget_fleet_dedup import (  # noqa: F401
    PREFETCH_WINDOW,
    FleetDedup,
    NzbSpool,
    call_abortable,
    fetch_cluster_abortable,
    posting_fingerprint_file,
    prefetched_clusters,
    same_variant,
)


def _submit_candidates(
    candidates,
    dupe_key,
    settings_getter,
    cancel_event=None,
    submitted_sink=None,
    dedup=None,
    veto_probe=True,
    limits=(None, None),
):
    """Download and dedupe candidates first, then send the unique ones.

    ``candidates`` is the whole fleet in rank order: the pick first (flagged
    ``_is_pick``, top DupeScore), then its backups. Each round has two phases,
    in rank order:

    1. **Collect.** Same-listing candidates collapse into one fetch (the
       others are fallbacks for a failed grab); NZB bodies are fetched
       ``PREFETCH_WINDOW`` at a time in parallel, and each is compared with the
       pick and every NZB already kept. A unique body is saved to an
       ``NzbSpool`` folder on disk as soon as it is known to be unique; a
       same-posting body is dropped. A backup whose every listing failed (or
       whose body can't be stored) is dropped; the send phase never makes
       ``append_nzb`` fetch on the resolve thread.
    2. **Send.** Every kept NZB is appended to NZBGet, best first. A failed
       PICK append stops the batch (the resolve reports it); each appended
       row records its ``_nzbid`` and a failed one its ``_append_error``.

    ``dedup.progress`` (optional) is told ``("download", done, total)`` after
    each listing cluster is handled, ``("send", sent, count)`` before each
    append, and ``("wait", None, None)`` while a slow fetch is pending, for the
    resolve's progress dialog (and its cancel/shutdown checks).

    Unlimited (``live_limit`` None): one round collects EVERY candidate, then
    sends them all. Capped: a round collects only the slots still open, and a
    failed or ``DELETED/COPY``-vetoed append opens a slot for the next round,
    so replacements are fetched only after an actual failure. The spool
    folder is deleted only after the last round was sent (or the batch
    stopped on a cancel or a met cap).

    ``limits`` is ``(live_limit, max_attempts)``: stops once ``live_limit``
    LIVE backups landed, ``max_attempts`` appends were tried, or
    ``cancel_event`` fires (``None`` limits are unbounded). Returns the LIVE
    NZBIDs.
    """
    live_limit, max_attempts = limits
    if dedup is None:
        dedup = FleetDedup()
    seen = set()
    usable = []
    for candidate in candidates or []:
        link = _core._usable_backup_link(candidate, seen)
        if link:
            seen.add(link)
            usable.append(candidate)
    clusters = dedup.clusters(usable)
    tally = {
        "live": [],
        "attempts": 0,
        "wanted": None,
        "seen": 0,
        "total": len(clusters),
        # The pick never counts against the backup cap: it needs one extra
        # collect slot until it is in hand, and its append is not a backup.
        "pick_pending": bool(usable and usable[0].get("_is_pick")),
    }
    spool = NzbSpool(dedup.spool_base)
    fetch = _fleet_fetcher(clusters, spool)
    stream = prefetched_clusters(
        clusters,
        fetch,
        cancel_event,
        window=PREFETCH_WINDOW,
        # Never fetch past what the current round can still use.
        demand=lambda: tally["wanted"],
        # A stalled indexer must not hide a dialog cancel or Kodi shutdown.
        on_wait=lambda: _report(dedup, "wait", None, None),
    )
    try:
        while True:
            need = _open_slots(tally, limits)
            # The pick is never bounded by the backup limits (it may be 0 when
            # adopted backups already fill the cap): only a filled-up round
            # WITHOUT the pick pending stops here -- a cancel always does.
            if cancel_event is not None and cancel_event.is_set():
                break
            if need == 0 or (
                not tally["pick_pending"]
                and _fill_done(
                    tally["live"],
                    live_limit,
                    tally["attempts"],
                    max_attempts,
                    cancel_event,
                )
            ):
                break
            kept = _collect_unique(
                stream, (dedup, spool, tally, fetch), cancel_event, need
            )
            if not kept or (cancel_event is not None and cancel_event.is_set()):
                break
            _send_kept(
                kept,
                dupe_key,
                settings_getter,
                (cancel_event, submitted_sink, veto_probe, dedup),
                (limits, tally),
            )
            # The whole round was sent: free its spool files and memory so a
            # capped fleet's replacement rounds have room.
            for _candidate, handle, _token in kept:
                spool.release(handle)
            dedup.end_round()
            if need is None or tally.get("pick_failed"):
                break  # unlimited: everything was collected and sent
    finally:
        # The fleet-wide append budget: later loader batches continue it.
        dedup.attempts_used = getattr(dedup, "attempts_used", 0) + tally["attempts"]
        stream.close()
        spool.close()
        # Every inspected row is now decided: the completion ledger records
        # only rows that really reached NZBGet.
        for candidate in usable:
            candidate.setdefault("_submitted", False)
    return tally["live"]


# Per-BACKUP NZB ceiling, spooled or in memory. Spooling bounds the download,
# but each append still loads the body whole and builds its base64 and JSON
# request copies (roughly 5x the body at peak), so a backup is capped well
# below the nzbget_api ceiling to keep that peak safe on a CoreELEC box; 32
# MiB still covers a 100+ GB release. The pick keeps the full ceiling.
_FLEET_NZB_MAX_BYTES = 32 * 1024 * 1024


def _fleet_fetcher(clusters, spool):
    """The fleet's NZB fetch.

    Each BACKUP streams straight into a spool file and is fingerprinted from
    disk (``posting_fingerprint_file``): one GET, no body in memory, capped at
    ``_FLEET_NZB_MAX_BYTES`` (its append loads it whole). Returns
    ``(path, fingerprint)``, or None for a response that isn't an NZB (its
    file is removed). Without a spool folder
    backups fall back to in-memory bytes, capped. The pick (a single fetch)
    is always fetched into memory at the full ceiling, so a full temp disk
    can't fail it.
    """
    pick_links = {
        row.get("link")
        for cluster in clusters[:1]
        if cluster and cluster[0].get("_is_pick")
        for row in cluster
    }

    def _fetch(url):
        if url in pick_links:
            # The pick is ONE fetch (memory bounded by the full ceiling) and
            # must not fail just because the temp disk is full: fetch it into
            # memory; ``NzbSpool.save(required=True)`` then keeps it either way.
            return _core.nzbget_api.fetch_nzb_bytes(url)
        path = spool.reserve()
        if path is None:
            return _core.nzbget_api.fetch_nzb_bytes(url, max_bytes=_FLEET_NZB_MAX_BYTES)
        _core.nzbget_api.download_nzb(url, path, max_bytes=_FLEET_NZB_MAX_BYTES)
        fingerprint = posting_fingerprint_file(path)
        if not fingerprint:
            spool.release(path)
            return None
        return path, fingerprint

    return _fetch


def _open_slots(tally, limits):
    """Unique NZBs the next round should collect (None = all of them)."""
    live_limit, max_attempts = limits
    if live_limit is None:
        return None
    need = max(0, live_limit - len(tally["live"]))
    if max_attempts is not None:
        need = min(need, max(0, max_attempts - tally["attempts"]))
    return need + 1 if tally.get("pick_pending") else need


def _collect_unique(stream, state, cancel_event, need):
    """Phase 1: pull from ``stream`` until ``need`` unique NZBs are spooled.

    ``state`` is ``(dedup, spool, tally, fetch)``; ``tally["wanted"]`` tells the
    stream how many more items this round can use. Returns
    ``(candidate, handle, token)`` entries: an ``NzbSpool`` handle and the
    ``FleetDedup.remember_posting`` token (never the fingerprint); a backup
    without a stored NZB is dropped, and a pick without one is kept with a
    None handle so the send phase reports its failure. ``need`` None collects
    everything the stream has.
    """
    dedup, spool, tally, fetch = state
    kept = []
    tally["wanted"] = need
    for candidate, payload, fingerprint in stream:
        tally["seen"] += 1
        _report(dedup, "download", tally["seen"], tally["total"])
        if cancel_event is not None and cancel_event.is_set():
            _discard(spool, payload)
            break
        is_pick = bool(candidate.get("_is_pick"))
        if payload is None and is_pick:
            # The pick seeds every later dedup decision: one more (abortable)
            # try at its own URL before giving up.
            _head, payload, fingerprint = fetch_cluster_abortable(
                [candidate],
                fetch,
                (cancel_event,),
                lambda: _report(dedup, "wait", None, None),
            )
        if is_pick:
            tally["pick_pending"] = False
            if not payload:
                # No pick, no fleet: stop downloading backups that could
                # never be sent (each would cost an indexer grab).
                kept.append((candidate, None, None))
                break
        if fingerprint and dedup.known_posting(fingerprint):
            _discard(spool, payload)
            _core.xbmc.log(
                "NeNeTeePee-Stream-Kodi: Skipped NZBGet duplicate backup '{}' "
                "(same Usenet posting as one already kept)".format(
                    candidate.get("title") or ""
                ),
                _core.xbmc.LOGINFO,
            )
            continue
        handle = _keep_handle(spool, payload, is_pick)
        if handle is None and not is_pick:
            # No NZB in hand: sending it would make append_nzb fetch it on the
            # resolve thread (uncancelable, a second grab). Drop this backup.
            _core.xbmc.log(
                "NeNeTeePee-Stream-Kodi: Dropped NZBGet duplicate backup '{}' "
                "(its NZB could not be downloaded or stored)".format(
                    candidate.get("title") or ""
                ),
                _core.xbmc.LOGINFO,
            )
            continue
        # Unique: held for this round so later downloads compare against it;
        # committed once its append reaches NZBGet. The round keeps only the
        # token: a fingerprint spilled to disk must not stay alive here.
        token = dedup.remember_posting(fingerprint)
        del fingerprint
        kept.append((candidate, handle, token))
        if need is not None:
            tally["wanted"] = need - len(kept)
            if len(kept) >= need:
                break
    return kept


def _discard(spool, payload):
    """Delete a fetched-but-unwanted spool file (bytes need no cleanup)."""
    if isinstance(payload, str):
        spool.release(payload)


def _keep_handle(spool, payload, is_pick):
    """The kept handle: a spooled path as-is, or bytes stored via the spool."""
    if isinstance(payload, str) or not payload:
        return payload or None
    return spool.save(payload, required=is_pick)


def _report(dedup, phase, done, total):
    """Forward a progress tick to ``dedup.progress`` when one is attached."""
    progress = getattr(dedup, "progress", None)
    if progress is not None:
        progress(phase, done, total)


def _send_kept(kept, dupe_key, settings_getter, run, budget):
    """Phase 2: append every kept NZB to NZBGet, best first.

    ``run`` is ``(cancel_event, submitted_sink, veto_probe, dedup)``;
    ``budget`` is ``(limits, tally)``, and ``tally`` (``live`` ids,
    ``attempts``) is updated in place. A sent NZB marks its listing and
    posting covered in ``dedup``, so later phases skip other copies of it.
    Each append (and its COPY-veto probe) runs behind the abortable wait, so a
    hung NZBGet RPC never blocks a cancel or Kodi shutdown;
    ``settings_getter`` must therefore be thread-safe (a settings snapshot).
    """
    cancel_event, submitted_sink, veto_probe, dedup = run
    (live_limit, max_attempts), tally = budget
    for index, (candidate, handle, token) in enumerate(kept):
        # One tick per append: moves the bar and re-checks the dialog cancel,
        # so a cancel stops the remaining appends.
        _report(dedup, "send", index, len(kept))
        is_pick = bool(candidate.get("_is_pick"))
        if (cancel_event is not None and cancel_event.is_set()) or (
            not is_pick
            and _fill_done(
                tally["live"], live_limit, tally["attempts"], max_attempts, cancel_event
            )
        ):
            break
        if not is_pick:
            tally["attempts"] += 1
        nzbid, vetoed = _append_abortably(
            candidate,
            NzbSpool.load(handle),
            (dupe_key, settings_getter, veto_probe),
            (cancel_event, dedup),
        )
        if not nzbid:
            if is_pick:
                # No pick, no fleet: the resolve fails with NZBGet's error.
                tally["pick_failed"] = True
                return
            continue
        candidate["_nzbid"] = nzbid
        if is_pick:
            # For the poll's FORCE rescue: parked on disk NOW, so its bytes
            # are never held while the (possibly huge) backups load and send.
            # Re-check cancel/shutdown first: a stopped resolve never rescues,
            # so it must not wait on a 100 MiB write to slow storage.
            _report(dedup, "wait", None, None)
            if cancel_event is None or not cancel_event.is_set():
                _park_pick(candidate, handle)
        dedup.remember_listing(candidate)
        dedup.commit_posting(token)
        # Sink FIRST (round-5 invariant): a cancel mid-batch must be able to
        # delete this id immediately, and a COPY-vetoed row still needs deleting.
        if submitted_sink is not None:
            submitted_sink.append(nzbid)
        # The completion ledger records only rows NZBGet really kept, under
        # their own titles; a vetoed row never downloads, and it frees its
        # slot for the next round (#372 r6). Only backups count against the
        # cap; the pick's own COPY veto is handled by the poll's FORCE rescue.
        candidate["_submitted"] = not vetoed
        if not vetoed and not is_pick:
            tally["live"].append(nzbid)


def _park_pick(candidate, handle):
    """Park the appended pick's NZB on disk (``_body_path``) for the rescue.

    Only when no temp folder takes it does it stay in memory (``_body``), the
    only good copy.
    """
    from resources.lib.nzbget_fleet_run import _park_pick_body

    body = NzbSpool.load(handle)
    path = _park_pick_body(body)
    if path is None:
        candidate["_body"] = body
    else:
        candidate["_body_path"] = path


def _append_abortably(candidate, body, send, stops):
    """One append (+ COPY-veto probe) behind the abortable wait.

    ``send`` is ``(dupe_key, settings_getter, veto_probe)``; ``stops`` is
    ``(cancel_event, dedup)``. Returns ``(nzbid, vetoed)``; ``(None, False)``
    on failure or when the wait was abandoned. An append that lands AFTER a
    user cancel abandoned the wait is deleted from NZBGet on the worker thread
    (it never reached ``submitted_nzbids``); on a Kodi shutdown it is left to
    finish, like the poll's ``aborted`` path.
    """
    dupe_key, settings_getter, veto_probe = send
    cancel_event, dedup = stops
    if not body:
        # Every fleet append carries its body: append_nzb must never fetch on
        # the resolve thread. A pick without one fails the resolve.
        candidate["_append_error"] = "NZB download failed"
        return None, False

    def _append():
        nzbid = _core._append_one_backup(
            candidate["link"], candidate, dupe_key, settings_getter, nzb_bytes=body
        )
        if nzbid and cancel_event is not None and cancel_event.is_set():
            # Canceled while the append was in flight: hand the id straight to
            # the late cleanup -- no veto probe (another RPC) first.
            return nzbid, False
        vetoed = bool(nzbid) and bool(
            veto_probe and _core._copy_vetoed_after_append(nzbid, settings_getter)
        )
        return nzbid, vetoed

    def _late(result):
        if not (result and result[0]):
            return
        # Recorded FIRST: until NZBGet confirms the delete (and after a Kodi
        # shutdown, which leaves it to finish), the job is in NZBGet, and a
        # replay must recognize it instead of sending it again.
        _core.nzbget_submit_ledger.record([dict(candidate, _nzbid=result[0])], dupe_key)
        aborted = getattr(dedup, "aborted", None)
        if aborted is not None and aborted():
            return
        if _core.nzbget_api.cancel_jobs([result[0]], settings_getter=settings_getter):
            _core.nzbget_submit_ledger.forget([result[0]])

    return call_abortable(
        _append,
        (cancel_event,),
        lambda: _report(dedup, "wait", None, None),
        default=(None, False),
        on_late=_late,
    )


def _fill_done(live, live_limit, attempts, max_attempts, cancel_event):
    """Stop conditions for ``_submit_candidates`` (``None`` limits are unbounded)."""
    if live_limit is not None and len(live) >= live_limit:
        return True
    if max_attempts is not None and attempts >= max_attempts:
        return True
    return bool(cancel_event is not None and cancel_event.is_set())


def _usable_backup_link(candidate, seen):
    """The candidate's usable NZB link, or None to skip the row.

    Shared filter for the picker's same-name backups and the loader-widened
    extras: skip non-dict rows (defensive -- both lists are best-effort inputs)
    and links already accepted this pass or already submitted (``seen``), so one
    URL is never appended to NZBGet twice under the same DupeKey.
    """
    if not isinstance(candidate, dict):
        return None
    link = candidate.get("link")
    if not link or link in seen:
        return None
    return link


def _append_one_backup(nzb_url, backup, dupe_key, settings_getter, nzb_bytes=None):
    """Append one duplicate backup to NZBGet and log the outcome (#372).

    Returns the new NZBID, or None on a failed/raised append -- the caller keeps
    iterating either way (best-effort: one bad backup never aborts the rest).

    Submitted under the backup's OWN (undecorated) release title: a promoted
    backup that completes becomes the SUCCESS history row the next picker
    render matches by EXACT name (``completed_history`` ->
    ``_tag_available_nzbget``), so the DL tag and selection-time reuse keep
    working -- a decorated ``[fallback-...]`` name would hide it and, with the
    wall-clock score base, a replay would re-download despite the files
    existing. Uniqueness is not needed: dupe grouping is DupeKey-driven and
    NZBGet keys jobs by NZBID. ``nzb_bytes`` is an already-fetched body (the
    append then skips its own fetch).
    """
    score = int(backup.get("score") or 0)
    job_name = backup.get("title") or dupe_key
    # SCORE parks lower-scored same-key items as backups; the fleet asks for
    # FORCE only for a lone pick whose score could not be checked.
    dupe_mode = backup.get("_dupe_mode") or "SCORE"
    extra = {} if nzb_bytes is None else {"nzb_bytes": nzb_bytes}
    try:
        nzbid, error = _core.nzbget_api.append_nzb(
            nzb_url,
            job_name,
            settings_getter=settings_getter,
            dupe_key=dupe_key,
            dupe_score=score,
            dupe_mode=dupe_mode,
            **extra,
        )
    except Exception as exc:  # pylint: disable=broad-except
        backup["_append_error"] = _core._redact_text(str(exc))
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup submit raised: {}".format(
                backup["_append_error"]
            ),
            _core.xbmc.LOGWARNING,
        )
        return None
    if nzbid:
        _core.xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: Queued NZBGet duplicate backup "
                "'{}' (score {})"
            ).format(job_name, score),
            _core.xbmc.LOGINFO,
        )
        return nzbid
    backup["_append_error"] = error
    _core.xbmc.log(
        ("NeNeTeePee-Stream-Kodi: NZBGet duplicate backup submit failed: {}").format(
            error
        ),
        _core.xbmc.LOGINFO,
    )
    return None


# Warn about HealthCheck=Pause at most once per Kodi session (a list so the
# module-level flag is mutable from the worker thread; a lock so two concurrent
# resolves' background threads can't both slip through the check-then-set).
_HEALTHCHECK_WARNED = [False]
_HEALTHCHECK_LOCK = threading.Lock()


def _warn_if_healthcheck_pauses(settings_getter, options=None):
    """Warn if NZBGet's ``HealthCheck=Pause`` disables automatic dup failover.

    Per nzbget.com/documentation/rss/#duplicates automatic duplicate failover
    needs HealthCheck = Delete, None (or Park); with Pause NZBGet pauses a failed
    download instead of promoting a backup, so the picked release's backups sit
    idle until the user unpauses one. Best-effort -- an unreadable config is
    skipped. Always logs; notifies the user at most once per Kodi session.
    ``options`` is an already-read ``config_options`` dict (else one RPC).
    """
    try:
        value = (
            options.get("healthcheck")
            if options is not None
            else _core.nzbget_api.config_option(
                "HealthCheck", settings_getter=settings_getter
            )
        )
    except Exception:  # pylint: disable=broad-except
        return
    if value != "pause":
        return
    _core.xbmc.log(
        (
            "NeNeTeePee-Stream-Kodi: NZBGet HealthCheck=Pause "
            "disables automatic duplicate failover; set it to "
            "Delete or None to enable it (#372)."
        ),
        _core.xbmc.LOGWARNING,
    )
    with _HEALTHCHECK_LOCK:
        if _HEALTHCHECK_WARNED[0]:
            return
        _HEALTHCHECK_WARNED[0] = True
    _core._notify(_core._addon_name(), _core._string(30230), 6000)


def _dupe_check_disabled(settings_getter, options=None):
    """True only when NZBGet's ``DupeCheck`` option is explicitly ``no``.

    With DupeCheck off NZBGet does not park same-key items as backups -- it would
    download every one as a normal queue item (parallel full downloads). FAIL
    CLOSED: an unreadable config counts as off, so a transient RPC failure can
    never launch an (unlimited) fleet of full parallel downloads -- the fleet
    then sends the pick alone. ``options`` is an already-read
    ``config_options`` dict (else one RPC).
    """
    if options is not None:
        return options.get("dupecheck", "no") == "no"
    try:
        value = _core.nzbget_api.config_option(
            "DupeCheck", settings_getter=settings_getter
        )
    except Exception:  # pylint: disable=broad-except
        return True
    return value is None or value == "no"


_MAX_EXTRA_BACKUPS = 5

# Hard bound on the extra append ATTEMPTS spent replacing COPY-vetoed candidates
# (#372 r6), so a pathological all-vetoed loader pool can't grind the worker (and
# the is_submitting-extended failover grace) for minutes.
_MAX_VETO_REPLACEMENTS = 5


# The candidate fields a fleet row keeps: its job name plus the listing
# evidence (size, post date) the same-listing dedup reads.
_FLEET_ROW_KEYS = ("title", "size", "pubdate", "_posted_epoch")


def _extra_backups_from_loader(  # pylint: disable=too-many-arguments
    loader,
    seen_links,
    limit=_MAX_EXTRA_BACKUPS,
    score_base=0,
    reserve=0,
    leading=None,
    pick=None,
):
    """Hydra duplicate uploads plus same-content candidates from the fallback loader.

    #372 round 2 widening: beyond the picker's same-release rows, NZBHydra's
    duplicate uploads (``leading``, the pick's exact title with
    single-result-per-group off) and the fallback loader (same-content mirrors)
    surface the uploads that were collapsed into a single picker row. Returns a
    list of dicts with keys ``link``, ``title``, and ``score``, deduped against
    ``seen_links``, ``leading`` first, scored DESCENDING from ``score_base`` (the
    fleet's wall-clock base) so they OUTRANK any prior same-key success while
    sitting BELOW every same-release backup (a last-resort failover, keyed under
    the pick's DupeKey). Bounded by ``limit`` (the standby cap's remaining
    slots; ``None`` = unlimited). ``reserve`` widens only the CANDIDATE LIST
    (to ``limit + reserve`` when ``limit > 0``), not the live-submit cap: the
    caller's veto-aware fill loop draws extra replacements from this headroom
    when a candidate is ``DELETED/COPY``-vetoed (#372 r6). The loader runs only
    when the leading uploads leave room. With a ``pick``, a candidate whose 3D,
    dub/sub, hardsub, cut, or language tags differ from the pick's is dropped:
    the loader's same-content gate leaves them open for the byte-verifying
    stream proxy, but an NZBGet failover would play them. Best-effort: a
    missing/erroring loader, its turned-off sentinel (a non-list), or
    ``limit <= 0`` yields no loader candidates.
    """
    if limit is not None and limit <= 0:
        return []
    list_cap = None if limit is None else limit + reserve
    extras = []
    seen = set(seen_links or [])
    score = score_base

    def _take(candidates):
        nonlocal score
        for candidate in candidates:
            if list_cap is not None and len(extras) >= list_cap:
                return
            link = _core._usable_backup_link(candidate, seen)
            if not link:
                continue
            if pick and not same_variant(pick, candidate):
                continue
            seen.add(link)
            row = {key: candidate[key] for key in _FLEET_ROW_KEYS if key in candidate}
            row.update(link=link, score=score)
            extras.append(row)
            score -= 1

    _take(leading or [])
    if loader is not None and (list_cap is None or len(extras) < list_cap):
        _take(_core._load_extra_candidates(loader))
    return extras


def _load_extra_candidates(loader):
    """Run the fallback loader, absorbing every failure mode (#372 r2).

    Returns the candidate list, or ``[]`` for an erroring loader or its
    turned-off sentinel (a non-list) -- the extras are best-effort widening
    only, so a broken indexer search must never surface past here.
    """
    try:
        candidates = loader()
    except Exception:  # pylint: disable=broad-except
        return []
    return candidates if isinstance(candidates, list) else []


def _hydra_uploads_for_fleet(dupe):
    """NZBHydra duplicate uploads for the pick, absorbing every failure (#372)."""
    loader = dupe.get("hydra_uploads")
    if loader is None:
        return []
    return _core._load_extra_candidates(loader)


def _fleet_spool_base():
    """Kodi's ``special://temp`` folder for the fleet's NZB spool, or None.

    None (the system temp directory) when Kodi can't translate the path.
    Call on the resolve thread only.
    """
    try:
        import xbmcvfs

        path = xbmcvfs.translatePath("special://temp/")
    except Exception:  # pylint: disable=broad-except
        return None
    return path if isinstance(path, str) and path else None


# ---------------------------------------------------------------------------
# #372 round 6: recover from NZBGet's content-fingerprint DELETED/COPY veto.
#
# NZBGet has its OWN content-fingerprint duplicate check (separate from the
# DupeKey/DupeScore fleet handling) that silently vetoes ANY re-submission of
# content it has seen before -- the item lands straight in history as
# ``DELETED/COPY`` with zero bytes, never entering the queue, so the existing
# promotion machinery (which only sees actively queued siblings) is blind to it.
# The append RPC's DupeMode=FORCE overrides this check, so a confirmed dead end
# (pick died DELETED/COPY, group otherwise exhausted) is recovered by re-appending
# the pick once with FORCE. Reactive, not preemptive: FORCE from the start would
# defeat every legitimate dupe protection NZBGet provides.
# ---------------------------------------------------------------------------


def _is_copy_veto_status(status):
    """Exact-match predicate for NZBGet's content-fingerprint COPY veto (#372 r6).

    Only the exact (uppercase-normalized) ``DELETED/COPY`` counts. Anything else
    -- an RPC-error empty string, ``DELETED/DUPE``, ``DELETED/MANUAL``, or a
    hypothetical future status format -- returns False, degrading to today's
    behavior rather than triggering a wrong rescue.
    """
    return str(status or "").strip().upper() == "DELETED/COPY"


def _is_copy_failure(poll_result):
    """True when a terminal poll status is COPY-shaped (#372 r6, message select).

    Matches both the synthetic ``FAILURE/COPY`` (fleet path) and the raw
    ``DELETED/COPY`` passthrough (plain path) so the honest "already in history,
    re-queue failed" message is shown only when the veto is what actually
    stopped playback.
    """
    return (
        str((poll_result or {}).get("status", "") or "")
        .strip()
        .upper()
        .endswith("/COPY")
    )


def _copy_vetoed_after_append(nzbid, settings_getter):
    """Whether ``nzbid`` was AFFIRMATIVELY COPY-vetoed, seen from the worker (#372 r6).

    Post-append history probe for the backup worker. Defensive by design: only a
    visible ``DELETED/COPY`` row counts as vetoed -- "not in history yet" (or any
    RPC error) counts as LIVE, so a misclassification can only degrade to today's
    behavior, never drop a good backup. Must be called with the worker's SNAPSHOT
    getter (no off-thread Kodi reads). Whole body fails safe to False.
    """
    try:
        hist = _core.nzbget_api.history_status(nzbid, settings_getter=settings_getter)
        if hist.get("present") and _is_copy_veto_status(hist.get("status")):
            _core.xbmc.log(
                "NeNeTeePee-Stream-Kodi: NZBGet content-vetoed duplicate backup {} "
                "(DELETED/COPY) -- backfilling its slot (#372).".format(nzbid),
                _core.xbmc.LOGINFO,
            )
            return True
        return False
    except Exception:  # pylint: disable=broad-except
        return False


def _pick_rescue_callable(ctx, nzb_url, title, pick_nzbid=None):
    """Build the resolve-thread closure that FORCE re-submits the vetoed pick (#372 r6).

    Returns a zero-arg callable the poll invokes ON THE RESOLVE THREAD (never the
    worker thread) so ``ctx.settings_getter`` off-thread reads are fine -- same as
    ``_submit_pick``. Before overriding NZBGet's veto, confirms no FOREIGN active
    download shares this exact release name (``active_group_by_name`` -- the
    plain submit path has no DupeKey to check via the fleet's own
    ``foreign_active``/``_promotion_still_pending`` guard, and a cross-DupeKey
    scheme could shadow the fleet path's check too); if one is present, the
    veto is shadowing a live download rather than a stale history-only one, so
    the rescue is skipped rather than racing a wasteful parallel download. It
    otherwise re-appends the pick's NZB once with ``DupeMode=FORCE`` (which
    overrides the content-fingerprint veto) under the same DupeKey and the
    pick's own DupeScore. On success the new NZBID is recorded into
    ``ctx.submitted_nzbids`` BEFORE returning -- so it counts as owned (failover
    tracking) and is covered by the cancel set -- and the id is returned. Any
    append error/exception, or the foreign-active skip, logs and returns None
    (the caller then reports the honest COPY failure).
    """

    def _rescue():
        # Runs behind the abortable wait (two RPCs + an indexer fetch can
        # each take the RPC timeout), on a settings snapshot taken here on the
        # resolve thread. An append that lands after a user cancel abandoned
        # the wait is deleted; after a Kodi shutdown it is left to finish.
        getter = _rescue_snapshot_getter(ctx.settings_getter)
        stop = {"aborted": False}
        return call_abortable(
            lambda: _rescue_now(getter),
            (getattr(ctx, "cancel_event", None),),
            lambda: _rescue_check_cancel(ctx, stop),
            default=RESCUE_ABANDONED,
            on_late=lambda nzbid: _rescue_late(nzbid, stop, getter),
        )

    def _rescue_now(getter):
        dupe = ctx.dupe or {}
        # The vetoed pick itself can linger in listgroups during NZBGet's
        # queue-to-history handoff: it is not a foreign download.
        if _core.nzbget_api.active_group_by_name(
            title, exclude_nzbid=pick_nzbid, settings_getter=getter
        ):
            _core.xbmc.log(
                (
                    "NeNeTeePee-Stream-Kodi: NZBGet FORCE rescue skipped -- "
                    "a foreign active download of this release is already "
                    "queued (#372 r6)."
                ),
                _core.xbmc.LOGINFO,
            )
            return None
        cancel_event = getattr(ctx, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            # Canceled/shutting down while the lookup ran: start no append.
            return None
        # Re-send the body the fleet already downloaded when there is one: the
        # pick URL may be dead (a mirror supplied it) or single-use.
        body = _read_parked_pick(getattr(ctx, "pick_nzb_path", None)) or getattr(
            ctx, "pick_nzb_bytes", None
        )
        extra = {"nzb_bytes": body} if body else {}
        try:
            nzbid, error = _core.nzbget_api.append_nzb(
                nzb_url,
                title,
                settings_getter=getter,
                dupe_key=dupe.get("key") or "",
                dupe_score=int(dupe.get("pick_score") or 0),
                dupe_mode="FORCE",
                **extra,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _core.xbmc.log(
                (
                    "NeNeTeePee-Stream-Kodi: NZBGet FORCE rescue re-submit "
                    "raised: {}"
                ).format(_core._redact_text(str(exc))),
                _core.xbmc.LOGWARNING,
            )
            return None
        if nzbid:
            # _SubmitCtx.__init__ always sets submitted_nzbids=[]; getattr here
            # only mirrors this module's existing defensive read pattern (see
            # _submit_poll_resolve's _owned_fleet_nzbids/_handle_poll_failure
            # call) for a ctx built some other way.
            if getattr(ctx, "submitted_nzbids", None) is None:
                ctx.submitted_nzbids = []
            ctx.submitted_nzbids.append(nzbid)
            _core.xbmc.log(
                (
                    "NeNeTeePee-Stream-Kodi: FORCE re-queued content-vetoed "
                    "pick as NZBID {} (#372 r6 rescue)."
                ).format(nzbid),
                _core.xbmc.LOGINFO,
            )
            return nzbid
        _core.xbmc.log(
            ("NeNeTeePee-Stream-Kodi: NZBGet FORCE rescue re-submit failed: {}").format(
                error
            ),
            _core.xbmc.LOGWARNING,
        )
        return None

    return _rescue


def _read_parked_pick(path):
    """The pick body ``submit_fleet`` parked on disk, or None (best-effort)."""
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


# ``_pick_rescue_callable``'s result when a cancel/shutdown abandoned the
# rescue: the poll keeps going so its own cancel/abort handling takes over.
RESCUE_ABANDONED = object()


def _rescue_snapshot_getter(settings_getter):
    """A thread-safe getter over the NZBGet connection settings, read now."""
    url, user, password, category = _core.nzbget_api._get_settings(settings_getter)
    snapshot = {
        "nzbget_url": url,
        "nzbget_username": user,
        "nzbget_password": password,
        "nzbget_category": category,
    }
    return lambda key, default="": snapshot.get(key, default)


def _rescue_check_cancel(ctx, stop):
    """Raise the resolve's cancel event on a dialog cancel or Kodi shutdown."""
    cancel_event = getattr(ctx, "cancel_event", None)
    if cancel_event is None:
        return
    dialog = getattr(ctx, "dialog", None)
    try:
        if dialog is not None and dialog.iscanceled() is True:
            cancel_event.set()
    except Exception as exc:  # pylint: disable=broad-except
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: FORCE rescue dialog check: {}".format(exc),
            _core.xbmc.LOGDEBUG,
        )
    try:
        if _core.xbmc.Monitor().abortRequested() is True:
            stop["aborted"] = True
            cancel_event.set()
    except Exception as exc:  # pylint: disable=broad-except
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: FORCE rescue abort check: {}".format(exc),
            _core.xbmc.LOGDEBUG,
        )


def _rescue_late(nzbid, stop, getter):
    """Delete a FORCE re-submit that landed after a user cancel (not shutdown)."""
    if nzbid and nzbid is not RESCUE_ABANDONED and not stop["aborted"]:
        _core.nzbget_api.cancel_jobs([nzbid], settings_getter=getter)


def _rescue_or_exhausted(state, fleet):
    """Group-follow exhaustion decision, with the one-shot FORCE rescue (#372 r6).

    Returns a terminal outcome dict, or None when a rescue was performed (the
    caller keeps polling; ``state["current"]`` now tracks the FORCE re-submit).
    The rescue fires at most once (``state["rescued"]`` is set even if the append
    fails) and only when the original pick died ``DELETED/COPY``
    (``state["copy_vetoed"]``); otherwise the legacy ``FAILURE/DUPE`` exhaustion
    is unchanged.
    """
    if state.get("copy_vetoed") and not state.get("rescued"):
        state["rescued"] = True  # one-shot, even if the append fails
        rescue = (fleet or {}).get("rescue")
        new_id = rescue() if rescue else None
        if new_id is RESCUE_ABANDONED:
            return None  # canceled/shutting down: the poll loop handles it
        if new_id:
            state["current"] = new_id
            state["promotion_deadline"] = None
            state["paused_nzbids"] = ()  # mirror _adopt_owned_promotion
            return None
        return {"outcome": "failed", "status": "FAILURE/COPY"}
    return {"outcome": "failed", "status": "FAILURE/DUPE"}


def _rescue_plain_pick(state, fleet):
    """One-shot FORCE rescue on the plain (no-DupeKey) submit path (#372 r6).

    The same content veto strikes a plain single submit; the poll carries a
    rescue callable there too. Sets ``state["current"]`` to the FORCE re-submit's
    NZBID and returns True (the poll keeps tracking it), or False when the rescue
    is unavailable/failed or was already spent (the caller then returns the raw
    failed status, unchanged from today).
    """
    if state.get("rescued"):
        return False
    state["rescued"] = True
    rescue = (fleet or {}).get("rescue")
    new_id = rescue() if rescue else None
    if new_id is RESCUE_ABANDONED:
        return True  # canceled/shutting down: keep polling; the loop exits
    if new_id:
        state["current"] = new_id
        return True
    return False


def _preexisting_success_ids(dupe_key, settings_getter, history=None):
    """Same-key SUCCESS rows already in history when the poll starts (#372 r4).

    Group-follow must IGNORE them: they predate this resolve (their files may
    be long gone -- the picker's reuse probe already declined them), and
    playing one would fail "No video file found" instead of waiting for this
    fleet's own member to complete. Best-effort: an RPC error yields ``()``
    (fail-open to the pre-round-4 behavior). Relocated here from nzbget_resolver
    to keep that module under the Codacy file-NLOC gate (#372 r6); re-exported
    so the suite's ``resources.lib.nzbget_resolver._preexisting_success_ids``
    patch path keeps intercepting.
    """
    try:
        return tuple(
            _core.nzbget_api.success_ids_by_dupekey(
                dupe_key, settings_getter=settings_getter, history=history
            )
        )
    except Exception:  # pylint: disable=broad-except
        return ()


def _canceled_resolve_nzbids(nzbid, poll_result, submitted_nzbids):
    """Every NZBID this resolve may have running at cancel (#372 round 5).

    ID-SCOPED, never a whole-DupeKey sweep: an overlapping play of the same
    release (another client, or an already-queued retry) shares the stable
    DupeKey and must survive this cancel. Covers the tracked member (the
    promoted backup once failover switched, OR the FORCE rescue re-submit once
    it was adopted -- #372 r6), any paused-promoted members (a promotion that
    landed while NZBGet was paused never becomes tracked), the worker's
    submitted backups (the parked hidden DUP rows -- ``cancel_jobs`` deletes
    history before queue, so nothing from this fleet is left to promote; a manual
    final-delete does not trigger NZBGet's failover), and the original pick. An
    append still in flight at cancel is covered by the worker's own drain
    cleanup. Relocated from nzbget_resolver to keep that module under the Codacy
    file-NLOC gate (#372 r6); re-exported so its call site is unchanged.
    """
    result = poll_result or {}
    ids = []
    for candidate in [
        result.get("nzbid"),
        *(result.get("paused_nzbids") or ()),
        *(submitted_nzbids or []),
        nzbid,
    ]:
        if candidate is not None and candidate not in ids:
            ids.append(candidate)
    return ids


def _read_poll_interval(settings_getter):
    """Read+clamp the shared ``poll_interval`` setting (seconds).

    The NZBGet path honors the same backend-agnostic Polling setting as the
    nzbdav path (range [1..60]) instead of a hardcoded cadence. Relocated here
    from nzbget_resolver to keep that module under the Codacy file-NLOC gate
    (#372 r6); the clamp constants and ``_bind_getter`` stay in nzbget_resolver
    and are reached through ``_core``.
    """
    getter = _core._bind_getter(settings_getter)
    try:
        interval = int(getter("poll_interval", "") or _core._DEFAULT_POLL_INTERVAL)
    except (TypeError, ValueError):
        interval = _core._DEFAULT_POLL_INTERVAL
    interval = max(interval, _core._POLL_INTERVAL_MIN)
    interval = min(interval, _core._POLL_INTERVAL_MAX)
    return interval
