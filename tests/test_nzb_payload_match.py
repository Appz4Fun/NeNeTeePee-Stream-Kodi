# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Telling a posting's files from its segment sizes, without an article read."""

import importlib
import io
import random
import threading

import pytest
from resources.lib import nzb_fingerprint_scan, nzbget_progress
from resources.lib.nzbget_fleet_dedup import (
    FleetDedup,
    NzbSpool,
    _walked_fingerprint,
    holds_more,
    posting_fingerprint,
    posting_payload,
    same_files,
)


def _sizes(seed, count=40):
    """Segment sizes as a poster's tool that varies them writes one file."""
    rng = random.Random(seed)
    return [rng.randrange(700000, 780000) for _ in range(count)]


def _fixed(count=40, last=31337):
    """Every article cut to one size, the last holding the remainder."""
    return [716800] * (count - 1) + [last]


def _nzb(files, post="a"):
    """An NZB of ``files``: ``(subject, segment sizes)`` each, posted as ``post``."""
    parts = []
    for number, (subject, sizes) in enumerate(files):
        segments = "".join(
            '<segment bytes="{}" number="{}">{}-{}-{}@post</segment>'.format(
                size, at + 1, post, number, at
            )
            for at, size in enumerate(sizes)
        )
        parts.append(
            '<file poster="p" date="1" subject="{} yEnc (1/{})">'
            "<groups><group>alt.binaries.test</group></groups>"
            "<segments>{}</segments></file>".format(subject, len(sizes), segments)
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">{}</nzb>'.format(
            "".join(parts)
        )
    ).encode("utf-8")


EPISODE = [("show.s01e01.mkv", _sizes(1, 400)), ("show.s01e01.nfo", [612])]
OTHER_EPISODES = [
    ("show.s01e0{}.mkv".format(number), _sizes(number, 400)) for number in (2, 3, 4)
]


def _payload(body):
    return posting_fingerprint(body).payload


def test_the_scan_gives_each_files_bytes_signature_and_repair_flag():
    files = [
        ("movie.mkv", _sizes(1)),
        ("movie.vol00+01.par2", _sizes(2)),
        ("movie.part01.rar", _fixed()),
        ("movie.nfo", [4096]),
    ]
    sizes = nzb_fingerprint_scan.FileSizes()

    found = nzb_fingerprint_scan.scan_fingerprint(io.BytesIO(_nzb(files)), sizes)

    assert found is not nzb_fingerprint_scan.UNUSUAL and len(found) == 121
    assert [total for total, _signature, _par2 in sizes.files] == [
        sum(one) for _subject, one in files
    ]
    assert [par2 for _total, _signature, par2 in sizes.files] == [
        False,
        True,
        False,
        False,
    ]
    signatures = [signature for _total, signature, _par2 in sizes.files]
    # One size on most segments, or too few segments, identifies nothing.
    assert signatures[0] and signatures[1] and signatures[2:] == [None, None]
    assert signatures[0] != signatures[1]


def test_the_parser_walk_reads_the_same_sizes_as_the_fast_scan(monkeypatch):
    body = _nzb(EPISODE + OTHER_EPISODES + [("x.par2", _sizes(9))])
    fast = posting_fingerprint(body)
    walked = _walked_fingerprint(io.BytesIO(body))

    assert list(walked) == list(fast)
    assert walked.payload == fast.payload and fast.payload is not None
    # A file split across the reader's chunks is still one file.
    monkeypatch.setattr(nzb_fingerprint_scan, "_CHUNK_BYTES", 4096)
    assert posting_fingerprint(body).payload == fast.payload


def test_a_fingerprint_without_usable_sizes_has_no_payload():
    fixed = _nzb([("movie.part01.rar", _fixed()), ("movie.part02.rar", _fixed())])
    assert _payload(fixed) is None
    unsized = _nzb(EPISODE).replace(b' bytes="', b' size="')
    assert _payload(unsized) is None
    assert posting_payload([]) is None and posting_payload(None) is None


def test_the_same_files_posted_again_are_a_twin():
    first = _payload(_nzb(EPISODE + [("a.par2", _sizes(7))], post="a"))
    again = _payload(_nzb(EPISODE + [("b.par2", _sizes(8, 60))], post="b"))

    # Other articles and other repair data, the same files.
    assert same_files(first, again)
    assert not holds_more(first, again) and not holds_more(again, first)


def test_another_encode_or_one_shared_file_is_no_twin():
    first = _payload(_nzb(EPISODE))
    other = _payload(_nzb([("show.s01e01.mkv", _sizes(50, 400))]))
    assert not same_files(first, other)

    # Two posts that share a proof picture and nothing else.
    proof = ("proof.jpg", _sizes(3, 14))
    left = _payload(_nzb([("left.mkv", _sizes(4, 400)), proof]))
    right = _payload(_nzb([("right.mkv", _sizes(5, 400)), proof]))
    assert not same_files(left, right)
    assert not same_files(first, None) and not same_files(None, None)


def test_a_pack_holds_the_episode_and_much_more():
    episode = _payload(_nzb(EPISODE))
    pack = _payload(_nzb(EPISODE[:1] + OTHER_EPISODES, post="pack"))

    assert holds_more(pack, episode)
    assert not holds_more(episode, pack)
    assert not same_files(pack, episode)
    # The episode with a few extras is still the episode.
    extras = _payload(_nzb(EPISODE + [("sample.mkv", _sizes(6, 20))], post="x"))
    assert not holds_more(extras, episode) and same_files(extras, episode)
    assert not holds_more(pack, None) and not holds_more(None, episode)


@pytest.fixture(name="collect")
def _collect(tmp_path):
    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib.nzbget_resolver_dupes import _collect_unique

    def run(rows):
        dedup = FleetDedup(spool_base=str(tmp_path))
        spool = NzbSpool(str(tmp_path))
        stream = [
            (
                dict(title=title, indexer=title, _is_pick=at == 0),
                body,
                posting_fingerprint(body),
            )
            for at, (title, body) in enumerate(rows)
        ]
        tally = {"wanted": 50, "seen": 0, "total": len(rows), "pick_pending": True}
        try:
            kept = _collect_unique(
                stream, (dedup, spool, tally, lambda url: None), threading.Event(), 50
            )
        finally:
            spool.close()
        return [candidate["title"] for candidate, _handle, _token in kept], dedup

    return run


def test_the_fleet_sends_twins_first_and_leaves_a_pack_out(collect):
    rows = [
        ("pick", _nzb(EPISODE, post="pick")),
        ("other wrapper", _nzb([("s01e01.r00", _sizes(60, 380))], post="w")),
        ("pack", _nzb(EPISODE[:1] + OTHER_EPISODES, post="pack")),
        ("twin", _nzb(EPISODE, post="twin")),
        ("fixed sizes", _nzb([("s01e01.mkv", _fixed(400))], post="f")),
        ("same posting", _nzb(EPISODE, post="pick")),
    ]

    kept, dedup = collect(rows)

    # The pick, then its twin, then the rest in the order they came. The
    # pack is not sent; a posting nothing can be said of is sent as before.
    assert kept == ["pick", "twin", "other wrapper", "fixed sizes"]
    assert dedup.report.packs == 1 and dedup.report.duplicates == 1
    assert nzbget_progress.not_sent_line(dedup.report) == (
        "Not sent: 1 duplicate upload, 1 pack under this name"
    )
    assert dedup.report.found() == 6


def test_a_pick_with_fixed_size_articles_changes_nothing(collect):
    rows = [
        ("pick", _nzb([("s01e01.mkv", _fixed(400))], post="pick")),
        ("pack", _nzb(EPISODE[:1] + OTHER_EPISODES, post="pack")),
        ("copy", _nzb(EPISODE, post="copy")),
    ]

    kept, dedup = collect(rows)

    assert kept == ["pick", "pack", "copy"]
    assert dedup.report.packs == 0
