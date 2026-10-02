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
"""

import collections
import threading

ID_EBML = 0x1A45DFA3
ID_SEGMENT = 0x18538067
ID_SEEKHEAD = 0x114D9B74
ID_INFO = 0x1549A966
ID_TRACKS = 0x1654AE6B
ID_CLUSTER = 0x1F43B675
ID_CUES = 0x1C53BB6B
ID_CHAPTERS = 0x1043A770
ID_TAGS = 0x1254C367
ID_ATTACHMENTS = 0x1941A469
ID_TIMESTAMP = 0xE7
ID_SILENTTRACKS = 0x5854
ID_POSITION = 0xA7
ID_PREVSIZE = 0xAB
ID_SIMPLEBLOCK = 0xA3
ID_BLOCKGROUP = 0xA0
ID_ENCRYPTEDBLOCK = 0xAF
ID_BLOCK = 0xA1
ID_BLOCKVIRTUAL = 0xA2
ID_BLOCKADDITIONS = 0x75A1
ID_BLOCKDURATION = 0x9B
ID_REFERENCEPRIORITY = 0xFA
ID_REFERENCEBLOCK = 0xFB
ID_REFERENCEVIRTUAL = 0xFD
ID_CODECSTATE = 0xA4
ID_DISCARDPADDING = 0x75A2
ID_SLICES = 0x8E
ID_REFERENCEFRAME = 0xC8
ID_VOID = 0xEC
ID_CRC32 = 0xBF

CLUSTER_ID_BYTES = b"\x1f\x43\xb6\x75"

# EBML globals allowed at any level.
_GLOBAL_IDS = frozenset((ID_VOID, ID_CRC32))
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
_CLUSTER_CHILDREN = frozenset(
    (
        ID_TIMESTAMP,
        ID_SILENTTRACKS,
        ID_POSITION,
        ID_PREVSIZE,
        ID_SIMPLEBLOCK,
        ID_BLOCKGROUP,
        ID_ENCRYPTEDBLOCK,
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
    None: _ROOT_CHILDREN,
    ID_SEGMENT: _SEGMENT_CHILDREN,
    ID_CLUSTER: _CLUSTER_CHILDREN,
    ID_BLOCKGROUP: _BLOCKGROUP_CHILDREN,
}
# Masters the tracker descends into. Every other element (including masters
# such as Tracks or BlockAdditions) is skipped whole by its declared size.
_DESCEND_IDS = frozenset((ID_SEGMENT, ID_CLUSTER, ID_BLOCKGROUP))
# Only these may declare the EBML "unknown size" (live-muxed files).
_UNKNOWN_SIZE_IDS = frozenset((ID_SEGMENT, ID_CLUSTER))
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
# All-ones IDs are reserved by EBML.
_RESERVED_IDS = frozenset((0xFF, 0x7FFF, 0x3FFFFF, 0x1FFFFFFF))

MAX_HEADER_LEN = 12  # 4-byte ID + 8-byte size

STATUS_OK = "ok"
STATUS_NEED = "need"
STATUS_BAD = "bad"

KIND_PAYLOAD = "payload"
KIND_VOID = "void"
KIND_UNREPAIRED = "unrepaired"

# A track-number VINT for track 1. Written only when the gap swallowed a
# block's first payload byte (its track number): 0x00 is not a VINT, so a
# strict demuxer would reject the block even though its size is intact.
_BLOCK_TRACK_FIX = b"\x81"

# Upper bound on one concealed span, readable bytes sacrificed for resync
# included. The proxy's look-ahead budget is tighter; this is the backstop.
DEFAULT_MAX_SPAN = 48 * 1024 * 1024

# How many bytes after a candidate Cluster ID the verifier may need: Cluster
# header, optional CRC-32, Timestamp, and the next child's header.
_VERIFY_WINDOW = 64

Element = collections.namedtuple("Element", "id start payload_start end")
Element.__doc__ = """One parsed element; ``end`` is None for an unknown size."""

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


# ---------------------------------------------------------------------------
# VINT primitives
# ---------------------------------------------------------------------------


def read_vint(data, pos, limit=None):
    """Return ``(length, value)`` of the VINT at ``data[pos]``.

    ``length`` is 0 when the bytes cannot be a VINT (no marker bit in the
    first byte) and -1 when more bytes are needed.
    """
    if limit is None:
        limit = len(data)
    if pos >= limit:
        return -1, 0
    first = data[pos]
    if first == 0:
        return 0, 0
    length = 1
    mask = 0x80
    while not first & mask:
        mask >>= 1
        length += 1
    if pos + length > limit:
        return -1, 0
    value = first & (mask - 1)
    for i in range(1, length):
        value = (value << 8) | data[pos + i]
    return length, value


def _is_unknown_size(length, value):
    return value == (1 << (7 * length)) - 1


def encode_vint(value, length):
    """Encode ``value`` as a VINT of exactly ``length`` bytes."""
    if length < 1 or length > 8 or value >= (1 << (7 * length)) - 1 or value < 0:
        raise ValueError("value {} does not fit a {}-byte vint".format(value, length))
    return (value | (1 << (7 * length))).to_bytes(length, "big")


def _id_length(first):
    if first >= 0x80:
        return 1
    if first >= 0x40:
        return 2
    if first >= 0x20:
        return 3
    if first >= 0x10:
        return 4
    return 0


def parse_id(data, pos, limit=None):
    """Return ``(status, element_id, id_len)`` for the ID at ``pos``."""
    if limit is None:
        limit = len(data)
    if pos >= limit:
        return STATUS_NEED, 0, 0
    id_len = _id_length(data[pos])
    if not id_len:
        return STATUS_BAD, 0, 0
    if pos + id_len > limit:
        return STATUS_NEED, 0, 0
    element_id = int.from_bytes(bytes(data[pos : pos + id_len]), "big")
    marker = 1 << (8 * id_len - id_len)
    if element_id in _RESERVED_IDS or element_id == marker:
        return STATUS_BAD, 0, 0
    return STATUS_OK, element_id, id_len


def parse_header(data, pos, limit=None):
    """Parse the element header at ``pos``.

    Returns ``(status, element_id, header_len, size)`` where ``size`` is None
    for the unknown-size marker. ``status`` is STATUS_NEED when the header is
    cut off by ``limit`` and STATUS_BAD when the bytes cannot be a header.
    """
    if limit is None:
        limit = len(data)
    status, element_id, id_len = parse_id(data, pos, limit)
    if status != STATUS_OK:
        return status, 0, 0, 0
    size_len, size = read_vint(data, pos + id_len, limit)
    if size_len == 0:
        return STATUS_BAD, 0, 0, 0
    if size_len < 0:
        return STATUS_NEED, 0, 0, 0
    if _is_unknown_size(size_len, size):
        size = None
    return STATUS_OK, element_id, id_len + size_len, size


def void_header(span):
    """Header of a Void element occupying exactly ``span`` bytes, or None.

    Picks the shortest size VINT whose remaining payload is representable;
    the all-ones value of each length is the unknown-size marker, which is
    why, for example, a 129-byte span needs a 2-byte size (payload 126) instead of a
    1-byte one (payload 127 == marker). A span under 2 bytes cannot hold an ID
    plus a size at all.
    """
    if span < 2:
        return None
    for length in range(1, 9):
        payload = span - 1 - length
        if payload < 0:
            return None
        if payload < (1 << (7 * length)) - 1:
            return bytes((ID_VOID,)) + encode_vint(payload, length)
    return None


# ---------------------------------------------------------------------------
# Cluster verification / look-ahead scanning
# ---------------------------------------------------------------------------


def verify_cluster(data, pos, abs_offset, content_length=0):
    """Decide whether ``data[pos]`` starts a real Cluster.

    A bare ID match is never enough: the four ID bytes occur inside
    compressed frames. The size must be plausible and fit the stream, the
    first child must be CRC-32 or Timestamp, a Timestamp must follow within the
    first two children, and the header of the child after the Timestamp must
    parse as a valid Cluster child inside the Cluster's bounds. Returns
    STATUS_OK, STATUS_BAD, or STATUS_NEED when ``data`` ends too early.
    """
    status, element_id, header_len, size = parse_header(data, pos)
    if status != STATUS_OK:
        return status
    if element_id != ID_CLUSTER:
        return STATUS_BAD
    cluster_end = None
    if size is not None:
        cluster_end = abs_offset + header_len + size
        if size < 3 or (content_length and cluster_end > content_length):
            return STATUS_BAD
    cursor = pos + header_len
    seen_timestamp = False
    for index in range(3):
        if cluster_end is not None and abs_offset + (cursor - pos) == cluster_end:
            return STATUS_OK if seen_timestamp else STATUS_BAD
        status, child_id, _ = parse_id(data, cursor)
        if status == STATUS_OK and (
            child_id not in _CLUSTER_CHILDREN and child_id not in _GLOBAL_IDS
        ):
            return STATUS_BAD
        if status == STATUS_OK:
            status, child_id, child_header, child_size = parse_header(data, cursor)
        if status == STATUS_NEED:
            return STATUS_NEED if len(data) - pos < _VERIFY_WINDOW else STATUS_BAD
        if status == STATUS_BAD or child_size is None:
            return STATUS_BAD
        if child_id not in _CLUSTER_CHILDREN and child_id not in _GLOBAL_IDS:
            return STATUS_BAD
        child_end = abs_offset + (cursor - pos) + child_header + child_size
        if cluster_end is not None and child_end > cluster_end:
            return STATUS_BAD
        if seen_timestamp:
            return STATUS_OK
        if child_id == ID_TIMESTAMP:
            if child_size > 8:
                return STATUS_BAD
            seen_timestamp = True
        elif child_id == ID_CRC32:
            if index != 0 or child_size != 4:
                return STATUS_BAD
        else:
            return STATUS_BAD
        cursor += child_header + child_size
    return STATUS_BAD


def _scan_for_cluster(buf, search, base, content_length):
    """Scan ``buf`` from ``search`` for a verified Cluster.

    Returns ``(STATUS_OK, index)``, ``(STATUS_NEED, index)`` when the first
    unresolved candidate needs more bytes, or ``(STATUS_BAD, None)``.
    """
    while True:
        index = buf.find(CLUSTER_ID_BYTES, search)
        if index < 0:
            return STATUS_BAD, None
        status = verify_cluster(buf, index, base + index, content_length)
        if status != STATUS_BAD:
            return status, index
        search = index + 1


class ClusterScanner:
    """Incrementally find the first verified Cluster in a forward byte run.

    Feed consecutive chunks starting at ``from_offset``; :meth:`feed` returns
    the absolute offset of the first verified Cluster or None. Memory stays
    bounded: only a short tail is carried between chunks so an ID or a
    candidate's header split across a chunk edge is still seen.
    """

    def __init__(self, from_offset, content_length=0):
        self._base = from_offset
        self._buf = bytearray()
        self._content_length = content_length
        self.scanned = 0
        self.found = None

    def feed(self, chunk):
        if self.found is not None:
            return self.found
        self.scanned += len(chunk)
        self._buf.extend(chunk)
        status, index = _scan_for_cluster(
            self._buf, 0, self._base, self._content_length
        )
        if status == STATUS_OK:
            self.found = self._base + index
            return self.found
        if status == STATUS_NEED:
            keep_from = index
        else:
            keep_from = max(0, len(self._buf) - (len(CLUSTER_ID_BYTES) - 1))
        del self._buf[:keep_from]
        self._base += keep_from
        return None


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
    """

    def __init__(self, start_offset, content_length=0):
        self.start_offset = start_offset
        self.pos = start_offset
        self.content_length = content_length or 0
        self.is_ebml = None
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
        parent = self._stack[-1] if self._stack else None
        while True:
            parent_id = parent.id if parent is not None else None
            allowed = _CHILDREN.get(parent_id, frozenset())
            if element_id in allowed or (
                parent is not None and element_id in _GLOBAL_IDS
            ):
                break
            # An unknown-size master ends where a sibling-or-higher appears.
            if parent is not None and parent.end is None:
                self._stack.pop()
                parent = self._stack[-1] if self._stack else None
                continue
            self._fail(
                "element 0x{:X} not allowed in 0x{:X}".format(
                    element_id, parent_id or 0
                ),
                start,
            )
            return
        end = None if size is None else payload_start + size
        if end is None and element_id not in _UNKNOWN_SIZE_IDS:
            self._fail("unknown size on 0x{:X}".format(element_id), start)
            return
        if parent is not None and parent.end is not None:
            if end is None or end > parent.end or payload_start > parent.end:
                reason = "element 0x{:X} overruns its parent".format(element_id)
                self._fail(reason, start)
                return
        if end is not None and self.content_length and end > self.content_length:
            self._fail("element 0x{:X} overruns the stream".format(element_id), start)
            return
        if element_id == ID_EBML and start == 0:
            self.is_ebml = True
        if element_id == ID_CLUSTER:
            self.seen_cluster = True
        element = Element(element_id, start, payload_start, end)
        if element_id in _DESCEND_IDS:
            self._stack.append(element)
        elif end != payload_start:
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
        if self.is_ebml is None and self.start_offset == 0:
            # The stream opened with something other than an EBML header.
            self.is_ebml = False
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
            self.is_ebml = True
            self.desync_reason = None
            # The enclosing Segment's bounds are not known from here.
            self._stack = [Element(ID_SEGMENT, None, None, None)]
            return tail, 0
        if status == STATUS_NEED:
            keep_from = found
        else:
            keep_from = max(0, len(buf) - (len(CLUSTER_ID_BYTES) - 1))
        del buf[:keep_from]
        self._scan_base += keep_from
        return view, len(view)


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


def _unrepaired(gap_start, gap_end, reason):
    return ConcealPlan(KIND_UNREPAIRED, gap_start, gap_end, (), reason)


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


def plan_concealment(  # pylint: disable=too-many-return-statements,too-many-branches
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
    if gap_end <= gap_start:
        return _unrepaired(gap_start, gap_end, "empty_gap")
    if not snapshot.synced:
        return _unrepaired(gap_start, gap_end, "no_structural_context")
    if snapshot.pos != gap_start:
        return _unrepaired(gap_start, gap_end, "position_mismatch")
    if snapshot.partial_header:
        # Part of this element's header is already on the wire; a Void would
        # have to start before gap_start.
        return _unrepaired(gap_start, gap_end, "header_already_emitted")
    masters = snapshot.masters
    if not masters or masters[0].id != ID_SEGMENT:
        return _unrepaired(gap_start, gap_end, "outside_segment")

    edits = []
    leaf = snapshot.leaf
    if leaf is not None:
        if leaf.id not in _PAYLOAD_SAFE_IDS:
            return _unrepaired(gap_start, gap_end, "opaque_element")
        if (
            leaf.id in (ID_SIMPLEBLOCK, ID_BLOCK)
            and gap_start == leaf.payload_start
            and leaf.end - leaf.payload_start >= 4
        ):
            edits.append((gap_start, _BLOCK_TRACK_FIX))
        if leaf.end >= gap_end:
            return ConcealPlan(KIND_PAYLOAD, gap_start, gap_end, edits, "payload_only")
        boundary = leaf.end
    else:
        boundary = gap_start

    segment = masters[0]
    for master in reversed(masters[1:]):
        if master.end is None:
            # Unknown-size Cluster: the next verified Cluster both ends it and
            # is the resume point, so the Void runs straight there.
            break
        if not _void_edit(edits, boundary, master.end):
            return _unrepaired(gap_start, gap_end, "void_slot_too_small")
        if master.end >= gap_end:
            if master.end - gap_start > max_span:
                return _unrepaired(gap_start, gap_end, "span_exceeds_bound")
            return ConcealPlan(
                KIND_VOID, gap_start, master.end, edits, "void_to_master_end"
            )
        boundary = master.end

    if not snapshot.seen_cluster:
        # Still in the header region (SeekHead/Info/Tracks): voiding there
        # would delete track metadata, not conceal media.
        return _unrepaired(gap_start, gap_end, "header_region")
    search_from = max(boundary, gap_end)
    if search_from - gap_start >= max_span:
        return _unrepaired(gap_start, gap_end, "span_exceeds_bound")
    if find_cluster is None:
        return _unrepaired(gap_start, gap_end, "no_verified_cluster:no_lookahead")
    resume, why = find_cluster(search_from, segment.end)
    if resume is None:
        return _unrepaired(gap_start, gap_end, "no_verified_cluster:{}".format(why))
    if resume < search_from or (segment.end is not None and resume >= segment.end):
        return _unrepaired(gap_start, gap_end, "no_verified_cluster:out_of_bounds")
    if resume - gap_start > max_span:
        return _unrepaired(gap_start, gap_end, "span_exceeds_bound")
    if not _void_edit(edits, boundary, resume):
        return _unrepaired(gap_start, gap_end, "void_slot_too_small")
    return ConcealPlan(KIND_VOID, gap_start, resume, edits, "void_to_next_cluster")


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
