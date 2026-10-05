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
from resources.lib.nzbget_fleet_dedup import FleetDedup


def submit_fleet(ctx, nzb_url, title, dupe_key):
    """Download, dedupe, and send the pick plus its backups; ``(nzbid, error)``.

    ``nzbid`` is the pick's NZBGet id (None when its append failed, with
    NZBGet's ``error``). A user cancel sets ``ctx.cancel_event`` and returns
    ``(None, None)``; anything already appended is in ``ctx.submitted_nzbids``
    for the caller to delete.
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
    if _core._dupe_check_disabled(getter):
        # Same-key items would download in parallel instead of parking as
        # backups: send the pick alone.
        _core.xbmc.log(
            "NeNeTeePee-Stream-Kodi: NZBGet DupeCheck=no -- skipping #372 duplicate "
            "backups (they would download in parallel).",
            _core.xbmc.LOGINFO,
        )
    else:
        _core._warn_if_healthcheck_pauses(getter)
        candidates += _fleet_backups(dupe)
    if progress.canceled():
        return None, None
    cap = dupe.get("max_backups")
    capped = isinstance(cap, int) and cap > 0
    # The pick counts as one live slot; a capped fleet probes each append for a
    # DELETED/COPY veto so the next round can backfill it.
    limits = (
        (cap + 1, cap + 1 + _core._MAX_VETO_REPLACEMENTS) if capped else (None, None)
    )
    dedup = FleetDedup(spool_base=_core._fleet_spool_base(), progress=progress.update)
    _core._submit_candidates(
        candidates,
        dupe_key,
        getter,
        cancel_event=ctx.cancel_event,
        submitted_sink=ctx.submitted_nzbids,
        dedup=dedup,
        veto_probe=capped,
        limits=limits,
    )
    if ctx.cancel_event.is_set():
        return None, None
    nzbid = pick.get("_nzbid")
    return (nzbid, None) if nzbid else (None, pick.get("_append_error"))


def _fleet_backups(dupe):
    """The pick's backups in rank order: picker rows, then Hydra + loader extras.

    The extras are scored just below the picker rows and shared as
    ``dupe["extras"]`` for the completion ledger.
    """
    backups = list(dupe.get("backups") or [])
    extras = _core._extra_backups_from_loader(
        dupe.get("loader"),
        [backup.get("link") for backup in backups],
        limit=None,
        score_base=int(dupe.get("score_base") or 0) - len(backups) - 1,
        leading=_core._hydra_uploads_for_fleet(dupe),
        pick=dupe.get("pick"),
    )
    dupe["extras"] = extras
    return backups + extras


class _FleetProgress:
    """Drives the resolve's progress dialog and turns its cancel into an event."""

    def __init__(self, dialog, cancel_event):
        self._dialog = dialog
        self._cancel_event = cancel_event

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
