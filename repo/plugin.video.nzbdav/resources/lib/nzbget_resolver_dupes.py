# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
# pylint: disable=cyclic-import

"""NZBGet Smart-Duplicates backup fleet: submit, widen, cancel-cleanup (#372).

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


def _submit_dupe_backups(
    backups,
    dupe_key,
    settings_getter,
    cancel_event=None,
    submitted_sink=None,
    dedup=None,
    veto_probe=True,
):
    """Submit the release's duplicate backups to NZBGet (#372, Smart Duplicates).

    ``backups`` is the picker-computed list of dicts with keys ``link``, ``title``, and
    ``score`` for the same-release rows. Each is appended with the shared
    ``dupe_key``, its own DupeScore (all below the pick's), and DupeMode=SCORE, so
    NZBGet keeps the pick (highest score) downloading and parks each backup in history
    as a duplicate -- failing over to the best remaining one if the pick is
    unrepairable. Because NZBGet decides by score, submission order does not
    matter: a backup submitted even after the pick has already succeeded is put
    into history as a backup (not deleted). Best-effort: a bad/duplicate URL or a
    failed fetch/append for one backup never aborts the rest or the pick. Stops
    early if ``cancel_event`` fires (the user canceled the resolve).

    ``dedup`` (a ``FleetDedup``) collapses listings and NZBs that are the same
    Usenet posting as the pick or an earlier backup; a fresh one is used when
    omitted. Returns the list of LIVE submitted NZBIDs -- a ``DELETED/COPY``-vetoed
    backup (NZBGet's content-fingerprint duplicate check refusing to re-add
    content already in history, #372 r6) is excluded so the fleet can backfill
    that slot, though it still lands in ``submitted_sink`` for cancel cleanup.
    ``veto_probe=False`` skips that per-append history probe (the unlimited
    fleet has no slot to backfill). ``submitted_sink`` (the resolve-shared
    ``ctx.submitted_nzbids``) receives each NZBID AS ITS APPEND SUCCEEDS -- a
    cancel mid-batch snapshots that list immediately, so an already-appended
    backup must be visible before the next append starts, not after the whole
    batch returns.
    """
    return _core._submit_candidates(
        backups,
        dupe_key,
        settings_getter,
        cancel_event=cancel_event,
        submitted_sink=submitted_sink,
        dedup=dedup,
        veto_probe=veto_probe,
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
    """Deduplicate, prefetch, and append fleet candidates in rank order.

    Same-listing candidates collapse into one fetch (the others are fallbacks
    for a failed grab); NZB bodies are fetched ``PREFETCH_WINDOW`` ahead in
    parallel and appended straight from memory; a body that is the same posting
    as an already-submitted one is skipped. A candidate whose every listing
    failed to fetch still gets one plain append (NZBGet's own fetch).
    ``limits`` is ``(live_limit, max_attempts)``: stops once ``live_limit``
    LIVE backups landed, ``max_attempts`` appends were tried, or
    ``cancel_event`` fires (``None`` limits are unbounded). Returns the LIVE
    NZBIDs.
    """
    live_limit, max_attempts = limits
    if dedup is None:
        dedup = _core.FleetDedup()
    seen = set()
    usable = []
    for candidate in candidates or []:
        link = _core._usable_backup_link(candidate, seen)
        if link:
            seen.add(link)
            usable.append(candidate)
    live = []
    attempts = 0
    stream = _core.prefetched_clusters(
        dedup.clusters(usable), _core.nzbget_api.fetch_nzb_bytes, cancel_event
    )
    try:
        for candidate, body in stream:
            if _fill_done(live, live_limit, attempts, max_attempts, cancel_event):
                break
            fingerprint = _core.posting_fingerprint(body) if body else None
            if fingerprint and dedup.known_posting(fingerprint):
                _core.xbmc.log(
                    "NeNeTeePee-Stream-Kodi: Skipped NZBGet duplicate backup '{}' "
                    "(same Usenet posting as one already queued)".format(
                        candidate.get("title") or ""
                    ),
                    _core.xbmc.LOGINFO,
                )
                continue
            attempts += 1
            extra = {"nzb_bytes": body} if body else {}
            nzbid = _core._append_one_backup(
                candidate["link"], candidate, dupe_key, settings_getter, **extra
            )
            if not nzbid:
                continue
            dedup.remember_posting(fingerprint)
            # Sink FIRST (round-5 invariant): a cancel mid-batch must be able to
            # delete this id immediately, and a COPY-vetoed row still needs deleting.
            if submitted_sink is not None:
                submitted_sink.append(nzbid)
            # A DELETED/COPY veto means the slot was never really filled ->
            # exclude it from the LIVE return so the fleet can backfill it (#372 r6).
            if not (
                veto_probe and _core._copy_vetoed_after_append(nzbid, settings_getter)
            ):
                live.append(nzbid)
            # Re-check BEFORE pulling the next item: pulling waits on (and
            # starts) another indexer fetch the satisfied cap no longer needs.
            if _fill_done(live, live_limit, attempts, max_attempts, cancel_event):
                break
    finally:
        stream.close()
    return live


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
    extra = {} if nzb_bytes is None else {"nzb_bytes": nzb_bytes}
    try:
        nzbid, error = _core.nzbget_api.append_nzb(
            nzb_url,
            job_name,
            settings_getter=settings_getter,
            dupe_key=dupe_key,
            dupe_score=score,
            dupe_mode="SCORE",
            **extra,
        )
    except Exception as exc:  # pylint: disable=broad-except
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup submit raised: {}".format(
                _core._redact_text(str(exc))
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


def _warn_if_healthcheck_pauses(settings_getter):
    """Warn if NZBGet's ``HealthCheck=Pause`` disables automatic dup failover.

    Per nzbget.com/documentation/rss/#duplicates automatic duplicate failover
    needs HealthCheck = Delete, None (or Park); with Pause NZBGet pauses a failed
    download instead of promoting a backup, so the picked release's backups sit
    idle until the user unpauses one. Best-effort -- an unreadable config is
    skipped. Always logs; notifies the user at most once per Kodi session.
    """
    try:
        value = _core.nzbget_api.config_option(
            "HealthCheck", settings_getter=settings_getter
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


def _snapshot_conn_getter(settings_getter):
    """A thread-safe getter over a main-thread snapshot of NZBGet connection
    settings (#372).

    Read the connection settings once on the calling (main/resolve) thread so the
    background backup worker performs NO off-thread Kodi ``getSetting`` (unsafe on
    CoreELEC/Kodi builds), and preserves a blank ``nzbget_username``/password
    verbatim (``dict.get`` returns the stored ``""`` rather than the auth default
    the addon getter substitutes).
    """
    url, user, password, category = _core.nzbget_api._get_settings(settings_getter)
    snapshot = {
        "nzbget_url": url,
        "nzbget_username": user,
        "nzbget_password": password,
        "nzbget_category": category,
    }
    return lambda key, default="": snapshot.get(key, default)


def _dupe_check_disabled(settings_getter):
    """True only when NZBGet's ``DupeCheck`` option is explicitly ``no``.

    With DupeCheck off NZBGet does not park same-key items as backups -- it would
    download every one as a normal queue item (parallel full downloads). Best-
    effort: an unreadable config returns False (assume the default, on).
    """
    try:
        return (
            _core.nzbget_api.config_option("DupeCheck", settings_getter=settings_getter)
            == "no"
        )
    except Exception:  # pylint: disable=broad-except
        return False


_MAX_EXTRA_BACKUPS = 5

# Hard bound on the extra append ATTEMPTS spent replacing COPY-vetoed candidates
# (#372 r6), so a pathological all-vetoed loader pool can't grind the worker (and
# the is_submitting-extended failover grace) for minutes.
_MAX_VETO_REPLACEMENTS = 5


# The candidate fields a fleet row keeps: its job name plus the listing
# evidence (size, post date) the same-listing dedup reads.
_FLEET_ROW_KEYS = ("title", "size", "pubdate", "_posted_epoch")


def _extra_backups_from_loader(
    loader, seen_links, limit=_MAX_EXTRA_BACKUPS, score_base=0, reserve=0, leading=None
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
    when the leading uploads leave room. Best-effort: a missing/erroring loader,
    its turned-off sentinel (a non-list), or ``limit <= 0`` yields no loader
    candidates.
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


def _dupe_worker_should_skip(getter, cancel_event):
    """True when the backup worker must submit nothing (#372).

    Skips silently on a pre-submit cancel (the user already gave up on the
    resolve), and skips with a log when the server has DupeCheck=no -- same-key
    items would then download in parallel instead of parking as backups.
    """
    if cancel_event.is_set():
        return True
    if _core._dupe_check_disabled(getter):
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet DupeCheck=no -- skipping #372 duplicate "
            "backups (they would download in parallel).",
            _core.xbmc.LOGINFO,
        )
        return True
    return False


def _fleet_is_unlimited(dupe):
    """True when the fleet submits every candidate (``nzbget_max_backups`` = -1)."""
    max_backups = dupe.get("max_backups")
    return isinstance(max_backups, int) and max_backups < 0


def _submit_backup_fleet(getter, cancel_event, dupe_key, dupe, submitted_ids):
    """Submit the same-release backups, then the widened extras (#372).

    Widens with NZBHydra duplicate uploads and same-content loader candidates
    (#372 r2) as lowest-priority backups keyed under the same (pick's) DupeKey.
    A capped fleet bounds them by the cap's REMAINING slots so backups + extras
    never exceed ``nzbget_max_backups``; an unlimited fleet (-1) submits every
    candidate and skips the per-append COPY-veto probe (there is no slot to
    backfill). Both phases share one ``FleetDedup`` so a listing or posting
    already covered by the pick or an earlier backup is never re-submitted.
    Rides every score on the fleet's ``score_base`` so they outrank prior
    same-key successes (#372 r4). A loader-only fleet (``backups`` empty,
    NZBHydra collapsed every mirror into one row) submits just the extras.
    Reads ONLY the snapshot ``getter`` -- this runs on the worker thread, which
    must never call into Kodi. Every appended NZBID is recorded into
    ``submitted_ids`` AS IT LANDS so the post-cancel cleanup can delete exactly
    this resolve's submissions.
    """
    backups = list(dupe.get("backups") or [])
    unlimited = _fleet_is_unlimited(dupe)
    dedup = _core.FleetDedup(pick=dupe.get("pick"))
    dedup.remember_posting(dupe.get("pick_fingerprint"))
    live = _core._submit_dupe_backups(
        backups,
        dupe_key,
        getter,
        cancel_event=cancel_event,
        submitted_sink=submitted_ids,
        dedup=dedup,
        veto_probe=not unlimited,
    )
    if cancel_event.is_set():
        return
    # Extras budget = the cap's slots the LIVE same-release backups left free;
    # a COPY-vetoed (or entirely failed) append frees its slot for a loader
    # replacement (#372 r6). Unlimited fleets have no budget.
    max_backups = dupe.get("max_backups")
    if unlimited:
        remaining = None
    elif max_backups is None:
        remaining = _MAX_EXTRA_BACKUPS
    else:
        remaining = max(0, max_backups - len(live))
    candidates = _core._loader_extras_for_fleet(dupe, backups, live_count=len(live))
    if candidates:
        _core._submit_extras_until_filled(
            candidates,
            remaining,
            dupe_key,
            getter,
            cancel_event,
            submitted_ids,
            dedup=dedup,
            veto_probe=not unlimited,
        )


def _submit_extras_until_filled(
    candidates,
    remaining,
    dupe_key,
    getter,
    cancel_event,
    submitted_ids,
    dedup=None,
    veto_probe=True,
):
    """Append extras until ``remaining`` LIVE backups land (#372 r6).

    The veto-aware fill: it keeps drawing from the widened ``candidates`` list
    past any that NZBGet vetoes as ``DELETED/COPY`` (a slot that was never
    really filled), so a same-content mirror already in NZBGet's history can't
    silently shrink the fallback depth. Bounded three ways -- ``remaining``
    LIVE extras appended, a user cancel, or ``remaining +
    _MAX_VETO_REPLACEMENTS`` total append attempts -- so a pathological
    all-vetoed pool can't grind the worker (and the ``is_submitting``-extended
    failover grace) for minutes. ``remaining=None`` (an unlimited fleet)
    appends every candidate. Each appended NZBID lands in ``submitted_ids`` AS
    IT LANDS (vetoed ones too, for cancel cleanup). Returns the LIVE extra
    NZBIDs.
    """
    if remaining is not None and remaining <= 0:
        return []
    max_attempts = None if remaining is None else remaining + _MAX_VETO_REPLACEMENTS
    return _core._submit_candidates(
        candidates,
        dupe_key,
        getter,
        cancel_event=cancel_event,
        submitted_sink=submitted_ids,
        dedup=dedup,
        veto_probe=veto_probe,
        limits=(remaining, max_attempts),
    )


def _hydra_uploads_for_fleet(dupe):
    """NZBHydra duplicate uploads for the pick, absorbing every failure (#372)."""
    loader = dupe.get("hydra_uploads")
    if loader is None:
        return []
    return _core._load_extra_candidates(loader)


def _loader_extras_for_fleet(dupe, backups, live_count=None):
    """The fleet's widened extras, bounded and score-based (#372 r2/r4/r6).

    NZBHydra duplicate uploads come first, then the fallback loader's
    same-content candidates. A capped fleet bounds them by the cap's REMAINING
    slots (``max_backups`` minus the backups already spent; ``None`` = the hard
    extras cap); an unlimited fleet (-1) takes them all. ``live_count`` (#372
    r6) is the count of backups that ACTUALLY landed live -- a
    ``DELETED/COPY``-vetoed backup frees its slot for a loader replacement, so
    the remaining-slot math uses the live tally when given (else
    ``len(backups)`` for back-compat). The candidate list is widened by
    ``_MAX_VETO_REPLACEMENTS`` (reserve) so the fill loop has headroom to draw
    replacements for vetoed extras.
    """
    extras_limit = dupe.get("max_backups")
    spent = len(backups) if live_count is None else live_count
    if _fleet_is_unlimited(dupe):
        remaining = None
    elif extras_limit is None:
        remaining = _MAX_EXTRA_BACKUPS
    else:
        remaining = max(0, extras_limit - spent)
    # Extras start just below the lowest same-release backup (base - count - 1);
    # the whole fleet rides BELOW the base so any later fleet's pick (== its
    # own, larger base) strictly outranks every member of this one. The anchor
    # stays on the INTENDED backup count (len(backups)), NOT the live tally,
    # so round-4's cross-fleet ordering guarantees are untouched.
    if remaining is not None and remaining <= 0:
        return []
    return _core._extra_backups_from_loader(
        dupe.get("loader"),
        [b.get("link") for b in backups],
        limit=remaining,
        score_base=int(dupe.get("score_base") or 0) - len(backups) - 1,
        reserve=_MAX_VETO_REPLACEMENTS,
        leading=_core._hydra_uploads_for_fleet(dupe),
    )


def _cleanup_canceled_submissions(getter, submitted_ids):
    """Delete exactly THIS worker's submissions after a mid-submit cancel (#372).

    Covers the append that was already in flight when the user canceled and so
    landed after _handle_poll_failure's one-shot DupeKey sweep. Scoped to the
    NZBIDs this resolve submitted -- NEVER a whole-DupeKey sweep, which could
    wipe a fresh retry of the same release (it shares the stable DupeKey) that
    started while this stale worker drained. Best-effort like every other
    backup step: a failed delete is swallowed, never raised off-thread.
    """
    try:
        _core.nzbget_api.cancel_jobs(submitted_ids, settings_getter=getter)
    except Exception:  # pylint: disable=broad-except
        pass


def _nothing_to_submit(dupe_key, dupe):
    """True when the worker has no possible submission (#372 r4).

    No key means no Smart-Duplicates fleet at all; with a key, an empty
    same-release list is still submittable when a loader or the NZBHydra
    duplicate-upload lookup exists to widen from (the NZBHydra
    collapsed-mirrors case -- a loader-only fleet).
    """
    if not dupe_key:
        return True
    return (
        not dupe.get("backups")
        and dupe.get("loader") is None
        and dupe.get("hydra_uploads") is None
    )


def _spawn_dupe_backups(ctx):
    """Fire-and-forget the release's duplicate backups in a daemon thread (#372).

    Runs off the resolve thread (each backup is an indexer HTTP round-trip) so it
    never delays the pick's poll/progress ("it won't affect playback"); the
    daemon flag keeps it from blocking Kodi shutdown. Because every item carries
    an explicit DupeScore (the pick highest), NZBGet keeps the pick the active
    download regardless of when the backups land -- so submission order is not a
    concern and a backup arriving after the pick already succeeded is still put
    into history as a backup, not deleted. Skips entirely when the server has
    DupeCheck turned off (backups would download in parallel), and warns once if
    HealthCheck=Pause would block automatic failover. Reads settings from a
    main-thread snapshot so the worker never touches Kodi off-thread. All errors
    are swallowed -- backups are pure insurance and must never break playback.
    """
    dupe = ctx.dupe or {}
    dupe_key = dupe.get("key") or ""
    if _core._nothing_to_submit(dupe_key, dupe):
        return None
    try:
        getter = _core._snapshot_conn_getter(ctx.settings_getter)
    except Exception as exc:  # pylint: disable=broad-except
        # The snapshot reads Kodi/injected settings and runs AFTER the primary is
        # already accepted. Backups are pure insurance -- a settings-read failure
        # here must skip them, never propagate out and fail the primary's playback.
        _core.xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup snapshot failed: {}"
            ).format(_core._redact_text(str(exc))),
            _core.xbmc.LOGWARNING,
        )
        return None
    cancel_event = ctx.cancel_event
    # Share the appended-ids list with the resolve thread: the cancel path
    # deletes exactly these (id-scoped, never a whole-DupeKey sweep).
    submitted_ids = getattr(ctx, "submitted_nzbids", None)
    if submitted_ids is None:
        submitted_ids = []

    def _worker():
        reached_submit = False
        try:
            if _core._dupe_worker_should_skip(getter, cancel_event):
                return
            _core._warn_if_healthcheck_pauses(getter)
            reached_submit = True
            _core._submit_backup_fleet(
                getter, cancel_event, dupe_key, dupe, submitted_ids
            )
        except Exception as exc:  # pylint: disable=broad-except
            _core.xbmc.log(
                (
                    "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup worker "
                    "error: {}"
                ).format(_core._redact_text(str(exc))),
                _core.xbmc.LOGWARNING,
            )
        finally:
            # If a cancel arrived while a backup's append was already in flight,
            # that backup can land in NZBGet AFTER _handle_poll_failure's
            # one-shot id-scoped cancel -- and NZBGet would then promote the
            # orphan as the group's new active download. Clean up once the
            # worker has drained, scoped to this resolve's own submissions
            # (#372 r2 cancel-race, r3 retry-race).
            if reached_submit and cancel_event.is_set() and submitted_ids:
                _core._cleanup_canceled_submissions(getter, submitted_ids)

    try:
        thread = threading.Thread(
            target=_worker, name="nzbdav-nzbget-dupe-backups", daemon=True
        )
        thread.start()
    except Exception as exc:  # pylint: disable=broad-except
        # for example, RuntimeError "can't start new thread" under thread exhaustion.
        # The backups are pure insurance -- never let them break the already-
        # queued pick's playback.
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet duplicate backup spawn failed: {}".format(
                _core._redact_text(str(exc))
            ),
            _core.xbmc.LOGWARNING,
        )
        return None
    return thread


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


def _pick_rescue_callable(ctx, nzb_url, title):
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
        dupe = ctx.dupe or {}
        if _core.nzbget_api.active_group_by_name(
            title, settings_getter=ctx.settings_getter
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
        try:
            nzbid, error = _core.nzbget_api.append_nzb(
                nzb_url,
                title,
                settings_getter=ctx.settings_getter,
                dupe_key=dupe.get("key") or "",
                dupe_score=int(dupe.get("pick_score") or 0),
                dupe_mode="FORCE",
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
    if new_id:
        state["current"] = new_id
        return True
    return False


def _preexisting_success_ids(dupe_key, settings_getter):
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
                dupe_key, settings_getter=settings_getter
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
