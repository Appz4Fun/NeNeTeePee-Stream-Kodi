# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""One deduplicated appendfleet submission; server owns ranking and failover."""

import base64

from resources.lib import nzbget_api
from resources.lib.nzbget_fleet_dedup import (
    FleetDedup,
    NzbSpool,
    call_abortable,
    prefetched_clusters,
)


def submit_bulk(ctx, nzb_url, title, dupe_key):
    """Collect at most 50 unique postings and send exactly one health-ranked fleet."""
    from resources.lib import nzbget_fleet_run as run
    from resources.lib import nzbget_resolver as core

    dupe = ctx.dupe or {}
    stop = dupe.get("loader_stop")
    progress = run._FleetProgress(
        ctx.dialog, ctx.cancel_event, on_cancel=stop.set if stop is not None else None
    )
    getter = run._snapshot_getter(ctx.settings_getter)
    ctx.bulk_fleet = True
    progress.finding()
    existing = call_abortable(
        lambda: _existing(ctx, getter, dupe_key),
        (ctx.cancel_event,),
        progress.canceled,
        default=None,
        deadline=20,
    )
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
    if existing:
        ctx.fleet_pick_nzbid = existing
        return existing, None
    if getattr(ctx, "bulk_completed", None):
        return None, None
    pick = dict(dupe.get("pick") or {}, link=nzb_url, title=title, _is_pick=True)
    candidates = [pick] + run._fleet_backups(dupe, progress, ranked=False)
    cap = dupe.get("max_backups")
    count = min(50, cap + 1) if isinstance(cap, int) and cap >= 0 else 50
    dedup = FleetDedup(spool_base=core._fleet_spool_base(), progress=progress.update)
    dedup.aborted = lambda: progress.aborted
    try:
        result = _collect_send(
            ctx, candidates, count, (dupe_key, getter, progress, dedup)
        )
    finally:
        dedup.close()
    progress.canceled()
    ctx.fleet_aborted = progress.aborted
    return result


def _existing(ctx, getter, key):
    """Reuse same-key SUCCESS or queued work before paying indexer grabs."""
    history = nzbget_api.history_rows(getter)
    ctx.preexisting_successes = (
        nzbget_api.success_ids_by_dupekey(key, getter, history=history)
        if history is not None
        else None
    )
    for row in history or []:
        entry = nzbget_api._success_history_entry(row, key)
        if entry:
            ctx.bulk_completed = entry
            return None
    queue = nzbget_api.queue_rows(getter)
    for row in queue or []:
        if nzbget_api._dupekey_match(row, key):
            nzbid = nzbget_api._int_nzbid(row.get("NZBID"))
            if nzbid:
                ctx.adopted_nzbids = [nzbid]
                return nzbid
    return None


def _collect_send(ctx, candidates, count, state):
    """Use the existing streaming collector; spool stays alive through the RPC."""
    from resources.lib import nzbget_resolver_dupes as engine

    key, getter, progress, dedup = state
    seen = set()
    usable = []
    for row in candidates:
        link = engine._usable_backup_link(row, seen)
        if link:
            seen.add(link)
            usable.append(row)
    clusters = dedup.clusters(usable)
    spool = NzbSpool(dedup.spool_base)
    fetch = engine._fleet_fetcher(clusters, spool)
    tally = {"wanted": count, "seen": 0, "total": len(clusters)}
    stream = prefetched_clusters(
        clusters,
        fetch,
        ctx.cancel_event,
        demand=lambda: tally["wanted"],
        on_wait=progress.canceled,
    )
    try:
        kept = engine._collect_unique(
            stream, (dedup, spool, tally, fetch), ctx.cancel_event, count
        )
        if progress.canceled():
            return None, None
        if not kept or kept[0][1] is None:
            return None, "NZB download failed"
        return _send(ctx, kept, (key, getter, progress, dedup))
    finally:
        stream.close()
        spool.close()


def _send(ctx, kept, state):
    """Build ordered payload off-thread, retain bodies for an explicit fallback."""
    from resources.lib import nzbget_fleet_run as run

    key, getter, progress, dedup = state
    progress.update("send", 0, len(kept))

    def send():
        members = [
            {
                "NZBFilename": "{}.nzb".format(row.get("title") or "submission"),
                **(
                    {"ContentPath": handle}
                    if isinstance(handle, str)
                    else {
                        "Content": base64.b64encode(NzbSpool.load(handle)).decode(
                            "ascii"
                        )
                    }
                ),
            }
            for row, handle, _token in kept
        ]
        if ctx.cancel_event.is_set():
            return None, None
        return nzbget_api.append_fleet(members, key, getter)

    reply, error = call_abortable(
        send,
        (ctx.cancel_event,),
        progress.canceled,
        default=(None, "Fleet submission interrupted"),
        on_late=lambda result: _late(result, getter, progress),
    )
    if progress.canceled():
        # The RPC may finish in the same slice that observes cancellation;
        # call_abortable then returns normally rather than invoking on_late.
        if reply and not progress.aborted:
            from resources.lib import nzbget_resolver as core

            core._cancel_jobs_in_background(_live_ids(reply), getter)
        return None, None
    if nzbget_api.is_unknown_method(error):
        ctx.bulk_fleet = False
        pick = kept[0][0]
        return run._submit_legacy_fleet(
            ctx, pick["link"], pick["title"], key, prepared=(kept, dedup)
        )
    if error or not reply:
        return None, error or "Invalid appendfleet response"
    ctx.bulk_reply = reply
    chosen = reply["Chosen"]
    if not chosen:
        if reply.get("Reason") == "ALREADY_DOWNLOADED":
            ctx.bulk_completed = nzbget_api.history_success_by_dupekey(
                key, settings_getter=getter
            )
            return None, (
                None
                if ctx.bulk_completed.get("present")
                else (
                    "NZBGet reports this release already downloaded, "
                    "but its completed file could not be found"
                )
            )
        return None, (
            "NZBGet found no working copy"
            if reply.get("Reason") == "ALL_DEAD"
            else "NZBGet queued no working copy"
        )
    ctx.submitted_nzbids.extend(_live_ids(reply))
    ctx.fleet_pick_nzbid = chosen
    _health_message(progress, reply)
    # Rows are returned in health rank, not request order. Do not assign IDs
    # positionally to input titles or the submission ledger.
    return chosen, None


def _live_ids(reply):
    """Only queued/backup IDs belong to cancellation and failover tracking."""
    ids = [
        row.get("NZBID")
        for row in reply.get("Members", [])
        if row.get("Status") in ("QUEUED", "BACKUP")
    ]
    ids.append(reply.get("Chosen"))
    return list(
        dict.fromkeys(
            value
            for value in ids
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
        )
    )


def _late(result, getter, progress):
    """A fleet accepted after user cancel is cleaned up; shutdown leaves it running."""
    reply, _error = result
    if reply and not progress.aborted:
        nzbget_api.cancel_jobs(_live_ids(reply), settings_getter=getter)


def _health_message(progress, reply):
    """Show server health only when it is known, never present -1 as a percent."""
    rows = reply.get("Members", [])
    good = sum(row.get("Status") in ("QUEUED", "BACKUP") for row in rows)
    chosen = next((row for row in rows if row.get("NZBID") == reply["Chosen"]), {})
    alive = chosen.get("Alive", -1)
    from resources.lib import nzbget_resolver as core

    health = (
        core._fmt(30619, alive)
        if isinstance(alive, (int, float)) and alive >= 0
        else ""
    )
    progress._update(0, core._fmt(30618, good, health))
