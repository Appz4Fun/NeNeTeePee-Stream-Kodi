# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
# pylint: disable=cyclic-import

"""Foreground NZBGet duplicate fleet: find, download, dedupe, spool, send (#372).

When the user picks a release on the NZBGet backend, the pick and every other
NZB of the same release are handled on the resolve thread, behind the progress
dialog:

1. "Looking for duplicate NZBs..." -- the picker's same-release rows, NZBHydra's
   hidden duplicate uploads, and the fallback loader's same-content extras.
2. "Downloading NZBs N of M" -- every NZB is downloaded (four at a time),
   checked against the pick and every NZB already kept, and each unique one is
   saved to a temp folder on disk.
3. "Sending N NZBs to NZBGet..." -- every saved NZB is uploaded, the pick first
   at the top DupeScore; the temp folder is deleted only after all were sent.

A backup an earlier play already sent in the last day (``nzbget_submit_ledger``)
is skipped before its download when NZBGet still holds that copy under the
same DupeKey; a still-parked copy joins this resolve's failover tracking.

The caller then polls the pick's download ("Downloading... 0%") as before.
Names that tests patch on ``nzbget_resolver`` are reached through ``_core``.
"""

import contextlib
import os
import tempfile
import time

import resources.lib.nzbget_resolver as _core  # noqa: F401  pylint: disable=unused-import
from resources.lib import nzbget_submit_ledger
from resources.lib.fallback_streams import _MAX_FALLBACKS
from resources.lib.nzbget_fleet_dedup import FleetDedup, call_abortable, same_listing


def submit_fleet(ctx, nzb_url, title, dupe_key):
    """Download, dedupe, and send the pick plus its backups; ``(nzbid, error)``.

    ``nzbid`` is the pick's NZBGet id (None when its append failed, with
    NZBGet's ``error``). ``ctx.preexisting_successes`` receives the same-key
    SUCCESS ids snapshotted before anything was submitted (None if unknown).
    A user cancel sets ``ctx.cancel_event`` and returns ``(None, None)``;
    anything already appended is in ``ctx.submitted_nzbids`` for the caller
    to delete -- unless ``ctx.fleet_aborted`` says Kodi is shutting down, when
    they are left to finish.
    """
    dupe = ctx.dupe if isinstance(ctx.dupe, dict) else {}
    getter = ctx.settings_getter
    loader_stop = dupe.get("loader_stop")
    progress = _FleetProgress(
        ctx.dialog,
        ctx.cancel_event,
        on_cancel=loader_stop.set if loader_stop is not None else None,
    )
    progress.finding()
    # Every NZBGet call below runs off-thread (abortable): read the connection
    # settings ONCE here on the resolve thread -- no off-thread getSetting.
    getter = _snapshot_getter(getter)
    dupe_check_off, ctx.preexisting_successes, max_score, members = (
        _probe_nzbget_config(getter, progress, dupe_key)
    )
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
    _lift_scores(dupe, max_score)
    held = nzbget_submit_ledger.held(dupe_key, members)
    pick = dict(
        dupe.get("pick") or {},
        link=nzb_url,
        title=title,
        score=int(dupe.get("pick_score") or 0),
        _is_pick=True,
    )
    if ctx.preexisting_successes is None:
        # NZBGet's history couldn't be read: no backups, and the pick's score
        # can't be lifted above an older same-key item.
        dupe_check_off = True
        if members is not None and not members:
            # The queue WAS read and holds nothing under this key: FORCE (no
            # duplicate checks) can't start a parallel download, and it keeps
            # SCORE from parking the pick behind an unseen older success.
            pick["_dupe_mode"] = "FORCE"
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet history unreadable -- sending the "
            "pick alone (DupeMode={}, no #372 backups).".format(
                pick.get("_dupe_mode") or "SCORE"
            ),
            _core.xbmc.LOGINFO,
        )
    candidates = [pick]
    if dupe_check_off:
        # Same-key items would download in parallel instead of parking as
        # backups: send the pick alone.
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet DupeCheck=no -- skipping #372 duplicate "
            "backups (they would download in parallel).",
            _core.xbmc.LOGINFO,
        )
    cap = dupe.get("max_backups")
    capped = isinstance(cap, int) and cap > 0
    if not dupe_check_off:
        # Unlimited: everything up front (download all, then send all).
        # Capped: the loader (it downloads manifests) waits until the cap is
        # known to be unfilled -- see below.
        candidates += _fleet_backups(dupe, progress, include_loader=not capped)
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
    # The cap bounds the BACKUPS (the pick is never counted); a capped fleet
    # probes each append for a DELETED/COPY veto so the next round can
    # backfill that backup's slot.
    limits = (cap, cap + _core._MAX_VETO_REPLACEMENTS) if capped else (None, None)
    dedup = FleetDedup(spool_base=_core._fleet_spool_base(), progress=progress.update)
    dedup.aborted = lambda: progress.aborted
    run = (dupe_key, getter, ctx, dedup, held)
    try:
        live = len(_send_batch(run, candidates, limits, capped))
        if capped and not dupe_check_off and pick.get("_nzbid"):
            _fill_from_loader(dupe, progress, run, (cap, live, candidates[1:]))
    finally:
        dedup.close()
    # A shutdown requested during the last append's wait may not have been
    # seen yet: check once more so the resolve never starts polling (several
    # RPCs before its first waitForAbort) while Kodi is exiting.
    progress.canceled()
    ctx.fleet_aborted = progress.aborted
    # The pick's downloaded body, parked ON DISK right after its append for
    # the poll's rare FORCE rescue (re-sending it beats re-fetching a dead,
    # mirrored, or single-use URL); the resolve deletes it when it ends, a
    # cancel included. Only when no temp folder was writable is it kept in
    # memory, the only good copy.
    ctx.pick_nzb_path = pick.pop("_body_path", None)
    body = pick.pop("_body", None)
    if ctx.cancel_event.is_set():
        return None, None
    if ctx.pick_nzb_path is None and body:
        ctx.pick_nzb_bytes = body
    nzbid = pick.get("_nzbid")
    return (nzbid, None) if nzbid else (None, pick.get("_append_error"))


def _park_pick_body(body):
    """Write the pick's NZB to a private temp file; its path, or None.

    Kodi's temp folder first, then the system temp directory: a folder that
    takes the file but then runs out of space falls through to the next one
    (whole create-and-write retried), so the pick is kept in memory only when
    no folder can hold it. The resolve deletes the file when it ends
    (``_discard_parked_pick``).
    """
    if not body:
        return None
    try:
        preferred = _core._fleet_spool_base()
    except (OSError, TypeError, ValueError):
        preferred = None
    for parent in dict.fromkeys((preferred, None)):
        path = _write_parked(body, parent)
        if path is not None:
            return path
    return None


def _write_parked(body, parent):
    """One create-and-write attempt in ``parent`` (None = system temp)."""
    try:
        handle, path = tempfile.mkstemp(
            prefix="nzbdav-pick-", suffix=".nzb", dir=parent
        )
    except (OSError, TypeError, ValueError):
        return None
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(body)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(path)
        return None
    return path


def _lift_scores(dupe, max_score):
    """Raise the whole fleet's scores above NZBGet's highest same-key score.

    The picker's scores ride a wall-clock base; after a clock rollback that
    base can fall below an earlier same-key item, and NZBGet would then
    dupe-delete the fresh pick. Shifting the pick, the base, and every backup
    by the same amount keeps their relative order.
    """
    pick_score = int(dupe.get("pick_score") or 0)
    if max_score is None or max_score < pick_score:
        return
    bump = max_score + 1 - pick_score
    dupe["pick_score"] = pick_score + bump
    dupe["score_base"] = int(dupe.get("score_base") or 0) + bump
    for backup in dupe.get("backups") or []:
        if isinstance(backup, dict):
            backup["score"] = int(backup.get("score") or 0) + bump


def _snapshot_getter(getter):
    """A thread-safe getter over the NZBGet connection settings, read now."""
    url, user, password, category = _core.nzbget_api._get_settings(getter)
    snapshot = {
        "nzbget_url": url,
        "nzbget_username": user,
        "nzbget_password": password,
        "nzbget_category": category,
    }
    return lambda key, default="": snapshot.get(key, default)


def _fill_from_loader(dupe, progress, run, state):
    """Capped fleet: fill the slots the picker/Hydra rows left open, on demand.

    ``state`` is ``(cap, live, prior)``. The fallback loader downloads NZB
    manifests, so it is asked for only as many candidates as there are open
    slots; when an append then fails or is COPY-vetoed and slots stay open, it
    is asked again for that many MORE (up to its own ceiling), so a rejected
    candidate is replaced without paying grabs up front.
    """
    cap, live, prior = state
    prior = list(prior)
    limit = dupe.get("loader_limit")
    dedup = run[3]
    # One replacement budget for the whole fleet: loader batches continue the
    # attempts the picker/Hydra pass already spent (never a fresh allowance).
    budget = cap + _core._MAX_VETO_REPLACEMENTS
    while live < cap and not progress.canceled():
        attempts_left = budget - getattr(dedup, "attempts_used", 0)
        if attempts_left <= 0:
            return
        if isinstance(limit, dict):
            asked = int(limit.get("n") or 0) + (cap - live)
            if limit.get("n") is not None and limit["n"] >= _MAX_FALLBACKS:
                return
            limit["n"] = min(_MAX_FALLBACKS, asked)
        more = _loader_extras(dupe, progress, prior)
        if progress.canceled():
            return
        if not more:
            # Everything the loader exposed was filtered out (language, year,
            # variant, seen link): widen and ask again, up to its ceiling.
            if isinstance(limit, dict) and (limit.get("n") or 0) < _MAX_FALLBACKS:
                continue
            return
        remaining = cap - live
        live += len(
            _send_batch(
                run,
                more,
                (remaining, budget - getattr(dedup, "attempts_used", 0)),
                True,
            )
        )
        prior += more
        if not isinstance(limit, dict):
            return  # a fixed loader has nothing more to give


# The whole pre-fleet probe's budget; past it the fleet proceeds on defaults.
_PREFLIGHT_BUDGET_SECONDS = 20


def _probe_nzbget_config(getter, progress, dupe_key):
    """Pre-fleet NZBGet probes, abortably.

    Returns ``(dupecheck_off, preexisting, max_score, members)``.

    The same-key preexisting-success snapshot, the highest same-key DupeScore
    (``_lift_scores``), and the same-key members an earlier play left in
    NZBGet (``nzbget_api.dupekey_member_states``, for the resubmit ledger)
    share one ``history`` and one ``listgroups`` read; the ``DupeCheck=no``
    check and the HealthCheck=Pause warning share one ``config`` read. They
    run off-thread behind the cancel/shutdown-aware wait with a shared
    ``_PREFLIGHT_BUDGET_SECONDS`` budget (an unresponsive NZBGet can't stall
    playback for minutes), reading only ``getter`` (the caller's settings
    snapshot). A canceled or failed probe returns ``(True, None, None, None)``:
    DupeCheck unknown counts as off (the pick is sent alone, under SCORE),
    the poll snapshots successes itself, scores are left as computed, and
    membership is unknown (nothing is skipped).
    """

    give_up_at = time.monotonic() + _PREFLIGHT_BUDGET_SECONDS

    def _stopped():
        # The worker itself stops between RPCs once the wait was abandoned
        # (cancel, shutdown, or the shared budget) -- no work after cleanup.
        return progress.cancel_event.is_set() or time.monotonic() >= give_up_at

    def _probe():
        # ONE history read serves both the same-key success snapshot (taken
        # BEFORE this fleet submits anything) and the highest same-key score;
        # ONE config read serves both DupeCheck and HealthCheck. A failed
        # history read leaves the snapshot UNKNOWN (None) so the poll retries.
        history = _core.nzbget_api.history_rows(getter)
        if history is None and not _stopped():
            history = _core.nzbget_api.history_rows(getter)  # one retry
        preexisting = (
            None
            if history is None
            else _core._preexisting_success_ids(dupe_key, getter, history=history)
        )
        if _stopped():
            return True, preexisting, None, None
        queue = _core.nzbget_api.queue_rows(getter)
        max_score = _core.nzbget_api.max_dupe_score_by_dupekey(
            dupe_key, settings_getter=getter, history=history or [], queue=queue or []
        )
        # Unknown queue: membership unknown (None). Unknown history: only the
        # queue's same-key members are known (still enough to skip them, and
        # to refuse a FORCE that would download in parallel with one).
        members = (
            None
            if queue is None
            else _core.nzbget_api.dupekey_member_states(dupe_key, history or [], queue)
        )
        if _stopped():
            return True, preexisting, max_score, members
        options = _core.nzbget_api.config_options(
            ("DupeCheck", "HealthCheck"), settings_getter=getter
        )
        if _core._dupe_check_disabled(getter, options=options):
            return True, preexisting, max_score, members
        if not _stopped():
            _core._warn_if_healthcheck_pauses(getter, options=options)
        return False, preexisting, max_score, members

    # A timed-out (or failed) probe fails CLOSED: DupeCheck unknown means the
    # pick goes alone, never an unbounded fleet of parallel full downloads.
    return call_abortable(
        _probe,
        (progress.cancel_event,),
        progress.canceled,
        default=(True, None, None, None),
        deadline=_PREFLIGHT_BUDGET_SECONDS,
    )


def _send_batch(run, candidates, limits, capped):
    """One download-dedupe-send pass of ``_submit_candidates``; LIVE backup ids.

    Backups NZBGet still holds from an earlier play are skipped first
    (``_skip_held``); every NZB this pass got into NZBGet is then recorded in
    the resubmit ledger. A still-parked backup this pass adopted is a live
    backup the poll follows, so it takes one of the cap's slots: it is
    returned with the newly sent ids and shrinks this pass's live limit.
    """
    dupe_key, getter, ctx, dedup, held = run
    live_limit, max_attempts = limits
    fresh, adopted = _skip_held(candidates, held, ctx, slots=live_limit)
    if live_limit is not None:
        live_limit = max(0, live_limit - len(adopted))
    if max_attempts is not None:
        # An adopted slot is not extra replacement allowance either.
        max_attempts = max(0, max_attempts - len(adopted))
    if adopted:
        # ...for this pass and for the loader batches that continue it.
        dedup.attempts_used = getattr(dedup, "attempts_used", 0) + len(adopted)
    try:
        live = _core._submit_candidates(
            fresh,
            dupe_key,
            getter,
            cancel_event=ctx.cancel_event,
            submitted_sink=ctx.submitted_nzbids,
            dedup=dedup,
            veto_probe=capped,
            limits=(live_limit, max_attempts),
        )
        return list(adopted) + list(live)
    finally:
        nzbget_submit_ledger.record(
            [row for row in fresh if isinstance(row, dict) and row.get("_nzbid")],
            dupe_key,
        )


def _skip_held(candidates, held, ctx, slots=None):
    """``candidates`` minus the backups an earlier play left in NZBGet.

    A backup is held when the resubmit ledger has its link (credentials
    stripped), or a same-listing row, under this DupeKey, and NZBGet still
    holds that NZBID (``nzbget_submit_ledger.held``). Skipping it saves the
    indexer grab and the duplicate append. A ``"parked"`` copy is a working
    backup: its NZBID joins ``ctx.adopted_nzbids`` so this resolve's poll
    follows a failover onto it, and the row takes that ``_nzbid`` for the
    completion ledger. A ``"dead"`` copy (failed, or refused as a copy) would
    only fail again. The pick is always sent. At most ``slots`` copies are
    adopted (None = all): past the cap a held copy is still skipped (not sent
    again) but not followed. Returns ``(kept, adopted)``: the candidates to
    send, and the NZBIDs newly adopted by this call.
    """
    if not held:
        return list(candidates), []
    by_link = {entry.get("link"): entry for entry in held}
    kept = []
    newly = []
    for candidate in candidates:
        entry = None
        if isinstance(candidate, dict) and not candidate.get("_is_pick"):
            entry = by_link.get(nzbget_submit_ledger.link_key(candidate.get("link")))
            if entry is None:
                entry = next(
                    (row for row in held if same_listing(candidate, row)), None
                )
        if entry is None:
            kept.append(candidate)
            continue
        room = slots is None or len(newly) < slots
        if entry.get("state") == "parked" and room:
            candidate["_nzbid"] = entry.get("nzbid")
            adopted = getattr(ctx, "adopted_nzbids", None)
            if isinstance(adopted, list) and entry.get("nzbid") not in adopted:
                adopted.append(entry.get("nzbid"))
                newly.append(entry.get("nzbid"))
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: Skipped NZBGet duplicate backup '{}' "
            "(sent in the last day; NZBGet still holds it as {})".format(
                candidate.get("title") or "", entry.get("state")
            ),
            _core.xbmc.LOGINFO,
        )
    return kept, newly


def _fleet_backups(dupe, progress, include_loader=True):
    """The pick's backups in rank order: picker rows, then Hydra (+ loader) extras.

    The extras are scored just below the picker rows and shared as
    ``dupe["extras"]`` for the completion ledger. The NZBHydra search and the
    fallback loader can each take tens of seconds, so they run off-thread
    behind an abortable wait: a dialog cancel or Kodi shutdown abandons them
    (and the fleet proceeds without extras). ``include_loader=False`` leaves
    the loader for ``_loader_extras`` (a capped fleet's last resort).
    """
    backups = list(dupe.get("backups") or [])

    def _extras():
        return _core._extra_backups_from_loader(
            dupe.get("loader") if include_loader else None,
            [backup.get("link") for backup in backups],
            limit=None,
            score_base=int(dupe.get("score_base") or 0) - len(backups) - 1,
            leading=_core._hydra_uploads_for_fleet(dupe),
            pick=dupe.get("pick"),
        )

    extras = call_abortable(
        _extras, (progress.cancel_event,), progress.canceled, default=[]
    )
    dupe["extras"] = extras
    return backups + extras


def _loader_extras(dupe, progress, prior):
    """The fallback loader's extras, ranked below ``prior`` (abortable).

    Scored just below the lowest-scored row already in the fleet and added to
    ``dupe["extras"]`` for the completion ledger.
    """
    if dupe.get("loader") is None:
        return []
    scores = [int(row.get("score") or 0) for row in prior if isinstance(row, dict)]
    floor = min(scores) if scores else int(dupe.get("score_base") or 0)

    def _extras():
        return _core._extra_backups_from_loader(
            dupe.get("loader"),
            [row.get("link") for row in prior if isinstance(row, dict)],
            limit=None,
            score_base=floor - 1,
            pick=dupe.get("pick"),
        )

    more = call_abortable(
        _extras, (progress.cancel_event,), progress.canceled, default=[]
    )
    dupe["extras"] = list(dupe.get("extras") or []) + list(more)
    return more


class _FleetProgress:
    """Drives the resolve's progress dialog and turns its cancel into an event."""

    def __init__(self, dialog, cancel_event, on_cancel=None):
        self._dialog = dialog
        self._cancel_event = cancel_event
        # Kodi shutdown (vs a user cancel): submitted jobs are left to finish.
        self.aborted = False
        # Called once a cancel/shutdown is seen (stops the loader's engine).
        self._on_cancel = on_cancel

    @property
    def cancel_event(self):
        return self._cancel_event

    def finding(self):
        self._update(0, _core._string(30615))

    def update(self, phase, done, total):
        if phase == "download":
            percent = int(done * 100 / total) if total else 0
            self._update(percent, _core._fmt(30616, done, total))
        elif phase == "send":
            percent = int(done * 100 / total) if total else 100
            self._update(percent, _core._fmt(30617, total))
        self.canceled()

    def canceled(self):
        """True (and the cancel event set) on a dialog cancel or Kodi shutdown."""
        # Two independent probes: a dialog Kodi is tearing down (iscanceled
        # raising) must never suppress the shutdown check. ``is True``: Kodi
        # returns real bools; anything else (a stub) is not a cancel.
        try:
            if self._dialog is not None and self._dialog.iscanceled() is True:
                self._cancel_event.set()
        except Exception as exc:  # pylint: disable=broad-except
            _log_dialog_error(exc)
        try:
            if _core.xbmc.Monitor().abortRequested() is True:
                self.aborted = True
                self._cancel_event.set()
        except Exception as exc:  # pylint: disable=broad-except
            _log_dialog_error(exc)
        canceled = self._cancel_event.is_set()
        if canceled and self._on_cancel is not None:
            self._on_cancel()
        return canceled

    def _update(self, percent, message):
        if self._dialog is None:
            return
        try:
            self._dialog.update(max(0, min(100, percent)), message)
        except Exception as exc:  # pylint: disable=broad-except
            _log_dialog_error(exc)


def _log_dialog_error(exc):
    """A torn-down progress dialog must never break the fleet; just log it."""
    _core.xbmc.log(
        "NeNeTeePee-Stream-Kodi: NZBGet fleet progress dialog error: {}".format(exc),
        _core.xbmc.LOGDEBUG,
    )
