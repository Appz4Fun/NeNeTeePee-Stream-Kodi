# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
#
# The replacement strategy (Void elements sized to valid boundaries, popping
# out through enclosing masters, resuming at a verified Cluster) is adapted
# from StreamNZB's pkg/media/ebml (GPL-3.0, commit 242a9c5d), reworked for a
# forward-only HTTP proxy that cannot rewrite bytes it has already sent.

"""Matroska/WebM (EBML) aware concealment of unreadable pass-through spans.

When the pass-through proxy cannot read a span of the source (missing Usenet
articles), it historically wrote literal zeros. Inside a Matroska Cluster
those zeros land where a demuxer expects element headers; ``0x00`` is not a
valid EBML VINT, so strict demuxers abort and lenient ones resync blindly.

This module plans a *structural* replacement instead, without changing the
response length or any absolute offset:

* a span that stays inside one media element's payload is left as zero
  padding (the element's own size still tells the demuxer where it ends);
* otherwise correctly sized EBML ``Void`` elements are placed at valid
  boundaries: from the end of the damaged element to the end of each enclosing
  BlockGroup/Cluster, and, when the span escapes the Cluster, from there to the
  next *verified* Cluster found by a bounded look-ahead.

Everything is pure Python with no Kodi imports so it can be tested in
isolation. Structural context comes from :class:`EbmlStreamTracker`, which
parses only element headers of the bytes the proxy actually emitted (payloads
are skipped by size), so its look-behind state is a small element stack.

Conservative policy: whenever structure cannot be established, or a safe plan
would have to rewrite bytes already sent, :func:`plan_concealment` returns an
``unrepaired`` plan (plain zero padding, today's behaviour) with a reason. It
never reports an unrepaired span as structurally repaired.

Limitations: concealment hides lost media, it cannot reconstruct it. Frames
in the span are gone and decoders may show artefacts until the next keyframe.
A Cluster carrying a CRC-32 element will fail its checksum after any edit
(demuxers that enforce CRC drop that Cluster; ffmpeg only checks CRC with
``-err_detect crccheck``). Cues/SeekHead entries pointing into a concealed
span still point at the same offsets, which now hold Void or zero bytes.

Layout: EBML primitives (IDs, VINT/header parsing, Cluster verification and
scanning) live in ``ebml_conceal_primitives`` and planning plus the plan store
in ``ebml_conceal_plan``; this module holds the stream tracker and re-exports
both so ``resources.lib.ebml_conceal.<name>`` stays the one import point.
"""

import collections

from resources.lib.ebml_conceal_plan import (
    DEFAULT_MAX_SPAN,
    KIND_PAYLOAD,
    KIND_UNREPAIRED,
    KIND_VOID,
    ConcealPlan,
    ConcealPlanStore,
    plan_concealment,
)
from resources.lib.ebml_conceal_primitives import (
    _CLUSTER_CHILDREN,
    _GLOBAL_IDS,
    CLUSTER_ID_BYTES,
    ID_ATTACHMENTS,
    ID_BLOCK,
    ID_BLOCKADDITIONS,
    ID_BLOCKDURATION,
    ID_BLOCKGROUP,
    ID_BLOCKVIRTUAL,
    ID_CHAPTERS,
    ID_CLUSTER,
    ID_CODECSTATE,
    ID_CRC32,
    ID_CUES,
    ID_DISCARDPADDING,
    ID_EBML,
    ID_ENCRYPTEDBLOCK,
    ID_INFO,
    ID_POSITION,
    ID_PREVSIZE,
    ID_REFERENCEBLOCK,
    ID_REFERENCEFRAME,
    ID_REFERENCEPRIORITY,
    ID_REFERENCEVIRTUAL,
    ID_SEEKHEAD,
    ID_SEGMENT,
    ID_SILENTTRACKS,
    ID_SIMPLEBLOCK,
    ID_SLICES,
    ID_TAGS,
    ID_TIMESTAMP,
    ID_TRACKS,
    ID_VOID,
    STATUS_BAD,
    STATUS_NEED,
    STATUS_OK,
    ClusterScanner,
    Element,
    _scan_for_cluster,
    _scan_keep_from,
    encode_vint,
    parse_header,
    parse_id,
    read_vint,
    verify_cluster,
    void_header,
)

# Re-exported so ``resources.lib.ebml_conceal.<name>`` keeps resolving for
# the proxy mixin and tests after the primitives/planning split.
__all__ = [
    "CLUSTER_ID_BYTES",
    "ClusterScanner",
    "ConcealPlan",
    "ConcealPlanStore",
    "DEFAULT_MAX_SPAN",
    "EbmlStreamTracker",
    "Element",
    "ID_ATTACHMENTS",
    "ID_BLOCK",
    "ID_BLOCKADDITIONS",
    "ID_BLOCKDURATION",
    "ID_BLOCKGROUP",
    "ID_BLOCKVIRTUAL",
    "ID_CHAPTERS",
    "ID_CLUSTER",
    "ID_CODECSTATE",
    "ID_CRC32",
    "ID_CUES",
    "ID_DISCARDPADDING",
    "ID_EBML",
    "ID_ENCRYPTEDBLOCK",
    "ID_INFO",
    "ID_POSITION",
    "ID_PREVSIZE",
    "ID_REFERENCEBLOCK",
    "ID_REFERENCEFRAME",
    "ID_REFERENCEPRIORITY",
    "ID_REFERENCEVIRTUAL",
    "ID_SEEKHEAD",
    "ID_SEGMENT",
    "ID_SILENTTRACKS",
    "ID_SIMPLEBLOCK",
    "ID_SLICES",
    "ID_TAGS",
    "ID_TIMESTAMP",
    "ID_TRACKS",
    "ID_VOID",
    "KIND_PAYLOAD",
    "KIND_UNREPAIRED",
    "KIND_VOID",
    "MAX_HEADER_LEN",
    "STATUS_BAD",
    "STATUS_NEED",
    "STATUS_OK",
    "TrackerSnapshot",
    "encode_vint",
    "parse_header",
    "parse_id",
    "plan_concealment",
    "read_vint",
    "verify_cluster",
    "void_header",
]

# Allowed direct children of the masters the tracker descends into.
_ROOT_CHILDREN = frozenset((ID_EBML, ID_SEGMENT))
_SEGMENT_CHILDREN = frozenset(
    (
        ID_SEEKHEAD,
        ID_INFO,
        ID_TRACKS,
        ID_CLUSTER,
        ID_CUES,
        ID_CHAPTERS,
        ID_TAGS,
        ID_ATTACHMENTS,
    )
)
_BLOCKGROUP_CHILDREN = frozenset(
    (
        ID_BLOCK,
        ID_BLOCKVIRTUAL,
        ID_BLOCKADDITIONS,
        ID_BLOCKDURATION,
        ID_REFERENCEPRIORITY,
        ID_REFERENCEBLOCK,
        ID_REFERENCEVIRTUAL,
        ID_CODECSTATE,
        ID_DISCARDPADDING,
        ID_SLICES,
        ID_REFERENCEFRAME,
    )
)
_CHILDREN = {
    ID_SEGMENT: _SEGMENT_CHILDREN,
    ID_CLUSTER: _CLUSTER_CHILDREN,
    ID_BLOCKGROUP: _BLOCKGROUP_CHILDREN,
}
# Masters the tracker descends into. Every other element (including masters
# such as Tracks or BlockAdditions) is skipped whole by its declared size.
_DESCEND_IDS = frozenset((ID_SEGMENT, ID_CLUSTER, ID_BLOCKGROUP))
# Only these may declare the EBML "unknown size" (live-muxed files).
_UNKNOWN_SIZE_IDS = frozenset((ID_SEGMENT, ID_CLUSTER))

MAX_HEADER_LEN = 12  # 4-byte ID + 8-byte size

TrackerSnapshot = collections.namedtuple(
    "TrackerSnapshot",
    "synced pos masters leaf partial_header seen_cluster desync_reason",
    defaults=(None,),
)
TrackerSnapshot.__doc__ = """Structural state at the next byte to be emitted.

``masters`` lists the open descended masters (outermost first), ``leaf`` the
element whose payload ``pos`` is inside (None at a boundary), and
``partial_header`` how many bytes of the current element header were already
emitted (non-zero means ``pos`` is inside a header)."""


def _allowed_in(parent, element_id):
    """Whether ``element_id`` may appear directly inside ``parent``."""
    if parent is None:
        # EBML globals (Void, CRC-32) are not allowed at the root.
        return element_id in _ROOT_CHILDREN
    allowed = _CHILDREN.get(parent.id, frozenset())
    return element_id in allowed or element_id in _GLOBAL_IDS


def _overruns_parent(parent, payload_start, end):
    """A child of a sized parent must lie inside it (and be sized itself)."""
    if parent is None or parent.end is None:
        return False
    return end is None or end > parent.end or payload_start > parent.end


# ---------------------------------------------------------------------------
# Incremental tracker of the emitted stream
# ---------------------------------------------------------------------------

_MODE_SYNCED = "synced"
_MODE_SCAN = "scan"
_MODE_DISABLED = "disabled"


class EbmlStreamTracker:  # pylint: disable=too-many-instance-attributes
    """Track EBML structure of a forward byte stream, one header at a time.

    Feed every byte the proxy sends, in order, starting at ``start_offset``.
    Payloads are skipped by declared size, so cost is per element, not per
    byte, and state is a small stack. A stream that starts at byte 0 must open
    with an EBML header (otherwise the tracker disables itself: not EBML). A
    stream that starts mid-file stays unsynced until it sees a *verified*
    Cluster. Any structural inconsistency drops sync again (and the tracker
    rescans for the next verified Cluster), so a false sync cannot persist.

    ``ebml_detected`` is the container verdict: None while undecided, True
    once an EBML header at byte 0 or a verified Cluster was seen, False when
    a stream starting at byte 0 did not open with an EBML header.
    """

    def __init__(self, start_offset, content_length=0):
        self.start_offset = start_offset
        self.pos = start_offset
        self.content_length = content_length or 0
        self.ebml_detected = None
        self.seen_cluster = False
        self.desync_reason = None
        self._stack = []
        self._leaf = None
        self._header = bytearray()
        self._header_start = start_offset
        self._scan = bytearray()
        self._scan_base = start_offset
        self._mode = _MODE_SYNCED if start_offset == 0 else _MODE_SCAN

    @property
    def synced(self):
        return self._mode == _MODE_SYNCED

    def snapshot(self):
        synced = self._mode == _MODE_SYNCED
        return TrackerSnapshot(
            synced,
            self.pos,
            tuple(self._stack) if synced else (),
            self._leaf if synced else None,
            len(self._header) if synced else 0,
            self.seen_cluster,
            self.desync_reason,
        )

    def feed(self, data):
        view = memoryview(data)
        index = 0
        while index < len(view):
            mode = self._mode
            if mode == _MODE_DISABLED:
                self.pos += len(view) - index
                return
            if mode == _MODE_SCAN:
                view, index = self._feed_scan(view, index)
                continue
            leaf = self._leaf
            if leaf is not None:
                step = min(leaf.end - self.pos, len(view) - index)
                index += step
                self.pos += step
                if self.pos == leaf.end:
                    self._leaf = None
                    self._pop_finished()
                continue
            index = self._feed_header(view, index)

    # -- synced mode -------------------------------------------------------

    def _feed_header(self, view, index):
        size_left = len(view) - index
        if self._header:
            held = len(self._header)
            take = min(MAX_HEADER_LEN - held, size_left)
            probe = bytes(self._header) + bytes(view[index : index + take])
            status, element_id, header_len, size = parse_header(probe, 0)
            if status == STATUS_NEED:
                self._header.extend(view[index : index + take])
                self.pos += take
                return index + take
            if status == STATUS_BAD:
                return self._desync("malformed element header", view, index)
            consumed = header_len - held
            start = self._header_start
            self._header = bytearray()
            self.pos += consumed
            self._on_element(element_id, start, start + header_len, size)
            return index + consumed
        status, element_id, header_len, size = parse_header(view, index)
        if status == STATUS_NEED:
            self._header_start = self.pos
            self._header.extend(view[index:])
            self.pos += size_left
            return len(view)
        if status == STATUS_BAD:
            return self._desync("malformed element header", view, index)
        start = self.pos
        self.pos += header_len
        self._on_element(element_id, start, start + header_len, size)
        return index + header_len

    def _on_element(self, element_id, start, payload_start, size):
        end = None if size is None else payload_start + size
        reason = self._close_to_parent(element_id)
        if reason is None:
            reason = self._bounds_error(element_id, payload_start, end)
        if reason is not None:
            self._fail(reason, start)
            return
        self._accept(Element(element_id, start, payload_start, end))

    def _close_to_parent(self, element_id):
        """Pop unknown-size masters until ``element_id`` fits its parent.

        An unknown-size master ends where a sibling-or-higher appears. Returns
        None once the element is allowed in the (new) innermost master, else
        the desync reason.
        """
        stack = self._stack
        while not _allowed_in(stack[-1] if stack else None, element_id):
            if not stack or stack[-1].end is not None:
                parent_id = stack[-1].id if stack else 0
                return "element 0x{:X} not allowed in 0x{:X}".format(
                    element_id, parent_id
                )
            stack.pop()
        return None

    def _bounds_error(self, element_id, payload_start, end):
        """Desync reason when the element's size breaks its bounds, or None."""
        if end is None and element_id not in _UNKNOWN_SIZE_IDS:
            return "unknown size on 0x{:X}".format(element_id)
        parent = self._stack[-1] if self._stack else None
        if _overruns_parent(parent, payload_start, end):
            return "element 0x{:X} overruns its parent".format(element_id)
        if end is not None and self.content_length and end > self.content_length:
            return "element 0x{:X} overruns the stream".format(element_id)
        return None

    def _accept(self, element):
        """Record a validated element: descend, enter its payload, or skip it."""
        if element.id == ID_EBML and element.start == 0:
            self.ebml_detected = True
        if element.id == ID_CLUSTER:
            self.seen_cluster = True
        if element.id in _DESCEND_IDS:
            self._stack.append(element)
        elif element.end != element.payload_start:
            self._leaf = element
        self._pop_finished()

    def _pop_finished(self):
        stack = self._stack
        while stack and stack[-1].end is not None and self.pos >= stack[-1].end:
            stack.pop()

    def _fail(self, reason, at):
        """Desync after a header was consumed (no bytes to re-scan)."""
        self._enter_scan(reason, at, b"")

    def _desync(self, reason, view, index):
        held = bytes(self._header)
        start = self._header_start if held else self.pos
        self._header = bytearray()
        self._enter_scan(reason, start, held)
        return index

    def _enter_scan(self, reason, at, seed):
        self._stack = []
        self._leaf = None
        self._header = bytearray()
        self.desync_reason = "{} at {}".format(reason, at)
        if self.ebml_detected is None and self.start_offset == 0:
            # The stream opened with something other than an EBML header.
            self.ebml_detected = False
            self._mode = _MODE_DISABLED
            return
        self._mode = _MODE_SCAN
        self._scan = bytearray(seed)
        self._scan_base = self.pos - len(seed)

    # -- scan mode ---------------------------------------------------------

    def _feed_scan(self, view, index):
        """Consume ``view[index:]`` looking for a verified Cluster.

        Returns the ``(view, index)`` to continue with: on a sync, the bytes
        from the Cluster start onward are handed back to synced parsing.
        """
        remaining = view[index:]
        self.pos += len(remaining)
        buf = self._scan
        buf.extend(remaining)
        status, found = _scan_for_cluster(buf, 0, self._scan_base, self.content_length)
        if status == STATUS_OK:
            tail = memoryview(bytes(buf[found:]))
            self.pos = self._scan_base + found
            self._scan = bytearray()
            self._mode = _MODE_SYNCED
            self.ebml_detected = True
            self.desync_reason = None
            # The enclosing Segment's bounds are not known from here.
            self._stack = [Element(ID_SEGMENT, None, None, None)]
            return tail, 0
        keep_from = _scan_keep_from(buf, status, found)
        del buf[:keep_from]
        self._scan_base += keep_from
        return view, len(view)
