# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
# pylint: disable=cyclic-import

"""EBML-aware gap concealment for the original-byte pass-through path.

Mixin for ``stream_proxy._StreamHandler``. It plugs the pure planner in
``ebml_conceal`` into ``_serve_proxy``:

* **Tap** - for MKV/WebM sessions the response ``wfile`` is wrapped for the
  request so every byte actually sent (prefix, read-ahead, upstream, synthetic)
  feeds an :class:`ebml_conceal.EbmlStreamTracker`. The tracker models what the
  client received, which is what any replacement has to stay consistent with.
* **Plan** - in the zero-fill step, after the retry ladder, live fallback
  cutover, stall wait and skip-probe have all run, the unreadable span is
  planned structurally. Only a structural plan that fits every zero-fill budget
  (charged its WHOLE span, including readable bytes skipped to resync) is
  used; anything else falls back to the legacy literal zero-fill unchanged.
* **Replay** - committed plans live on the session ctx, scoped to the source
  URL they were computed from. Later requests replay a plan byte for byte and
  bound their upstream reads to stop at a plan's start, so repeated and
  overlapping ranges always see identical bytes at identical offsets.

Look-ahead reads go straight to the active upstream (original bytes); the tap
only ever sees client output, so fingerprint/identity probes are unaffected.
"""

import resources.lib.stream_proxy as _sp  # noqa: E402
from resources.lib import ebml_conceal as _ec  # noqa: E402


def _close_quietly(resp):
    """Close a look-ahead response, ignoring socket errors on close."""
    try:
        resp.close()
    except OSError:
        # The scan is already finished or abandoned and nothing reads this
        # socket again, so a reset while closing it changes nothing.
        pass


class _EbmlTapWriter:  # pylint: disable=too-few-public-methods
    """``wfile`` proxy that feeds every successfully written byte to a tracker."""

    def __init__(self, inner, tracker):
        self._inner = inner
        self._tracker = tracker
        self.broken = False

    def write(self, data):
        result = self._inner.write(data)
        if not self.broken:
            try:
                self._tracker.feed(data)
            except Exception as exc:  # pylint: disable=broad-except
                # A tracker bug must never cost playback: stop tracking (no
                # snapshot is trusted again) and keep streaming.
                self.broken = True
                _sp.xbmc.log(
                    "NZB-DAV: EBML tracker disabled after error: {!r} "
                    "(reason=ebml_tracker_error)".format(exc),
                    _sp.xbmc.LOGWARNING,
                )
        return result

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ConcealRequest:  # pylint: disable=too-few-public-methods
    """Per-request concealment state: the tracker and the unwrapped wfile."""

    __slots__ = ("tracker", "tap", "inner_wfile")

    def __init__(self, tracker, tap, inner_wfile):
        self.tracker = tracker
        self.tap = tap
        self.inner_wfile = inner_wfile


class _EbmlConcealMixin:  # pylint: disable=too-few-public-methods
    """EBML tap, structural concealment, and plan replay for pass-through."""

    # -- lifecycle ---------------------------------------------------------

    @staticmethod
    def _ebml_conceal_candidate(ctx):
        """Whether this session may be Matroska/WebM (bytes decide for sure)."""
        if not isinstance(ctx, dict) or ctx.get(_sp._EBML_CONTAINER_KEY) is False:
            return False
        content_type = (ctx.get("content_type") or "").lower()
        if "matroska" in content_type or "webm" in content_type:
            return True
        path = str(ctx.get("remote_url") or "").split("?", 1)[0].lower()
        return path.endswith(_sp._EBML_CONCEAL_EXTENSIONS)

    def _ebml_conceal_begin(self, ctx, st):
        """Wrap ``wfile`` with the tracker tap for a candidate session."""
        st.conceal = None
        if not self._ebml_conceal_candidate(ctx):
            return
        tracker = _ec.EbmlStreamTracker(st.start, int(ctx.get("content_length") or 0))
        inner = self.wfile
        tap = _EbmlTapWriter(inner, tracker)
        self.wfile = tap
        st.conceal = _ConcealRequest(tracker, tap, inner)

    def _ebml_conceal_end(self, ctx, st):
        """Restore ``wfile`` and remember a definitive container verdict."""
        cs = st.conceal
        if cs is None:
            return
        self.wfile = cs.inner_wfile
        if cs.tracker.ebml_detected is False and isinstance(ctx, dict):
            ctx[_sp._EBML_CONTAINER_KEY] = False

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _ebml_source_key(active_ctx):
        return str(active_ctx.get("remote_url") or "")

    @staticmethod
    def _ebml_store(ctx, create=False):
        if not isinstance(ctx, dict):
            return None
        store = ctx.get(_sp._EBML_CONCEAL_STORE_KEY)
        if store is None and create:
            store = ctx.setdefault(_sp._EBML_CONCEAL_STORE_KEY, _ec.ConcealPlanStore())
        return store

    def _ebml_emit_plan(self, st, plan, emit_end):
        """Write ``plan`` bytes for ``[st.current, emit_end)`` and account them."""
        chunk = len(_sp._ZERO_FILL_BUFFER)
        offset = st.current
        while offset < emit_end:
            length = min(chunk, emit_end - offset)
            self.wfile.write(plan.render(offset, length))
            offset += length
        emitted = emit_end - st.current
        st.total_skipped += emitted
        st.current = emit_end
        _sp._record_density_window(st.density_window, "zero_fill", emitted)
        return emitted

    # -- loop steps --------------------------------------------------------

    def _serve_proxy_conceal_replay_step(self, ctx, st):
        """Replay a stored plan covering ``current``; bound the next read.

        Runs first in every loop iteration. Sets ``st.read_end`` so the
        upstream read and retry ladder stop before the next stored plan (whose
        bytes must come from the plan, not from upstream, to stay identical
        to what an earlier response sent).
        """
        st.read_end = st.end
        store = self._ebml_store(ctx)
        if store is None:
            return None
        key = self._ebml_source_key(st.active_ctx)
        plan = store.covering(key, st.current)
        if plan is not None:
            emit_end = min(plan.end, st.end + 1)
            start = st.current
            emitted = self._ebml_emit_plan(st, plan, emit_end)
            _sp.xbmc.log(
                "NZB-DAV: Replayed EBML concealment {}-{} ({} bytes, kind={}) "
                "for a repeated range (reason=ebml_conceal_replay)".format(
                    start, emit_end, emitted, plan.kind
                ),
                _sp.xbmc.LOGINFO,
            )
            if st.current > st.end:
                st.terminal_reason = "complete"
                return "return"
            return "continue"
        next_start = store.next_start(key, st.current)
        if next_start is not None:
            st.read_end = min(st.end, next_start - 1)
        return None

    def _ebml_read_bound(self, ctx, st):
        """Last byte readable from upstream before the next stored plan."""
        store = self._ebml_store(ctx)
        if store is None:
            return st.end
        key = self._ebml_source_key(st.active_ctx)
        if store.covering(key, st.current) is not None:
            return st.current - 1
        next_start = store.next_start(key, st.current)
        return st.end if next_start is None else min(st.end, next_start - 1)

    def _serve_proxy_bounded_read_done(self, ctx, st):
        """A read bounded by a stored plan finished cleanly: loop to replay it."""
        if st.result == _sp._UPSTREAM_RANGE_OK and st.current <= st.end:
            self._serve_proxy_progress_step(ctx, st)
            return "continue"
        return None

    def _ebml_refresh_read_bound(self, ctx, st):
        """Re-read the plan store after concealment was not committed.

        Planning (and its look-ahead) can take seconds, and a concurrent
        request on this session may store a plan meanwhile. Returns True when
        a stored plan now covers ``current`` (the loop's replay step must emit
        it); otherwise ``st.read_end`` stops short of the next plan so the
        legacy zero-fill can be clipped before it.
        """
        st.read_end = self._ebml_read_bound(ctx, st)
        return st.read_end < st.current

    def _ebml_clip_skip(self, st, skip):
        """Never let a legacy zero-fill run into a stored plan's span."""
        if skip is None or st.read_end >= st.end:
            return skip
        limit = st.read_end + 1 - st.current
        return min(skip, limit) if limit > 0 else skip

    # -- planning ----------------------------------------------------------

    def _ebml_budget_refusal(self, ctx, st, span):
        """Name the zero-fill budget the full concealed span would breach."""
        if (
            st.zero_fill_budget_enabled
            and st.total_skipped + span > _sp._MAX_TOTAL_ZERO_FILL
        ):
            return "response_budget"
        if st.density_breaker_enabled and _sp._would_trip_density_breaker(
            st.density_window, span
        ):
            return "density_breaker"
        if st.zero_fill_budget_enabled:
            _, _, ratio = _sp._project_session_zero_fill_ratio(
                self.server, ctx, extra_zero_fill=span, extra_recoveries=1
            )
            if ratio > _sp._SESSION_ZERO_FILL_RATIO_MAX:
                return "session_budget"
        return None

    def _ebml_log_unrepaired(self, st, skip, detail):
        _sp.xbmc.log(
            "NZB-DAV: EBML concealment not applied at byte {} (skip={}, "
            "detail={}); using plain zero-fill, container structure NOT "
            "repaired (reason=ebml_conceal_unrepaired)".format(
                st.current, skip, detail
            ),
            _sp.xbmc.LOGWARNING,
        )

    def _ebml_try_conceal(self, ctx, st, skip):
        """Conceal ``[current, current + skip)`` structurally if safe.

        Returns True when a structural plan was committed and emitted (the
        caller must skip the legacy zero-fill), False to fall back to it.
        """
        cs = st.conceal
        if cs is None or cs.tap.broken or cs.tracker.ebml_detected is False:
            return False
        plan = self._ebml_plan_gap(cs, st, skip)
        if plan is None:
            return False
        store = self._ebml_store(ctx, create=True)
        key = self._ebml_source_key(st.active_ctx)
        refusal = self._ebml_commit_refusal(ctx, st, store, key, plan)
        if refusal is not None:
            self._ebml_log_unrepaired(st, skip, refusal)
            return False
        if not store.add(key, plan):
            return self._ebml_replay_concurrent(st, store, key, skip)
        # Charge and log at commit, before the write (see _ebml_charge_plan).
        self._ebml_charge_plan(ctx, st, plan, skip)
        emit_end = min(plan.end, st.end + 1)
        self._ebml_emit_plan(st, plan, emit_end)
        self._ebml_self_check(cs, plan, emit_end)
        return True

    def _ebml_plan_gap(self, cs, st, skip):
        """Plan the gap from the tracker snapshot; None (logged) if unrepaired."""
        active_ctx = st.active_ctx

        def find_cluster(from_offset, limit):
            return self._ebml_lookahead_find_cluster(active_ctx, from_offset, limit)

        snapshot = cs.tracker.snapshot()
        plan = _ec.plan_concealment(
            snapshot,
            st.current,
            st.current + skip,
            find_cluster=find_cluster,
            max_span=_sp._EBML_CONCEAL_MAX_SPAN,
        )
        if plan.structural:
            return plan
        detail = plan.reason
        if plan.reason == "no_structural_context" and snapshot.desync_reason:
            detail = "{} ({})".format(plan.reason, snapshot.desync_reason)
        self._ebml_log_unrepaired(st, skip, detail)
        return None

    def _ebml_commit_refusal(self, ctx, st, store, key, plan):
        """Why ``plan`` must not be committed (log detail), or None."""
        next_start = store.next_start(key, st.current)
        if next_start is not None and plan.end > next_start:
            return "overlaps_stored_plan"
        refusal = self._ebml_budget_refusal(ctx, st, plan.span)
        if refusal is not None:
            return "{} (span={})".format(refusal, plan.span)
        return None

    def _ebml_replay_concurrent(self, st, store, key, skip):
        """``store.add`` refused the plan: replay a concurrent twin if any.

        A concurrent request on this session committed a plan for the same
        gap first: replay it so both responses send identical bytes.
        """
        existing = store.covering(key, st.current)
        if existing is None:
            self._ebml_log_unrepaired(st, skip, "plan_store_refused")
            return False
        self._ebml_emit_plan(st, existing, min(existing.end, st.end + 1))
        return True

    def _ebml_charge_plan(self, ctx, st, plan, skip):
        """Charge a newly committed plan's whole span and log it.

        Charged once, at commit and before the write: replays are not
        re-charged, and the client may drop mid-write while the committed
        plan stays stored for later requests.
        """
        st.recovery_count += 1
        _sp._update_session_recovery_state(
            self.server, ctx, zero_fill=plan.span, recoveries=1
        )
        _sp._maybe_notify_recovery_summary(self.server, ctx)
        _sp.xbmc.log(
            "NZB-DAV: EBML-concealed unreadable span at byte {} (probe skip={}, "
            "kind={}, plan={}-{}, span={}, voids={}, detail={}) "
            "(reason=ebml_conceal_{})".format(
                st.current,
                skip,
                plan.kind,
                plan.start,
                plan.end,
                plan.span,
                len([e for e in plan.edits if e[1][:1] == b"\xec"]),
                plan.reason,
                plan.kind,
            ),
            _sp.xbmc.LOGWARNING,
        )

    @staticmethod
    def _ebml_self_check(cs, plan, emit_end):
        """After emitting a whole plan the tracker must be synced again."""
        if emit_end == plan.end and not cs.tracker.synced and not cs.tap.broken:
            _sp.xbmc.log(
                "NZB-DAV: EBML concealment self-check failed: tracker lost sync "
                "at resume byte {} ({}) (reason=ebml_conceal_selfcheck)".format(
                    plan.end, cs.tracker.desync_reason
                ),
                _sp.xbmc.LOGERROR,
            )

    # -- bounded look-ahead ------------------------------------------------

    @staticmethod
    def _ebml_lookahead_response_ok(resp, from_offset):
        """Only a 206 whose Content-Range starts at ``from_offset`` is usable."""
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 206:
            return False
        content_range = _sp._get_header(resp, "Content-Range")
        if not content_range:
            return True
        try:
            first = content_range.split()[1].split("-", 1)[0]
            return int(first) == from_offset
        except (IndexError, ValueError):
            return False

    def _ebml_lookahead_find_cluster(self, active_ctx, from_offset, limit):
        """One bounded forward read for the next verified Cluster.

        Bounded by ``_EBML_LOOKAHEAD_MAX_BYTES``, ``_EBML_LOOKAHEAD_MAX_SECONDS``
        and a single upstream request; abortable via ``abortRequested`` (never
        ``waitForAbort(0)``: Kodi treats a non-positive timeout as an infinite
        wait, which would wedge this handler thread). Every socket wait is
        capped by the remaining wall-clock budget. Returns
        ``(absolute_offset, "found")`` or ``(None, reason)``. Reads original
        upstream bytes only; nothing read here is sent to the client.
        """
        content_length = int(active_ctx.get("content_length") or 0)
        stop = self._ebml_lookahead_stop(from_offset, limit, content_length)
        if stop <= from_offset:
            return None, "window_empty"
        monitor = _sp.xbmc.Monitor()
        if monitor.abortRequested():
            return None, "aborted"
        budget = _sp._EBML_LOOKAHEAD_MAX_SECONDS
        deadline = _sp.time.monotonic() + budget
        try:
            resp = self._ebml_lookahead_open(active_ctx, from_offset, stop, budget)
        except (OSError, ValueError) as exc:
            return None, "open_failed:{}".format(type(exc).__name__)
        try:
            if not self._ebml_lookahead_response_ok(resp, from_offset):
                return None, "bad_range_response"
            return self._ebml_lookahead_scan(
                resp, monitor, deadline, from_offset, stop, content_length
            )
        except (MemoryError, OSError, ValueError) as exc:
            return None, "read_failed:{}".format(type(exc).__name__)
        finally:
            _close_quietly(resp)

    @staticmethod
    def _ebml_lookahead_stop(from_offset, limit, content_length):
        """Exclusive end of the look-ahead window (byte, Segment and file caps)."""
        stop = from_offset + _sp._EBML_LOOKAHEAD_MAX_BYTES
        if limit is not None:
            stop = min(stop, limit)
        if content_length:
            stop = min(stop, content_length)
        return stop

    @staticmethod
    def _ebml_lookahead_open(active_ctx, from_offset, stop, budget):
        """Open the single bounded Range request; raises OSError/ValueError."""
        req = _sp.Request(active_ctx["remote_url"])
        _sp._add_request_headers(req, active_ctx.get("auth_header"))
        req.add_header("Range", "bytes={}-{}".format(from_offset, stop - 1))
        # nosemgrep
        return (
            _sp.urlopen(  # nosec B310 — URL from user-configured nzbdav/WebDAV setting
                req, timeout=min(_sp._UPSTREAM_READ_TIMEOUT, budget)
            )
        )

    @staticmethod
    def _ebml_lookahead_scan(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        resp, monitor, deadline, from_offset, stop, content_length
    ):
        scanner = _ec.ClusterScanner(from_offset, content_length)
        want = stop - from_offset
        while scanner.scanned < want:
            remaining = deadline - _sp.time.monotonic()
            if remaining <= 0:
                return None, "deadline"
            if monitor.abortRequested():
                return None, "aborted"
            # A stalled upstream must not hold the read past the budget.
            _sp._set_upstream_read_timeout(resp, max(0.5, remaining))
            chunk = resp.read(min(_sp._UPSTREAM_READ_CHUNK, want - scanner.scanned))
            if not chunk:
                return None, "short_read"
            found = scanner.feed(chunk[: want - scanner.scanned])
            if found is not None:
                return found, "found"
        return None, "not_found"
