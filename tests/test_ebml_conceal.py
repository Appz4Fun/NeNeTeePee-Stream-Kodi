# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Unit tests for the pure EBML gap-concealment parser and planner.

These pin the structural contract independently of the proxy: VINT parsing,
Void sizing across VINT length transitions, the incremental stream tracker
(including sync on a *verified* Cluster only), the replacement planner, the
bounded Cluster look-ahead scanner, and the per-session plan store.

The core property every structural plan must satisfy: substituting
``plan.render`` over ``[plan.start, plan.end)`` keeps the total length and every
byte outside that span unchanged, never edits bytes before the gap start, and
leaves a stream a fresh tracker parses end-to-end without losing sync.
"""

import pytest
from resources.lib import ebml_conceal as ec

from tests.ebml_builder import (
    ID_CLUSTER,
    ID_TIMESTAMP,
    UNKNOWN,
    Mkv,
    Node,
    block_group,
    cluster,
    simple_block,
    standard_file,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _track(data, start=0, chunk=None, content_length=None):
    tracker = ec.EbmlStreamTracker(start, content_length or 0)
    view = data[start:]
    if chunk is None:
        tracker.feed(view)
    else:
        for i in range(0, len(view), chunk):
            tracker.feed(view[i : i + chunk])
    return tracker


def _snapshot_at(data, offset, start=0, chunk=None):
    tracker = _track(data[:offset], start=start, chunk=chunk, content_length=len(data))
    return tracker.snapshot()


def _cluster_finder(data):
    """find_cluster callback scanning the real bytes (no budget concerns)."""
    calls = []

    def find(from_offset, limit):
        calls.append((from_offset, limit))
        scanner = ec.ClusterScanner(from_offset, len(data))
        found = scanner.feed(data[from_offset:limit])
        if found is None:
            return None, "not_found"
        return found, "ok"

    find.calls = calls
    return find


def _apply(data, plan):
    rendered = plan.render(plan.start, plan.end - plan.start)
    return data[: plan.start] + rendered + data[plan.end :]


def _assert_structural(data, plan):
    """The structural-plan contract (length, offsets, parse continuation)."""
    assert plan.structural
    assert all(off >= plan.start for off, _ in plan.edits)
    assert all(off + len(b) <= plan.end for off, b in plan.edits)
    out = _apply(data, plan)
    assert len(out) == len(data)
    assert out[: plan.start] == data[: plan.start]
    assert out[plan.end :] == data[plan.end :]
    # Feed in awkward chunk sizes: the tracker must stay synced through the
    # synthetic span, sit at a clean boundary at the resume point, and parse
    # the original continuation to EOF.
    tracker = _track(out, chunk=7, content_length=len(out))
    snap = tracker.snapshot()
    assert snap.synced, tracker.desync_reason
    assert snap.pos == len(out)
    resume = _track(out[: plan.end], chunk=5, content_length=len(out)).snapshot()
    assert resume.synced
    assert resume.partial_header == 0
    return out


# ---------------------------------------------------------------------------
# VINT / header primitives
# ---------------------------------------------------------------------------


def test_read_vint_lengths_and_values():
    assert ec.read_vint(b"\x81", 0) == (1, 1)
    assert ec.read_vint(b"\x40\x02", 0) == (2, 2)
    assert ec.read_vint(b"\x01\x00\x00\x00\x00\x00\x00\x05", 0) == (8, 5)


def test_read_vint_malformed_and_truncated():
    assert ec.read_vint(b"\x00\x81", 0)[0] == 0  # >8-byte length: malformed
    assert ec.read_vint(b"\x40", 0)[0] == -1  # 2-byte vint, 1 byte present
    assert ec.read_vint(b"", 0)[0] == -1


def test_parse_header_rejects_reserved_and_zero_ids():
    assert ec.parse_header(b"\xff\x81", 0)[0] == ec.STATUS_BAD  # all-ones ID
    assert ec.parse_header(b"\x80\x81", 0)[0] == ec.STATUS_BAD  # all-zero ID
    assert ec.parse_header(b"\x05\x81", 0)[0] == ec.STATUS_BAD  # >4-byte ID
    assert ec.parse_header(b"\x1f\x43\xb6", 0)[0] == ec.STATUS_NEED


def test_parse_header_unknown_size():
    status, eid, hlen, size = ec.parse_header(b"\x1f\x43\xb6\x75\xff", 0)
    assert (status, eid, hlen, size) == (ec.STATUS_OK, ec.ID_CLUSTER, 5, None)


@pytest.mark.parametrize(
    "span,header_len",
    [
        (2, 2),
        (3, 2),
        (128, 2),  # 1-byte size: payload 126
        (129, 3),  # payload 127 is the 1-byte unknown marker -> 2-byte size
        (130, 3),
        (16385, 3),  # payload 16382: last 2-byte value
        (16386, 4),  # payload 16383 = 2-byte unknown marker -> 3-byte size
        (16387, 4),
    ],
)
def test_void_header_vint_transitions(span, header_len):
    header = ec.void_header(span)
    assert header is not None and len(header) == header_len
    status, eid, hlen, size = ec.parse_header(header, 0)
    assert status == ec.STATUS_OK and eid == ec.ID_VOID
    assert hlen + size == span


@pytest.mark.parametrize("span", [0, 1, -5])
def test_void_header_impossible_spans(span):
    assert ec.void_header(span) is None


# ---------------------------------------------------------------------------
# Incremental tracker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chunk", [None, 1, 3, 7, 64])
def test_tracker_parses_whole_file_in_any_chunking(chunk):
    mkv = standard_file()
    tracker = _track(mkv.data, chunk=chunk, content_length=len(mkv.data))
    snap = tracker.snapshot()
    assert tracker.is_ebml is True
    assert snap.synced and snap.pos == len(mkv.data)
    assert snap.seen_cluster


def test_tracker_snapshot_inside_block_payload():
    mkv = standard_file()
    block = mkv["c1.b1"]
    snap = _snapshot_at(mkv.data, block.payload_start + 10, chunk=13)
    assert snap.synced and snap.partial_header == 0
    assert snap.leaf.id == ec.ID_SIMPLEBLOCK
    assert (snap.leaf.start, snap.leaf.end) == (block.start, block.end)
    assert [m.id for m in snap.masters] == [ec.ID_SEGMENT, ec.ID_CLUSTER]
    assert snap.masters[-1].end == mkv["c1"].end


def test_tracker_snapshot_inside_header_reports_partial():
    mkv = standard_file()
    block = mkv["c2.b0"]
    snap = _snapshot_at(mkv.data, block.start + 1)
    assert snap.synced and snap.partial_header == 1 and snap.leaf is None


def test_tracker_non_ebml_stream_is_disabled():
    tracker = _track(b"X" * 4096)
    assert tracker.is_ebml is False
    assert not tracker.snapshot().synced


def test_tracker_desyncs_on_malformed_vint():
    mkv = standard_file()
    target = mkv["c1.b2"]
    damaged = bytearray(mkv.data)
    damaged[target.start + 1] = 0x00  # size vint with no marker bit
    snap = _track(bytes(damaged[: target.start + 6])).snapshot()
    assert not snap.synced


def test_tracker_desyncs_on_truncated_size_past_parent():
    mkv = standard_file()
    target = mkv["c1.b2"]
    damaged = bytearray(mkv.data)
    damaged[target.start + 1] = 0x40  # 2-byte size 0x40xx: runs past Cluster
    damaged[target.start + 2] = 0xFF
    snap = _track(bytes(damaged[: target.start + 6])).snapshot()
    assert not snap.synced


def test_tracker_midfile_start_syncs_on_next_verified_cluster():
    mkv = standard_file()
    start = mkv["c1.b1"].payload_start + 5
    tracker = _track(mkv.data, start=start, chunk=11, content_length=len(mkv.data))
    assert tracker.snapshot().synced
    # Before reaching c2 it cannot know where it is.
    early = _track(
        mkv.data[: mkv["c2"].start], start=start, content_length=len(mkv.data)
    ).snapshot()
    assert not early.synced


def test_tracker_ignores_false_cluster_signature_in_payload():
    # A frame carrying the Cluster ID, a plausible size and even a Timestamp-
    # looking byte, but no valid child after it: must not be taken as a sync.
    fake = ID_CLUSTER + b"\x90" + ID_TIMESTAMP + b"\x81\x05" + b"\x00" * 40
    blocks = [simple_block("c1.b0", frame=b"\x55" * 20 + fake + b"\x55" * 20)]
    mkv = Mkv(
        [
            cluster("c0", 0, [simple_block("c0.b0")]),
            cluster("c1", 10, blocks),
            cluster("c2", 20, [simple_block("c2.b0")]),
        ]
    )
    start = mkv["c1.b0"].payload_start + 4
    tracker = ec.EbmlStreamTracker(start, len(mkv.data))
    tracker.feed(mkv.data[start : mkv["c2"].start])
    assert not tracker.snapshot().synced
    tracker.feed(mkv.data[mkv["c2"].start :])
    snap = tracker.snapshot()
    assert snap.synced and snap.pos == len(mkv.data)


def test_verify_cluster_rejects_bare_signature_and_accepts_real():
    mkv = standard_file()
    c2 = mkv["c2"]
    assert ec.verify_cluster(mkv.data, c2.start, c2.start, len(mkv.data)) == (
        ec.STATUS_OK
    )
    junk = ID_CLUSTER + b"\x84" + b"\x11\x22\x33\x44"
    assert ec.verify_cluster(junk, 0, 0, 0) == ec.STATUS_BAD
    assert ec.verify_cluster(mkv.data[: c2.start + 6], c2.start, c2.start, 0) == (
        ec.STATUS_NEED
    )


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def test_plan_payload_only_hole_keeps_zero_padding():
    mkv = standard_file(frame_len=400)
    block = mkv["c1.b1"]
    gap_start = block.payload_start + 50
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(snap, gap_start, gap_start + 100)
    assert plan.kind == ec.KIND_PAYLOAD
    assert (plan.start, plan.end) == (gap_start, gap_start + 100)
    assert plan.edits == ()
    out = _assert_structural(mkv.data, plan)
    assert out[gap_start : gap_start + 100] == bytes(100)


def test_plan_restores_block_track_number_when_payload_start_lost():
    mkv = standard_file(frame_len=400)
    block = mkv["c1.b1"]
    gap_start = block.payload_start
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(snap, gap_start, gap_start + 30)
    assert plan.kind == ec.KIND_PAYLOAD
    assert plan.edits == ((gap_start, b"\x81"),)
    _assert_structural(mkv.data, plan)


def test_plan_hole_spanning_blocks_voids_to_cluster_end():
    mkv = standard_file()
    b0 = mkv["c1.b0"]
    c1 = mkv["c1"]
    gap_start = b0.payload_start + 20
    gap_end = mkv["c1.b2"].payload_start + 5  # eats b1 and b2's header
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(snap, gap_start, gap_end)
    assert plan.kind == ec.KIND_VOID
    assert plan.end == c1.end
    assert [off for off, _ in plan.edits] == [b0.end]
    _assert_structural(mkv.data, plan)


def test_plan_damaged_element_header_voids_from_boundary():
    mkv = standard_file()
    b1 = mkv["c1.b1"]
    snap = _snapshot_at(mkv.data, b1.start)
    assert snap.leaf is None and snap.partial_header == 0
    plan = ec.plan_concealment(snap, b1.start, b1.start + 3)
    assert plan.kind == ec.KIND_VOID
    assert plan.edits[0][0] == b1.start
    _assert_structural(mkv.data, plan)


def test_plan_hole_spanning_clusters_resumes_at_next_verified_cluster():
    mkv = standard_file()
    gap_start = mkv["c1.b1"].payload_start + 7
    gap_end = mkv["c2.b1"].payload_start  # eats c1's tail and c2's head
    snap = _snapshot_at(mkv.data, gap_start)
    find = _cluster_finder(mkv.data)
    plan = ec.plan_concealment(snap, gap_start, gap_end, find_cluster=find)
    assert plan.kind == ec.KIND_VOID
    assert plan.end == mkv["c3"].start
    assert [off for off, _ in plan.edits] == [mkv["c1.b1"].end, mkv["c1"].end]
    assert find.calls[0][0] == gap_end
    _assert_structural(mkv.data, plan)


def test_plan_hole_in_blockgroup_pops_through_each_master():
    groups = [block_group("c1.g0", frame_len=300), block_group("c1.g1")]
    mkv = Mkv(
        [
            cluster("c0", 0, [simple_block("c0.b0")]),
            cluster("c1", 10, groups + [simple_block("c1.b9")]),
            cluster("c2", 20, [simple_block("c2.b0")]),
        ]
    )
    inner = mkv["c1.g0.block"]
    gap_start = inner.payload_start + 40
    gap_end = mkv["c1.g1"].start + 2
    snap = _snapshot_at(mkv.data, gap_start)
    assert [m.id for m in snap.masters][-1] == ec.ID_BLOCKGROUP
    plan = ec.plan_concealment(snap, gap_start, gap_end)
    assert plan.kind == ec.KIND_VOID
    assert [off for off, _ in plan.edits] == [inner.end, mkv["c1.g0"].end]
    assert plan.end == mkv["c1"].end
    _assert_structural(mkv.data, plan)


def test_plan_unknown_size_cluster_resumes_at_next_cluster():
    mkv = standard_file(size=UNKNOWN)
    gap_start = mkv["c1.b0"].payload_start + 9
    gap_end = mkv["c1.b2"].payload_start + 1
    snap = _snapshot_at(mkv.data, gap_start)
    assert snap.masters[-1].end is None
    plan = ec.plan_concealment(
        snap, gap_start, gap_end, find_cluster=_cluster_finder(mkv.data)
    )
    assert plan.kind == ec.KIND_VOID
    assert plan.end == mkv["c2"].start
    assert [off for off, _ in plan.edits] == [mkv["c1.b0"].end]
    _assert_structural(mkv.data, plan)


def test_plan_refuses_header_already_emitted():
    mkv = standard_file()
    b1 = mkv["c1.b1"]
    snap = _snapshot_at(mkv.data, b1.start + 1)
    plan = ec.plan_concealment(snap, b1.start + 1, b1.start + 50)
    assert plan.kind == ec.KIND_UNREPAIRED
    assert plan.reason == "header_already_emitted"
    assert not plan.structural and plan.edits == ()
    assert (plan.start, plan.end) == (b1.start + 1, b1.start + 50)


def test_plan_refuses_without_structural_context():
    mkv = standard_file()
    start = mkv["c1.b0"].payload_start + 3
    gap_start = mkv["c1.b1"].payload_start + 3
    snap = _snapshot_at(mkv.data, gap_start, start=start)
    plan = ec.plan_concealment(snap, gap_start, gap_start + 10)
    assert plan.kind == ec.KIND_UNREPAIRED
    assert plan.reason == "no_structural_context"


def test_plan_refuses_position_mismatch():
    mkv = standard_file()
    snap = _snapshot_at(mkv.data, mkv["c1.b1"].payload_start + 3)
    plan = ec.plan_concealment(snap, snap.pos + 1, snap.pos + 10)
    assert plan.reason == "position_mismatch"


def test_plan_refuses_header_region_before_first_cluster():
    mkv = standard_file()
    tracks = mkv["tracks"]
    snap = _snapshot_at(mkv.data, tracks.start)
    plan = ec.plan_concealment(
        snap, tracks.start, tracks.start + 4, find_cluster=_cluster_finder(mkv.data)
    )
    assert plan.reason == "header_region"


def test_plan_refuses_opaque_element():
    mkv = standard_file()
    tracks = mkv["tracks"]
    snap = _snapshot_at(mkv.data, tracks.payload_start + 1)
    plan = ec.plan_concealment(snap, tracks.payload_start + 1, tracks.end)
    assert plan.reason == "opaque_element"


def test_plan_falls_back_when_lookahead_finds_nothing():
    mkv = standard_file()
    gap_start = mkv["c2.b1"].payload_start + 7
    gap_end = mkv["c3.b0"].payload_start

    def find(_from, _limit):
        return None, "lookahead_failed"

    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(snap, gap_start, gap_end, find_cluster=find)
    assert plan.kind == ec.KIND_UNREPAIRED
    assert plan.reason == "no_verified_cluster:lookahead_failed"
    assert (plan.start, plan.end) == (gap_start, gap_end)


def test_plan_falls_back_without_lookahead_callback():
    mkv = standard_file()
    gap_start = mkv["c2.b1"].payload_start + 7
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(snap, gap_start, mkv["c3.b0"].payload_start)
    assert plan.kind == ec.KIND_UNREPAIRED


def test_plan_bounds_concealed_span():
    mkv = standard_file()
    gap_start = mkv["c1.b1"].payload_start + 7
    gap_end = mkv["c2.b1"].payload_start
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(
        snap,
        gap_start,
        gap_end,
        find_cluster=_cluster_finder(mkv.data),
        max_span=gap_end - gap_start + 1,
    )
    assert plan.reason == "span_exceeds_bound"


def test_plan_one_byte_void_slot_is_refused():
    # A boundary one byte before its parent's end cannot exist in a well-formed
    # file (every element is >= 2 bytes); it can only come from a mis-sync, so
    # the planner must refuse rather than emit something unparseable.
    seg = ec.Element(ec.ID_SEGMENT, None, None, None)
    clu = ec.Element(ec.ID_CLUSTER, 100, 105, 200)
    snap = ec.TrackerSnapshot(True, 199, (seg, clu), None, 0, True)
    plan = ec.plan_concealment(snap, 199, 250)
    assert plan.reason == "void_slot_too_small"


def test_plan_tiny_gap_at_boundary_uses_two_byte_void():
    blocks = [simple_block("c1.b0"), Node("c1.pad", b"\xec", b"")]
    mkv = Mkv(
        [
            cluster("c0", 0, [simple_block("c0.b0")]),
            cluster("c1", 10, blocks),
            cluster("c2", 20, [simple_block("c2.b0")]),
        ]
    )
    pad = mkv["c1.pad"]  # a 2-byte Void right at the Cluster's end
    snap = _snapshot_at(mkv.data, pad.start)
    plan = ec.plan_concealment(snap, pad.start, pad.start + 1)
    assert plan.kind == ec.KIND_VOID
    assert plan.edits == ((pad.start, b"\xec\x80"),)
    _assert_structural(mkv.data, plan)


def test_plan_render_subranges_are_consistent():
    mkv = standard_file()
    gap_start = mkv["c1.b1"].payload_start + 7
    snap = _snapshot_at(mkv.data, gap_start)
    plan = ec.plan_concealment(
        snap,
        gap_start,
        mkv["c2.b1"].payload_start,
        find_cluster=_cluster_finder(mkv.data),
    )
    whole = plan.render(plan.start, plan.end - plan.start)
    pieces = b"".join(
        plan.render(off, min(3, plan.end - off))
        for off in range(plan.start, plan.end, 3)
    )
    assert pieces == whole


# ---------------------------------------------------------------------------
# Look-ahead scanner
# ---------------------------------------------------------------------------


def test_cluster_scanner_skips_false_signature_and_handles_split_chunks():
    fake = ID_CLUSTER + b"\x90" + b"\x00" * 20
    mkv = Mkv(
        [
            cluster("c0", 0, [simple_block("c0.b0", frame=b"\x55" * 9 + fake)]),
            cluster("c1", 10, [simple_block("c1.b0")]),
        ]
    )
    origin = mkv["c0.b0"].payload_start
    for split in range(1, 60):
        scanner = ec.ClusterScanner(origin, len(mkv.data))
        tail = mkv.data[origin:]
        found = None
        for i in range(0, len(tail), split):
            found = scanner.feed(tail[i : i + split])
            if found is not None:
                break
        assert found == mkv["c1"].start, split


def test_cluster_scanner_returns_none_without_cluster():
    scanner = ec.ClusterScanner(0, 0)
    assert scanner.feed(b"\x00" * 4096) is None
    assert scanner.scanned == 4096


# ---------------------------------------------------------------------------
# Plan store
# ---------------------------------------------------------------------------


def _void_plan(start, end):
    return ec.ConcealPlan(ec.KIND_VOID, start, end, ((start, b"\xec\x80"),), "t")


def test_plan_store_scoped_by_source_and_refuses_overlap():
    store = ec.ConcealPlanStore(max_plans=3)
    assert store.add("a", _void_plan(100, 200))
    assert not store.add("a", _void_plan(150, 250))
    assert not store.add("a", ec.ConcealPlan(ec.KIND_UNREPAIRED, 300, 310, (), "x"))
    assert store.add("b", _void_plan(150, 250))
    assert store.covering("a", 199).start == 100
    assert store.covering("a", 200) is None
    assert store.covering("c", 150) is None
    assert store.next_start("a", 0) == 100
    assert store.next_start("a", 100) is None
    assert store.next_start("b", 0) == 150


def test_plan_store_is_bounded():
    store = ec.ConcealPlanStore(max_plans=2)
    for i in range(5):
        assert store.add("a", _void_plan(i * 100, i * 100 + 10))
    assert store.covering("a", 0) is None
    assert store.covering("a", 400) is not None
    assert store.plan_count() == 2
