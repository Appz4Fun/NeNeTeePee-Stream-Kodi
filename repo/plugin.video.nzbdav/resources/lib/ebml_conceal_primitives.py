# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
#
# The replacement strategy (Void elements sized to valid boundaries, popping
# out through enclosing masters, resuming at a verified Cluster) is adapted
# from StreamNZB's pkg/media/ebml (GPL-3.0, commit 242a9c5d), reworked for a
# forward-only HTTP proxy that cannot rewrite bytes it has already sent.

"""EBML primitives for :mod:`resources.lib.ebml_conceal`.

Matroska element IDs and their allowed-children sets, VINT and element-header
parsing, Void header sizing, and the verified-Cluster check and incremental
scanner used both by the stream tracker and by the proxy's bounded look-ahead.
Pure Python, no Kodi imports. Split out of ``ebml_conceal`` to keep that
module under the file-size budget; every public name here is re-exported from
``resources.lib.ebml_conceal`` so existing callers and tests keep resolving.
"""

import collections

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
# All-ones IDs are reserved by EBML.
_RESERVED_IDS = frozenset((0xFF, 0x7FFF, 0x3FFFFF, 0x1FFFFFFF))

STATUS_OK = "ok"
STATUS_NEED = "need"
STATUS_BAD = "bad"

# How many bytes after a candidate Cluster ID the verifier may need: Cluster
# header, optional CRC-32, Timestamp, and the next child's header.
_VERIFY_WINDOW = 64

# Stand-in bound for an unknown-size Cluster: no child offset ever equals or
# exceeds it, so the bound checks below need no separate None branch.
_NO_BOUND = float("inf")

Element = collections.namedtuple("Element", "id start payload_start end")
Element.__doc__ = """One parsed element; ``end`` is None for an unknown size."""


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


def _is_cluster_child(element_id):
    return element_id in _CLUSTER_CHILDREN or element_id in _GLOBAL_IDS


def _cluster_bound(data, pos, abs_offset, content_length):
    """Check the candidate Cluster header at ``pos``.

    Returns ``(status, header_len, cluster_end)``; ``cluster_end`` is the
    absolute end offset, or ``_NO_BOUND`` for an unknown-size Cluster.
    """
    status, element_id, header_len, size = parse_header(data, pos)
    if status != STATUS_OK:
        return status, 0, None
    if element_id != ID_CLUSTER:
        return STATUS_BAD, 0, None
    if size is None:
        return STATUS_OK, header_len, _NO_BOUND
    cluster_end = abs_offset + header_len + size
    if size < 3 or (content_length and cluster_end > content_length):
        return STATUS_BAD, 0, None
    return STATUS_OK, header_len, cluster_end


def _cluster_child_header(data, cursor, available):
    """Parse a candidate Cluster child header at ``cursor``.

    Returns ``(status, child_id, header_len, size)``. An ID that parses but is
    not a Cluster child is rejected before its size is needed. A header cut
    off by the end of ``data`` is STATUS_NEED only while fewer than
    ``_VERIFY_WINDOW`` bytes (``available``) were offered for the candidate.
    """
    status, child_id, _ = parse_id(data, cursor)
    if status == STATUS_OK and not _is_cluster_child(child_id):
        status = STATUS_BAD
    if status == STATUS_BAD:
        return STATUS_BAD, 0, 0, 0
    status, child_id, header_len, size = parse_header(data, cursor)
    if status == STATUS_NEED:
        return (STATUS_NEED if available < _VERIFY_WINDOW else STATUS_BAD), 0, 0, 0
    if status == STATUS_BAD or size is None:
        return STATUS_BAD, 0, 0, 0
    return STATUS_OK, child_id, header_len, size


def _valid_leading_child(index, child_id, child_size):
    """Before the Timestamp, only a 4-byte CRC-32 as the first child may appear."""
    if child_id == ID_TIMESTAMP:
        return child_size <= 8
    return child_id == ID_CRC32 and index == 0 and child_size == 4


def _verify_cluster_children(data, pos, cursor, abs_offset, cluster_end):
    """Check the first children of the Cluster at ``pos`` (see verify_cluster)."""
    seen_timestamp = False
    for index in range(3):
        child_start = abs_offset + (cursor - pos)
        if child_start == cluster_end:
            return STATUS_OK if seen_timestamp else STATUS_BAD
        status, child_id, child_header, child_size = _cluster_child_header(
            data, cursor, len(data) - pos
        )
        if status != STATUS_OK:
            return status
        if child_start + child_header + child_size > cluster_end:
            return STATUS_BAD
        if seen_timestamp:
            return STATUS_OK
        if not _valid_leading_child(index, child_id, child_size):
            return STATUS_BAD
        seen_timestamp = child_id == ID_TIMESTAMP
        cursor += child_header + child_size
    return STATUS_BAD


def verify_cluster(data, pos, abs_offset, content_length=0):
    """Decide whether ``data[pos]`` starts a real Cluster.

    A bare ID match is never enough: the four ID bytes occur inside
    compressed frames. The size must be plausible and fit the stream, the
    first child must be CRC-32 or Timestamp, a Timestamp must follow within the
    first two children, and the header of the child after the Timestamp must
    parse as a valid Cluster child inside the Cluster's bounds. Returns
    STATUS_OK, STATUS_BAD, or STATUS_NEED when ``data`` ends too early.
    """
    status, header_len, cluster_end = _cluster_bound(
        data, pos, abs_offset, content_length
    )
    if status != STATUS_OK:
        return status
    return _verify_cluster_children(
        data, pos, pos + header_len, abs_offset, cluster_end
    )


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


def _scan_keep_from(buf, status, index):
    """Where to trim a scan buffer so a split ID or candidate is still seen."""
    if status == STATUS_NEED:
        return index
    return max(0, len(buf) - (len(CLUSTER_ID_BYTES) - 1))


class ClusterScanner:  # pylint: disable=too-few-public-methods
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
        keep_from = _scan_keep_from(self._buf, status, index)
        del self._buf[:keep_from]
        self._base += keep_from
        return None
