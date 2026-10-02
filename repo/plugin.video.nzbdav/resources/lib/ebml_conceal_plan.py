# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
#
# The replacement strategy (Void elements sized to valid boundaries, popping
# out through enclosing masters, resuming at a verified Cluster) is adapted
# from StreamNZB's pkg/media/ebml (GPL-3.0, commit 242a9c5d), reworked for a
# forward-only HTTP proxy that cannot rewrite bytes it has already sent.

"""Concealment planning and the per-session plan store.

:func:`plan_concealment` turns a tracker snapshot plus an unreadable span into
a :class:`ConcealPlan`; :class:`ConcealPlanStore` keeps committed plans so
repeated ranges replay identical bytes. See :mod:`resources.lib.ebml_conceal`
for the overall design and its limitations. Pure Python, no Kodi imports.
Split out of ``ebml_conceal`` to keep that module under the file-size budget;
every public name here is re-exported from ``resources.lib.ebml_conceal`` so
existing callers and tests keep resolving.
"""

import collections
import threading

from resources.lib.ebml_conceal_primitives import (
    ID_BLOCK,
    ID_BLOCKDURATION,
    ID_BLOCKVIRTUAL,
    ID_CODECSTATE,
    ID_CRC32,
    ID_DISCARDPADDING,
    ID_ENCRYPTEDBLOCK,
    ID_POSITION,
    ID_PREVSIZE,
    ID_REFERENCEBLOCK,
    ID_REFERENCEFRAME,
    ID_REFERENCEPRIORITY,
    ID_REFERENCEVIRTUAL,
    ID_SEGMENT,
    ID_SIMPLEBLOCK,
    ID_TIMESTAMP,
    ID_VOID,
    void_header,
)

KIND_PAYLOAD = "payload"
KIND_VOID = "void"
KIND_UNREPAIRED = "unrepaired"

# Leaves whose payload is opaque media data or a plain number: zero padding
# inside them damages content, never container structure.
_PAYLOAD_SAFE_IDS = frozenset(
    (
        ID_SIMPLEBLOCK,
        ID_BLOCK,
        ID_BLOCKVIRTUAL,
        ID_ENCRYPTEDBLOCK,
        ID_CODECSTATE,
        ID_VOID,
        ID_CRC32,
        ID_TIMESTAMP,
        ID_POSITION,
        ID_PREVSIZE,
        ID_BLOCKDURATION,
        ID_REFERENCEPRIORITY,
        ID_REFERENCEBLOCK,
        ID_REFERENCEVIRTUAL,
        ID_DISCARDPADDING,
        ID_REFERENCEFRAME,
    )
)

# A track-number VINT for track 1. Written only when the gap swallowed a
# block's first payload byte (its track number): 0x00 is not a VINT, so a
# strict demuxer would reject the block even though its size is intact.
_BLOCK_TRACK_FIX = b"\x81"

# Upper bound on one concealed span, readable bytes sacrificed for resync
# included. The proxy's look-ahead budget is tighter; this is the backstop.
DEFAULT_MAX_SPAN = 48 * 1024 * 1024


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class ConcealPlan:  # pylint: disable=too-few-public-methods
    """Synthetic bytes for ``[start, end)``: zeros overlaid with ``edits``.

    ``edits`` is a tuple of ``(absolute_offset, bytes)``. The output for any
    sub-range is a pure function of the plan, so every range request that
    covers part of a plan gets identical bytes.
    """

    __slots__ = ("kind", "start", "end", "edits", "reason")

    def __init__(self, kind, start, end, edits, reason):
        self.kind = kind
        self.start = start
        self.end = end
        self.edits = tuple(edits)
        self.reason = reason

    @property
    def structural(self):
        return self.kind != KIND_UNREPAIRED

    @property
    def span(self):
        return self.end - self.start

    def render(self, offset, length):
        if length <= 0:
            return b""
        if offset < self.start or offset + length > self.end:
            raise ValueError("render outside plan span")
        out = bytearray(length)
        stop = offset + length
        for edit_offset, edit_bytes in self.edits:
            lo = max(edit_offset, offset)
            hi = min(edit_offset + len(edit_bytes), stop)
            if lo < hi:
                out[lo - offset : hi - offset] = edit_bytes[
                    lo - edit_offset : hi - edit_offset
                ]
        return bytes(out)

    def __repr__(self):
        return "ConcealPlan({}, {}-{}, edits={}, reason={})".format(
            self.kind, self.start, self.end, len(self.edits), self.reason
        )


class _Gap(collections.namedtuple("_Gap", "start end max_span")):
    """The unreadable span being planned and the bound on the whole plan."""

    __slots__ = ()

    def unrepaired(self, reason):
        """Plain zero padding over exactly the gap (today's behaviour)."""
        return ConcealPlan(KIND_UNREPAIRED, self.start, self.end, (), reason)


def _void_edit(edits, start, end):
    """Append a Void edit covering ``[start, end)``; False if impossible."""
    span = end - start
    if span == 0:
        return True
    header = void_header(span)
    if header is None:
        return False
    edits.append((start, header))
    return True


def _precheck_reason(snapshot, gap):
    """Why no structural plan is possible at all, or None to go on planning."""
    if gap.end <= gap.start:
        return "empty_gap"
    if not snapshot.synced:
        return "no_structural_context"
    if snapshot.pos != gap.start:
        return "position_mismatch"
    if snapshot.partial_header:
        # Part of this element's header is already on the wire; a Void would
        # have to start before gap_start.
        return "header_already_emitted"
    masters = snapshot.masters
    if not masters or masters[0].id != ID_SEGMENT:
        return "outside_segment"
    return None


def _swallows_track_number(leaf, gap_start):
    """The gap starts on a block's first payload byte (its track number)."""
    return (
        leaf.id in (ID_SIMPLEBLOCK, ID_BLOCK)
        and gap_start == leaf.payload_start
        and leaf.end - leaf.payload_start >= 4
    )


def _plan_leaf(leaf, gap, edits):
    """Handle a gap that starts inside a leaf's payload.

    Returns ``(plan, None)`` when the leaf alone settles the outcome, else
    ``(None, boundary)`` with the first element boundary at or after the gap.
    """
    if leaf is None:
        return None, gap.start
    if leaf.id not in _PAYLOAD_SAFE_IDS:
        return gap.unrepaired("opaque_element"), None
    if _swallows_track_number(leaf, gap.start):
        edits.append((gap.start, _BLOCK_TRACK_FIX))
    if leaf.end >= gap.end:
        plan = ConcealPlan(KIND_PAYLOAD, gap.start, gap.end, edits, "payload_only")
        return plan, None
    return None, leaf.end


def _plan_masters(masters, boundary, gap, edits):
    """Void from ``boundary`` to the end of each enclosing sized master.

    Walks outward from the innermost master below the Segment. Returns
    ``(plan, None)`` once a master's end covers the gap (or a Void cannot be
    placed), else ``(None, boundary)`` to resume at the next verified Cluster.
    """
    for master in reversed(masters[1:]):
        if master.end is None:
            # Unknown-size Cluster: the next verified Cluster both ends it and
            # is the resume point, so the Void runs straight there.
            break
        if not _void_edit(edits, boundary, master.end):
            return gap.unrepaired("void_slot_too_small"), None
        if master.end >= gap.end:
            if master.end - gap.start > gap.max_span:
                return gap.unrepaired("span_exceeds_bound"), None
            plan = ConcealPlan(
                KIND_VOID, gap.start, master.end, edits, "void_to_master_end"
            )
            return plan, None
        boundary = master.end
    return None, boundary


def _resume_refusal(resume, why, search_from, segment_end, gap):
    """Why the look-ahead's resume offset is unusable, or None if it is."""
    if resume is None:
        return "no_verified_cluster:{}".format(why)
    if resume < search_from or (segment_end is not None and resume >= segment_end):
        return "no_verified_cluster:out_of_bounds"
    if resume - gap.start > gap.max_span:
        return "span_exceeds_bound"
    return None


def _plan_resume(snapshot, boundary, gap, edits, find_cluster):
    """Void from ``boundary`` to the next verified Cluster past the gap."""
    if not snapshot.seen_cluster:
        # Still in the header region (SeekHead/Info/Tracks): voiding there
        # would delete track metadata, not conceal media.
        return gap.unrepaired("header_region")
    search_from = max(boundary, gap.end)
    if search_from - gap.start >= gap.max_span:
        return gap.unrepaired("span_exceeds_bound")
    if find_cluster is None:
        return gap.unrepaired("no_verified_cluster:no_lookahead")
    segment_end = snapshot.masters[0].end
    resume, why = find_cluster(search_from, segment_end)
    reason = _resume_refusal(resume, why, search_from, segment_end, gap)
    if reason is None and not _void_edit(edits, boundary, resume):
        reason = "void_slot_too_small"
    if reason is not None:
        return gap.unrepaired(reason)
    return ConcealPlan(KIND_VOID, gap.start, resume, edits, "void_to_next_cluster")


def plan_concealment(
    snapshot, gap_start, gap_end, find_cluster=None, max_span=DEFAULT_MAX_SPAN
):
    """Plan the bytes for an unreadable span ``[gap_start, gap_end)``.

    ``snapshot`` is the tracker state at ``gap_start`` (every byte before it
    has been sent and can no longer change). ``gap_end`` is where the proxy's
    forward probe found readable data again: a coarse bound, not an exact map
    of the missing bytes. ``find_cluster(from_offset, limit)`` performs the
    bounded look-ahead and returns ``(offset_or_None, reason)``; ``limit`` is
    the enclosing Segment's end when known, else None.

    Returns a :class:`ConcealPlan` that starts at ``gap_start``. A structural
    plan may end beyond ``gap_end`` (at the resume boundary); every edit lies
    inside the plan. An unrepaired plan always spans exactly the gap.
    """
    gap = _Gap(gap_start, gap_end, max_span)
    reason = _precheck_reason(snapshot, gap)
    if reason is not None:
        return gap.unrepaired(reason)
    edits = []
    plan, boundary = _plan_leaf(snapshot.leaf, gap, edits)
    if plan is None:
        plan, boundary = _plan_masters(snapshot.masters, boundary, gap, edits)
    if plan is None:
        plan = _plan_resume(snapshot, boundary, gap, edits, find_cluster)
    return plan


# ---------------------------------------------------------------------------
# Per-session plan store
# ---------------------------------------------------------------------------


class ConcealPlanStore:
    """Committed structural plans of one session, keyed by source identity.

    Repeated or overlapping range requests replay a stored plan byte for byte
    so the client never sees two different versions of the same offsets.
    Plans are scoped to the source they were computed from: after a fallback
    cutover the new source starts with no plans. Bounded in count (oldest
    evicted first); plans never overlap within one source.
    """

    def __init__(self, max_plans=64):
        self._lock = threading.Lock()
        self._max_plans = max_plans
        self._plans = collections.OrderedDict()  # (key, start) -> plan

    def add(self, source_key, plan):
        if not plan.structural or plan.span <= 0:
            return False
        with self._lock:
            for (key, _start), other in self._plans.items():
                overlaps = plan.start < other.end and other.start < plan.end
                if key == source_key and overlaps:
                    return False
            self._plans[(source_key, plan.start)] = plan
            while len(self._plans) > self._max_plans:
                self._plans.popitem(last=False)
        return True

    def covering(self, source_key, offset):
        with self._lock:
            for (key, _start), plan in self._plans.items():
                if key == source_key and plan.start <= offset < plan.end:
                    return plan
        return None

    def next_start(self, source_key, offset):
        """Smallest plan start strictly after ``offset`` for this source."""
        best = None
        with self._lock:
            for key, start in self._plans:
                if key != source_key or start <= offset:
                    continue
                if best is None or start < best:
                    best = start
        return best

    def plan_count(self):
        with self._lock:
            return len(self._plans)
