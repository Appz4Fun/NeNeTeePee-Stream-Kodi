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
from resources.lib.nzbget_fleet_dedup import FleetDedup, call_abortable


def submit_fleet(ctx, nzb_url, title, dupe_key):
    """Download, dedupe, and send the pick plus its backups; ``(nzbid, error)``.

    ``nzbid`` is the pick's NZBGet id (None when its append failed, with
    NZBGet's ``error``). ``ctx.preexisting_successes`` receives the same-key
    SUCCESS ids snapshotted before anything was submitted (None if unknown).
    A user cancel sets ``ctx.cancel_event`` and returns ``(None, None)``;
    anything already appended is in ``ctx.submitted_nzbids`` for the caller
    to delete.
    """
    dupe = ctx.dupe if isinstance(ctx.dupe, dict) else {}
    getter = ctx.settings_getter
    progress = _FleetProgress(ctx.dialog, ctx.cancel_event)
    progress.finding()
    pick = dict(
        dupe.get("pick") or {},
        link=nzb_url,
        title=title,
        score=int(dupe.get("pick_score") or 0),
        _is_pick=True,
    )
    candidates = [pick]
    dupe_check_off, ctx.preexisting_successes = _probe_nzbget_config(
        getter, progress, dupe_key
    )
    if progress.canceled():
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
        return None, None
    # The cap bounds the BACKUPS (the pick is never counted); a capped fleet
    # probes each append for a DELETED/COPY veto so the next round can
    # backfill that backup's slot.
    limits = (cap, cap + _core._MAX_VETO_REPLACEMENTS) if capped else (None, None)
    dedup = FleetDedup(spool_base=_core._fleet_spool_base(), progress=progress.update)
    run = (dupe_key, getter, ctx, dedup)
    live = _send_batch(run, candidates, limits, capped)
    if capped and not dupe_check_off and pick.get("_nzbid") and len(live) < cap:
        # Picker/Hydra rows collapsed, died, or were vetoed before filling the
        # cap: only now consult the loader, for exactly the open slots.
        more = _loader_extras(dupe, progress, candidates[1:])
        remaining = cap - len(live)
        if more and not progress.canceled():
            _send_batch(
                run,
                more,
                (remaining, remaining + _core._MAX_VETO_REPLACEMENTS),
                capped,
            )
    if ctx.cancel_event.is_set():
        return None, None
    nzbid = pick.get("_nzbid")
    return (nzbid, None) if nzbid else (None, pick.get("_append_error"))


def _probe_nzbget_config(getter, progress, dupe_key):
    """Pre-fleet NZBGet probes, abortably: ``(dupecheck_off, preexisting)``.

    The same-key preexisting-success snapshot (history), the ``DupeCheck=no``
    check, and the HealthCheck=Pause warning are RPCs that can each hang for
    the RPC timeout, so they run off-thread behind the cancel/shutdown-aware
    wait. The thread reads only a snapshot of the connection settings taken
    HERE on the resolve thread (no off-thread Kodi ``getSetting``). A canceled
    or failed probe returns ``(False, None)`` (DupeCheck assumed on; the poll
    then snapshots successes itself).
    """
    url, user, password, category = _core.nzbget_api._get_settings(getter)
    snapshot = {
        "nzbget_url": url,
        "nzbget_username": user,
        "nzbget_password": password,
        "nzbget_category": category,
    }

    def _snapshot_getter(key, default=""):
        return snapshot.get(key, default)

    def _probe():
        # Same-key successes BEFORE this fleet submits anything: one another
        # resolve lands while this fleet downloads/sends is not stale.
        preexisting = _core._preexisting_success_ids(dupe_key, _snapshot_getter)
        if _core._dupe_check_disabled(_snapshot_getter):
            return True, preexisting
        _core._warn_if_healthcheck_pauses(_snapshot_getter)
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

    def __init__(self, dialog, cancel_event):
        self._dialog = dialog
        self._cancel_event = cancel_event

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
        try:
            # ``is True``: Kodi returns real bools; anything else (a stub) is
            # not a cancel.
            if self._dialog is not None and self._dialog.iscanceled() is True:
                self._cancel_event.set()
            if _core.xbmc.Monitor().abortRequested() is True:
                self._cancel_event.set()
        except Exception as exc:  # pylint: disable=broad-except
            _log_dialog_error(exc)
        return self._cancel_event.is_set()

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
