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
    assert [one[0] for one in sizes.files] == [sum(one) for _subject, one in files]
    assert [one[2] for one in sizes.files] == [False, True, False, False]
    assert [one[3] for one in sizes.files] == [None] * 4
    signatures = [one[1] for one in sizes.files]
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


def test_both_readers_agree_on_odd_subjects_and_sizes():
    # ".par2" in the poster is not a repair file; a size that is not plain
    # digits makes its file say nothing, in the fast read and in the walk.
    body = _nzb(EPISODE + [("S01E02.mkv", _sizes(5)), ("x.par2", _sizes(9))])
    body = body.replace(b'poster="p"', b'poster="tool.par2@example.com"')
    odd = body.replace(b'bytes="', b'bytes=" ', 1)
    for one in (body, odd):
        fast = posting_fingerprint(one)
        walked = _walked_fingerprint(io.BytesIO(one))
        assert fast.payload == walked.payload and fast.payload is not None
        assert fast.payload[2] == frozenset(((1, 1), (1, 2)))
    assert _payload(odd)[0] < _payload(body)[0]
    assert len(_payload(odd)[1]) == len(_payload(body)[1]) - 1


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


def test_a_complete_repost_of_a_cut_short_pick_is_not_a_pack():
    volumes = [("show.s01e01.r{:02d}".format(n), _sizes(n, 60)) for n in range(10)]
    cut_short = _payload(_nzb(volumes[:6], post="pick"))
    complete = _payload(_nzb(volumes, post="repost"))

    # Larger than the pick and holding all of it, but one episode: the
    # copy that will play when the pick does not.
    assert complete[0] > 1.35 * cut_short[0]
    assert not holds_more(complete, cut_short)
    # The same files listed twice in one NZB are not a pack either.
    assert not holds_more(_payload(_nzb(volumes[:6] * 2, post="x")), cut_short)
    # Files that name no episode cannot show a pack.
    plain = [("{:08x}".format(n), _sizes(n, 400)) for n in range(4)]
    assert not holds_more(_payload(_nzb(plain)), _payload(_nzb(plain[:1], post="p")))


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


def test_the_scores_an_older_server_fails_over_by_follow_the_new_order():
    importlib.import_module("resources.lib.nzbget_resolver")
    from resources.lib.nzbget_fleet_run import _score_prepared
    from resources.lib.nzbget_resolver_dupes import _twins_first

    def rows():
        pick = {"link": "p", "_is_pick": True, "score": 100}
        one = {"link": "1", "score": 99}
        twin = {"link": "2", "score": 98, "_same_files": True}
        three = {"link": "3", "score": 97}
        return pick, one, twin, three

    pick, one, twin, three = rows()
    ordered = [row for row, _h, _t in _twins_first([(r, None, None) for r in rows()])]
    assert [row["link"] for row in ordered] == ["p", "2", "1", "3"]
    assert [row["score"] for row in ordered] == [100, 99, 98, 97]

    # Rows scored only after collection (a server that refused the bulk
    # call): the twin is scored right behind the pick.
    kept = [(row, None, None) for row in (pick, twin, one)]
    _score_prepared({"pick_score": 500}, kept, [one, twin, three])
    assert (pick["score"], twin["score"], one["score"], three["score"]) == (
        500,
        499,
        498,
        497,
    )

    # Rows without scores (the bulk call) are only reordered.
    bare = [({"link": "p", "_is_pick": True}, None, None), ({"link": "1"}, None, None)]
    assert [row for row, _h, _t in _twins_first(bare)] == [bare[0][0], bare[1][0]]
    assert "score" not in bare[1][0]


def test_a_pick_with_fixed_size_articles_changes_nothing(collect):
    rows = [
        ("pick", _nzb([("s01e01.mkv", _fixed(400))], post="pick")),
        ("pack", _nzb(EPISODE[:1] + OTHER_EPISODES, post="pack")),
        ("copy", _nzb(EPISODE, post="copy")),
    ]

    kept, dedup = collect(rows)

    assert kept == ["pick", "pack", "copy"]
    assert dedup.report.packs == 0
