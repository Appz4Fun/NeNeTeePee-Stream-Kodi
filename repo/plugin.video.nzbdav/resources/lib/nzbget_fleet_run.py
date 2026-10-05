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

The caller then polls the pick's download ("Downloading... 0%") as before.
Names that tests patch on ``nzbget_resolver`` are reached through ``_core``.
"""

import resources.lib.nzbget_resolver as _core  # noqa: F401  pylint: disable=unused-import
from resources.lib.fallback_streams import _MAX_FALLBACKS
from resources.lib.nzbget_fleet_dedup import FleetDedup, call_abortable


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
    pick = dict(
        dupe.get("pick") or {},
        link=nzb_url,
        title=title,
        score=int(dupe.get("pick_score") or 0),
        _is_pick=True,
    )
    candidates = [pick]
    # Every NZBGet call below runs off-thread (abortable): read the connection
    # settings ONCE here on the resolve thread -- no off-thread getSetting.
    getter = _snapshot_getter(getter)
    dupe_check_off, ctx.preexisting_successes = _probe_nzbget_config(
        getter, progress, dupe_key
    )
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
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
    run = (dupe_key, getter, ctx, dedup)
    live = len(_send_batch(run, candidates, limits, capped))
    if capped and not dupe_check_off and pick.get("_nzbid"):
        _fill_from_loader(dupe, progress, run, (cap, live, candidates[1:]))
    ctx.fleet_aborted = progress.aborted
    if ctx.cancel_event.is_set():
        return None, None
    nzbid = pick.get("_nzbid")
    return (nzbid, None) if nzbid else (None, pick.get("_append_error"))


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
        if not more or progress.canceled():
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


def _probe_nzbget_config(getter, progress, dupe_key):
    """Pre-fleet NZBGet probes, abortably: ``(dupecheck_off, preexisting)``.

    The same-key preexisting-success snapshot (history), the ``DupeCheck=no``
    check, and the HealthCheck=Pause warning are RPCs that can each hang for
    the RPC timeout, so they run off-thread behind the cancel/shutdown-aware
    wait, reading only ``getter`` (the caller's settings snapshot). A canceled
    or failed probe returns ``(False, None)`` (DupeCheck assumed on; the poll
    then snapshots successes itself).
    """

    def _probe():
        # Same-key successes BEFORE this fleet submits anything: one another
        # resolve lands while this fleet downloads/sends is not stale.
        preexisting = _core._preexisting_success_ids(dupe_key, getter)
        if _core._dupe_check_disabled(getter):
            return True, preexisting
        _core._warn_if_healthcheck_pauses(getter)
        return False, preexisting

    return call_abortable(
        _probe, (progress.cancel_event,), progress.canceled, default=(False, None)
    )


def _send_batch(run, candidates, limits, capped):
    """One download-dedupe-send pass of ``_submit_candidates``; LIVE backup ids."""
    dupe_key, getter, ctx, dedup = run
    return _core._submit_candidates(
        candidates,
        dupe_key,
        getter,
        cancel_event=ctx.cancel_event,
        submitted_sink=ctx.submitted_nzbids,
        dedup=dedup,
        veto_probe=capped,
        limits=limits,
    )


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
