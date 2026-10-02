# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Tiny Matroska/EBML byte builder for the gap-concealment tests.

Builds structurally exact files with known element offsets so tests can aim a
gap at a precise structural position (inside a block payload, on a header, at
a Cluster boundary, ...). Nothing here is used by the add-on at runtime.
"""

ID_EBML = b"\x1a\x45\xdf\xa3"
ID_SEGMENT = b"\x18\x53\x80\x67"
ID_INFO = b"\x15\x49\xa9\x66"
ID_TRACKS = b"\x16\x54\xae\x6b"
ID_CLUSTER = b"\x1f\x43\xb6\x75"
ID_TIMESTAMP = b"\xe7"
ID_SIMPLEBLOCK = b"\xa3"
ID_BLOCKGROUP = b"\xa0"
ID_BLOCK = b"\xa1"
ID_BLOCKDURATION = b"\x9b"
ID_VOID = b"\xec"
ID_CUES = b"\x1c\x53\xbb\x6b"

UNKNOWN = object()


def vint(value, length=None):
    """Encode ``value`` as an EBML size vint (minimal length by default)."""
    if length is None:
        length = 1
        while value >= (1 << (7 * length)) - 1:
            length += 1
    return (value | (1 << (7 * length))).to_bytes(length, "big")


def unknown_size(length=8):
    return (((1 << (7 * length)) - 1) | (1 << (7 * length))).to_bytes(length, "big")


class Node:
    """One element: ``id`` bytes plus either raw ``payload`` or ``children``."""

    def __init__(self, name, eid, payload=b"", children=None, size=None):
        self.name = name
        self.eid = eid
        self.payload = payload
        self.children = children
        self.size = size  # None = minimal vint, UNKNOWN, or an explicit length
        self.start = None
        self.payload_start = None
        self.end = None

    def body(self):
        if self.children is None:
            return self.payload
        return b"".join(child.encode() for child in self.children)

    def encode(self):
        body = self.body()
        if self.size is UNKNOWN:
            size = unknown_size()
        elif self.size is None:
            size = vint(len(body))
        else:
            size = vint(len(body), self.size)
        return self.eid + size + body

    def layout(self, offset):
        """Assign absolute offsets; returns the end offset."""
        self.start = offset
        body_len = len(self.body())
        if self.size is UNKNOWN:
            size_len = 8
        elif self.size is None:
            size_len = len(vint(body_len))
        else:
            size_len = self.size
        self.payload_start = offset + len(self.eid) + size_len
        cursor = self.payload_start
        for child in self.children or ():
            cursor = child.layout(cursor)
        self.end = self.payload_start + body_len
        return self.end

    def walk(self):
        yield self
        for child in self.children or ():
            for node in child.walk():
                yield node


def simple_block(name, track=1, frame=b"", frame_len=64, fill=0x11):
    frame = frame or bytes([fill]) * frame_len
    payload = bytes([0x80 | track]) + b"\x00\x00\x80" + frame
    return Node(name, ID_SIMPLEBLOCK, payload)


def block_group(name, track=1, frame_len=64, fill=0x22):
    frame = bytes([fill]) * frame_len
    payload = bytes([0x80 | track]) + b"\x00\x00\x00" + frame
    block = Node(name + ".block", ID_BLOCK, payload)
    duration = Node(name + ".duration", ID_BLOCKDURATION, b"\x10")
    return Node(name, ID_BLOCKGROUP, children=[block, duration])


def cluster(name, timestamp, blocks, size=None):
    ts = Node(name + ".ts", ID_TIMESTAMP, bytes([timestamp & 0xFF]))
    return Node(name, ID_CLUSTER, children=[ts] + list(blocks), size=size)


class Mkv:
    """A built file: ``data`` bytes plus a name -> Node index."""

    def __init__(self, segment_children, segment_size=None):
        header = Node("ebml", ID_EBML, b"\x42\x82\x88matroska")
        self.segment = Node(
            "segment", ID_SEGMENT, children=segment_children, size=segment_size
        )
        self.roots = [header, self.segment]
        offset = 0
        for root in self.roots:
            offset = root.layout(offset)
        self.data = b"".join(root.encode() for root in self.roots)
        assert len(self.data) == offset
        self.nodes = {}
        for root in self.roots:
            for node in root.walk():
                self.nodes[node.name] = node

    def __getitem__(self, name):
        return self.nodes[name]


def standard_file(cluster_count=4, blocks_per_cluster=3, frame_len=200, **kwargs):
    """Header region (Info, Tracks) followed by known-size Clusters."""
    children = [
        Node("info", ID_INFO, b"\x2a\xd7\xb1\x83\x0f\x42\x40"),
        Node("tracks", ID_TRACKS, b"\xae\x83\xd7\x81\x01"),
    ]
    for c in range(cluster_count):
        blocks = [
            simple_block(
                "c{}.b{}".format(c, b), frame_len=frame_len, fill=0x30 + c * 8 + b
            )
            for b in range(blocks_per_cluster)
        ]
        children.append(cluster("c{}".format(c), c * 10, blocks, **kwargs))
    return Mkv(children)
