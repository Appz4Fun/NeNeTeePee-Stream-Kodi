# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""One deduplicated appendfleet submission; server owns ranking and failover."""

import base64
import threading
import time

from resources.lib import nzbget_api, nzbget_submit_ledger
from resources.lib.nzbget_fleet_dedup import (
    FleetDedup,
    NzbSpool,
    call_abortable,
    prefetched_clusters,
)
from resources.lib.nzbget_fleet_identity import member_filename, record_members


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
    give_up_at = time.monotonic() + 20
    abandoned = threading.Event()

    def stopped():
        return (
            abandoned.is_set()
            or ctx.cancel_event.is_set()
            or time.monotonic() >= give_up_at
        )

    probe = call_abortable(
        lambda: _existing(getter, dupe_key, stopped),
        (ctx.cancel_event,),
        progress.canceled,
        default={},
        deadline=20,
    )
    abandoned.set()
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
    # Only the waiting caller owns ctx. Abandoned workers return data and
    # cannot overwrite a later accepted fleet's playback/failover state.
    ctx.preexisting_successes = probe.get("successes")
    core._warn_if_healthcheck_pauses(getter, options=probe.get("options", {}))
    if probe.get("completed"):
        return _use_completed(ctx, probe["completed"], progress)
    existing = probe.get("chosen")
    if existing:
        ctx.adopted_nzbids = probe.get("adopted", [])
        ctx.fleet_pick_nzbid = existing
        return existing, None
    pick = dict(dupe.get("pick") or {}, link=nzb_url, title=title, _is_pick=True)
    # Discover the loader only after dedup proves the leading rows leave room.
    candidates = [pick] + run._fleet_backups(
        dupe, progress, include_loader=False, ranked=False
    )
    cap = dupe.get("max_backups")
    capped = isinstance(cap, int) and cap >= 0
    ctx.bulk_held = nzbget_submit_ledger.held(dupe_key, probe.get("members"))
    candidates, adopted = run._skip_held(
        candidates, ctx.bulk_held, ctx, slots=cap if capped else None
    )
    count = min(50, cap + 1 - len(adopted)) if capped else 50
    dedup = FleetDedup(spool_base=core._fleet_spool_base(), progress=progress.update)
    dedup.aborted = lambda: progress.aborted
    dedup.attempts_used = len(adopted)
    try:
        result = _collect_send(
            ctx, candidates, count, (dupe_key, getter, progress, dedup)
        )
    finally:
        dedup.close()
    progress.canceled()
    ctx.fleet_aborted = progress.aborted
    return result


def _existing(getter, key, stopped):
    """Return preflight data without ever mutating the live submit context."""
    result = {}
    if stopped():
        return result
    history = nzbget_api.history_rows(getter)
    result["successes"] = (
        nzbget_api.success_ids_by_dupekey(key, getter, history=history)
        if history is not None
        else None
    )
    if stopped():
        return result
    result["completed"] = next(
        (
            entry
            for entry in (
                nzbget_api._success_history_entry(row, key) for row in history or []
            )
            if entry
        ),
        None,
    )
    if result["completed"]:
        return result
    queue = nzbget_api.queue_rows(getter)
    if stopped():
        return result
    result["members"] = (
        nzbget_api.dupekey_member_states(key, history or [], queue)
        if queue is not None
        else None
    )
    matches = [row for row in queue or [] if nzbget_api._dupekey_match(row, key)]
    matches.sort(key=lambda row: str(row.get("Status") or "").upper() == "PAUSED")
    for row in matches:
        nzbid = nzbget_api._int_nzbid(row.get("NZBID"))
        if nzbid and nzbid > 0:
            states = nzbget_api.dupekey_member_states(key, history or [], queue)
            result["adopted"] = [
                job for job, state in states.items() if state in ("queued", "parked")
            ]
            result["chosen"] = nzbid
            break
    if not stopped():
        result["options"] = nzbget_api.config_options(("HealthCheck",), getter)
    return result


def _use_completed(ctx, completed, progress):
    """Reuse only readable files; never resubmit a server-confirmed download."""
    from resources.lib import nzbget_resolver as core

    url = core._reuse_completed_job(
        dict(completed, name=completed.get("name") or completed.get("job_name", "")),
        ctx,
    )
    if progress.canceled():
        ctx.fleet_aborted = progress.aborted
        return None, None
    if url is core.SMB_UNREADABLE:
        ctx.bulk_reuse_unreadable = True
        return None, None
    if url:
        ctx.bulk_completed = completed
        ctx.bulk_completed_url = url
        return None, None
    return None, core._string(30620)


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
    spool = NzbSpool(dedup.spool_base)
    try:
        kept = _collect_rows(usable, count, (ctx, dedup, spool, progress))
        if progress.canceled():
            return None, None
        if not kept or kept[0][1] is None:
            return None, "NZB download failed"
        remaining = count - len(kept)
        if remaining and (ctx.dupe or {}).get("loader") is not None:
            from resources.lib import nzbget_fleet_run as run

            dupe = ctx.dupe
            limit = dupe.get("loader_limit")
            if isinstance(limit, dict):
                limit["n"] = remaining
            more = run._loader_extras(dupe, progress, candidates[1:], ranked=False)
            candidates = list(candidates) + list(more)
            capped = (
                isinstance(dupe.get("max_backups"), int) and dupe["max_backups"] >= 0
            )
            more, adopted = run._skip_held(
                more,
                getattr(ctx, "bulk_held", None),
                ctx,
                slots=remaining if capped else None,
            )
            dedup.attempts_used = getattr(dedup, "attempts_used", 0) + len(adopted)
            if capped:
                remaining -= len(adopted)
            if remaining:
                kept += _collect_rows(more, remaining, (ctx, dedup, spool, progress))
        if progress.canceled():
            return None, None
        return _send(ctx, kept, (key, getter, progress, dedup), candidates)
    finally:
        spool.close()


def _collect_rows(rows, count, state):
    """Collect one bounded phase while sharing spool and posting fingerprints."""
    from resources.lib import nzbget_resolver_dupes as engine

    ctx, dedup, spool, progress = state
    clusters = dedup.clusters(rows)
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
        return engine._collect_unique(
            stream, (dedup, spool, tally, fetch), ctx.cancel_event, count
        )
    finally:
        stream.close()


def _send(ctx, kept, state, candidates):
    """Build ordered payload off-thread, retain bodies for an explicit fallback."""
    from resources.lib import nzbget_fleet_run as run

    key, getter, progress, dedup = state
    progress.update("send", 0, len(kept))

    def send():
        members = [
            {
                "NZBFilename": member_filename(row, number),
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
            for number, (row, handle, _token) in enumerate(kept, 1)
        ]
        if ctx.cancel_event.is_set():
            return None, None
        return nzbget_api.append_fleet(members, key, getter)

    reply, error = call_abortable(
        send,
        (ctx.cancel_event,),
        progress.canceled,
        default=(None, "Fleet submission interrupted"),
        on_late=lambda result: _late(result, getter, progress, kept, key),
    )
    mapped = record_members(reply, kept, key) if reply and reply.get("Chosen") else []
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
            ctx, pick["link"], pick["title"], key, prepared=(kept, dedup, candidates)
        )
    if error or not reply:
        return None, error or "Invalid appendfleet response"
    ctx.bulk_reply = reply
    chosen = reply["Chosen"]
    if not chosen:
        if reply.get("Reason") == "ALREADY_DOWNLOADED":
            completed = call_abortable(
                lambda: nzbget_api.history_success_by_dupekey(
                    key, settings_getter=getter
                ),
                (ctx.cancel_event,),
                progress.canceled,
                default={"present": False},
            )
            if completed.get("present"):
                return _use_completed(ctx, completed, progress)
            return (
                None,
                (
                    "NZBGet reports this release already downloaded, "
                    "but its completed file could not be found"
                ),
            )
        return None, (
            "NZBGet found no working copy"
            if reply.get("Reason") == "ALL_DEAD"
            else "NZBGet queued no working copy"
        )
    live_ids = _live_ids(reply)
    ctx.submitted_nzbids.extend(live_ids)
    # A server omitting echoed member identity cannot safely leave anonymous
    # parked backups for replay. Cancel deletes those fresh IDs in full.
    ctx.bulk_unrecorded_nzbids = [nzbid for nzbid in live_ids if nzbid not in mapped]
    ctx.fleet_pick_nzbid = chosen
    _health_message(progress, reply)
    # Rows are returned in health rank, not request order. Do not assign IDs
    # positionally to input titles or the submission ledger.
    return chosen, None


def _live_ids(reply):
    """Only queued/backup IDs belong to cancellation and failover tracking."""
    if not reply.get("Chosen"):
        return []
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


def _late(result, getter, progress, kept, key):
    """A fleet accepted after user cancel is cleaned up; shutdown leaves it running."""
    reply, _error = result
    if reply:
        if reply.get("Chosen"):
            record_members(reply, kept, key)
        if not progress.aborted:
            ids = _live_ids(reply)
            if nzbget_api.cancel_jobs(ids, settings_getter=getter):
                nzbget_submit_ledger.forget(ids)


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
