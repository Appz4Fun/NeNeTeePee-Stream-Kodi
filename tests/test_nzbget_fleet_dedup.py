"""NZBGet duplicate fleet: same-release selection, same-posting dedup, prefetch."""

import pathlib
import tempfile
import threading
import time
from unittest.mock import patch

import pytest
from resources.lib.nzbget_fleet_dedup import (
    FleetDedup,
    NzbSpool,
    posting_fingerprint,
    prefetched_clusters,
    same_listing,
    same_posting,
    same_release,
)


@pytest.fixture(autouse=True)
def _download_through_fetch_stub():
    """Route the fleet's streaming ``download_nzb`` through ``fetch_nzb_bytes``.

    Tests stub ``nzbget_api.fetch_nzb_bytes`` (url -> bytes); the fleet now
    streams each NZB to a spool file via ``download_nzb``. This writes whatever
    the (patched) fetch returns to the requested path, so every fetch stub keeps
    working. Resolved at call time, so per-test patches are honored.
    """
    from resources.lib import nzbget_api

    def _download(url, dest_path, max_bytes=None):
        body = nzbget_api.fetch_nzb_bytes(url)
        with open(dest_path, "wb") as out:
            out.write(body)
        return len(body)

    with patch.object(nzbget_api, "download_nzb", side_effect=_download):
        yield


_APPEND = "resources.lib.nzbget_resolver.nzbget_api.append_nzb"
_FETCH = "resources.lib.nzbget_resolver.nzbget_api.fetch_nzb_bytes"
_VETO = "resources.lib.nzbget_resolver._copy_vetoed_after_append"


def _nzb(msgids):
    segments = "".join(
        '<segment bytes="700000" number="{}">{}</segment>'.format(i + 1, m)
        for i, m in enumerate(msgids)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
        '<file poster="p" date="1" subject="&quot;movie.mkv&quot; yEnc (1/1)">'
        "<groups><group>alt.binaries.test</group></groups>"
        "<segments>{}</segments></file></nzb>".format(segments)
    ).encode("utf-8")


def _ids(prefix, count):
    return ["{}{}@post.example".format(prefix, i) for i in range(count)]


# --- same_release -----------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("The Matrix 1999 1080p BluRay x264-GRP", True),  # other spelling
        ("The.Matrix.1999.1080p.BluRay.x264-OTHER", False),  # other group
        ("The.Matrix.1999.2160p.BluRay.x264-GRP", False),  # other resolution
        ("The.Matrix.1999.1080p.BluRay.x264.REPACK-GRP", False),  # REPACK
        ("The.Matrix.Reloaded.2003.1080p.BluRay.x264-GRP", False),  # other film
    ],
)
def test_same_release_movie(title, expected):
    pick = {"title": "The.Matrix.1999.1080p.BluRay.x264-GRP", "link": "p"}
    assert same_release(pick, {"title": title, "link": "x"}) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Show S01E02 1080p WEB H264-GRP", True),
        ("Show.S01E03.1080p.WEB.h264-GRP", False),  # other episode
        ("Show.S01E02.1080p.WEB.h264-NTB", False),  # other group
    ],
)
def test_same_release_episode(title, expected):
    pick = {"title": "Show.S01E02.1080p.WEB.h264-GRP", "link": "p"}
    assert same_release(pick, {"title": title, "link": "x"}) is expected


def test_same_release_fails_closed_on_error():
    with patch(
        "resources.lib.fallback_streams._same_content", side_effect=RuntimeError
    ):
        assert not same_release({"title": "a"}, {"title": "a"})


# --- same_listing -----------------------------------------------------------

_PUB = "Sat, 03 Oct 2026 12:00:00 +0000"
_PUB_90S = "Sat, 03 Oct 2026 12:01:30 +0000"
_PUB_10M = "Sat, 03 Oct 2026 12:10:00 +0000"


def test_same_listing_same_size_close_post_date():
    left = {"size": "100", "pubdate": _PUB}
    assert same_listing(left, {"size": "100", "pubdate": _PUB_90S})


def test_same_listing_rejects_far_post_date_or_other_size():
    base = {"size": "100", "pubdate": _PUB}
    assert not same_listing(base, {"size": "100", "pubdate": _PUB_10M})
    assert not same_listing(base, {"size": "101", "pubdate": _PUB})


def test_same_listing_needs_size_and_date():
    assert not same_listing({"size": "100"}, {"size": "100"})
    assert not same_listing({"pubdate": _PUB}, {"pubdate": _PUB})


def test_same_listing_reads_hydra_posted_epoch():
    picker_row = {"size": "100", "pubdate": _PUB}
    hydra_row = {"size": 100, "_posted_epoch": 1791028860}  # 12:01:00 UTC
    assert same_listing(picker_row, hydra_row)


# --- posting fingerprints ---------------------------------------------------


def test_relisting_with_one_reuploaded_segment_is_same_posting():
    ids = _ids("a", 300)
    relisted = ids[:-1] + ["reup@post.example"]
    left = posting_fingerprint(_nzb(ids))
    assert same_posting(left, posting_fingerprint(_nzb(relisted)))


def test_disjoint_repost_is_a_different_posting():
    left = posting_fingerprint(_nzb(_ids("a", 300)))
    right = posting_fingerprint(_nzb(_ids("b", 300)))
    assert not same_posting(left, right)


def test_large_postings_compare_every_article():
    ids = _ids("a", 5000)
    left = posting_fingerprint(_nzb(ids))
    assert len(left) == 5000  # every article kept, 4 bytes each
    relisted = posting_fingerprint(_nzb(ids[:-3] + _ids("r", 3)))
    other = posting_fingerprint(_nzb(_ids("b", 5000)))
    assert same_posting(left, relisted)
    assert not same_posting(left, other)


def test_overlap_just_over_one_percent_is_never_missed():
    # 6 shared of 512 = 1.17%: a 1-in-16 sample could drop all six (Codex r3).
    shared = _ids("s", 6)
    left = posting_fingerprint(_nzb(shared + _ids("a", 506)))
    right = posting_fingerprint(_nzb(shared + _ids("b", 506)))
    assert same_posting(left, right)
    under = posting_fingerprint(_nzb(shared[:5] + _ids("c", 507)))  # 0.98%
    assert not same_posting(left, under)


def test_fleet_known_posting_matches_same_posting():
    dedup = FleetDedup()
    first = posting_fingerprint(_nzb(_ids("a", 300)))
    dedup.remember_posting(first)
    assert dedup.known_posting(posting_fingerprint(_nzb(_ids("a", 299) + ["z@x"])))
    assert not dedup.known_posting(posting_fingerprint(_nzb(_ids("b", 300))))


def test_small_vs_large_fingerprints_compare_like_with_like():
    ids = _ids("a", 5000)
    assert same_posting(
        posting_fingerprint(_nzb(ids)), posting_fingerprint(_nzb(ids[:400]))
    )


def test_unparseable_nzb_has_no_fingerprint():
    assert posting_fingerprint(b"not xml") is None
    assert posting_fingerprint(_nzb([])) is None
    assert not same_posting(None, posting_fingerprint(_nzb(_ids("a", 3))))


# --- FleetDedup.clusters ----------------------------------------------------


def test_clusters_drop_pick_listing_and_group_same_listings():
    pick = {"link": "p", "size": "100", "pubdate": _PUB}
    rows = [
        {"link": "a", "size": "100", "pubdate": _PUB_90S},  # pick's posting
        {"link": "b", "size": "200", "pubdate": _PUB},
        {"link": "c", "size": "300", "pubdate": _PUB},
        {"link": "d", "size": "200", "pubdate": _PUB_90S},  # b's posting
        {"link": "e"},  # no evidence: kept alone
    ]
    clusters = FleetDedup(pick=pick).clusters(rows)
    assert [[r["link"] for r in c] for c in clusters] == [["b", "d"], ["c"], ["e"]]


def test_only_sent_listings_cover_later_phases():
    # Codex r5: a posting whose every listing failed stays uncovered, so a
    # later phase's mirror of it is still tried; a SENT one covers it.
    dedup = FleetDedup()
    failed = {"link": "b", "size": "200", "pubdate": _PUB}
    dedup.clusters([failed])
    mirror = {"link": "z", "size": "200", "pubdate": _PUB_90S}
    assert dedup.clusters([mirror]) == [[mirror]]
    dedup.remember_listing(failed)
    assert not dedup.clusters([mirror])


# --- prefetched_clusters ----------------------------------------------------


def _valid(url, **_kw):
    return _nzb([url + "@x"])


def test_prefetch_yields_in_rank_order_and_tries_next_listing():
    clusters = [[{"link": "a"}], [{"link": "b1"}, {"link": "b2"}], [{"link": "c"}]]

    def _fetch(url, **_kw):
        if url == "b1":
            raise OSError("403")
        return _valid(url)

    got = list(prefetched_clusters(clusters, _fetch))
    # The head keeps its slot (title, DupeScore); the mirror only supplies bytes.
    assert [(m["link"], body) for m, body, _fp in got] == [
        ("a", _valid("a")),
        ("b1", _valid("b2")),
        ("c", _valid("c")),
    ]
    assert all(fp == posting_fingerprint(body) for _m, body, fp in got)


def test_prefetch_skips_a_non_nzb_body_to_the_next_listing():
    # An HTTP-200 login / rate-limit page is not a grab: try the next listing.
    pages = {"x": b"<html>rate limited</html>", "y": _valid("y")}
    got = list(prefetched_clusters([[{"link": "x"}, {"link": "y"}]], pages.get))
    assert [(m["link"], body) for m, body, _fp in got] == [("x", _valid("y"))]


def test_prefetch_reports_head_when_every_listing_fails():
    got = list(prefetched_clusters([[{"link": "x"}, {"link": "y"}]], _raise))
    assert got == [({"link": "x"}, None, None)]


def test_prefetch_stops_on_cancel():
    cancel = threading.Event()
    clusters = [[{"link": str(i)}] for i in range(10)]
    seen = []
    for member, _body, _fp in prefetched_clusters(clusters, _valid, cancel):
        seen.append(member["link"])
        cancel.set()
    assert seen == ["0"]


def test_prefetch_close_stops_in_flight_fetches_moving_on():
    # Closing the stream (cap met / cancel) must stop an in-flight cluster
    # from grabbing its next listing.
    release = threading.Event()
    fetched = []

    def _fetch(url, **_kw):
        fetched.append(url)
        if url == "slow1":
            release.wait(5)
            raise OSError("timeout")
        return _valid(url)

    clusters = [[{"link": "a"}], [{"link": "slow1"}, {"link": "slow2"}]]
    stream = prefetched_clusters(clusters, _fetch, window=2)
    next(stream)
    stream.close()
    release.set()
    for _ in range(50):
        if "slow1" in fetched:
            break
        threading.Event().wait(0.01)
    threading.Event().wait(0.05)
    assert "slow2" not in fetched


def test_prefetch_uses_daemon_threads():
    started = []
    real_thread = threading.Thread

    def _spy(target=None, name=None, daemon=None):
        thread = real_thread(target=target, name=name, daemon=daemon)
        started.append(thread)
        return thread

    with patch("resources.lib.nzbget_fleet_dedup.threading.Thread", side_effect=_spy):
        list(prefetched_clusters([[{"link": "a"}], [{"link": "b"}]], _valid))
    assert started and all(t.daemon for t in started)


def test_prefetch_never_fetches_inline_when_threads_cannot_start():
    # Codex r7: an inline request could not be canceled, so a cluster whose
    # thread can't start reports no body (NZBGet fetches the URL itself).
    fetched = []
    with patch(
        "resources.lib.nzbget_fleet_dedup.threading.Thread",
        side_effect=RuntimeError("can't start new thread"),
    ):
        got = list(
            prefetched_clusters(
                [[{"link": "a"}], [{"link": "b"}]],
                lambda url, **_kw: fetched.append(url) or _valid(url),
            )
        )
    assert got == [({"link": "a"}, None, None), ({"link": "b"}, None, None)]
    assert not fetched


def _raise(_url, **_kw):
    raise OSError("down")


# --- router_play selection --------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected", [("-1", -1), ("-7", -1), ("0", 0), ("12", 12), ("x", -1), ("", -1)]
)
def test_parse_max_backups(raw, expected):
    from resources.lib.router_play import _parse_max_backups

    assert _parse_max_backups(raw) == expected


def test_same_release_backups_exact_first_then_other_names():
    from resources.lib.router_play import _same_release_backups

    pick = {"link": "p", "title": "Show.S01E02.1080p.WEB.h264-GRP"}
    rows = [
        pick,
        {"link": "a", "title": "Show S01E02 1080p WEB H264-GRP", "size": "9"},
        {"link": "b", "title": "show.s01e02.1080p.web.h264-grp"},
        {"link": "c", "title": "Show.S01E03.1080p.WEB.h264-GRP"},
        {"link": "d", "title": "Show.S01E02.1080p.WEB.h264-NTB"},
        {"link": "a", "title": "Show S01E02 1080p WEB H264-GRP"},  # dup link
    ]
    got = _same_release_backups(pick, rows)
    assert [r["link"] for r in got] == ["b", "a"]
    assert got[1]["size"] == "9"


# --- worker submit pipeline -------------------------------------------------


def _posting_bodies(mapping):
    return lambda url, **_kw: (_nzb(mapping[url]) if url in mapping else _raise(url))


def test_hydra_duplicate_upload_carries_posted_epoch():
    from resources.lib.hydra import _duplicate_upload_from_raw

    raw = {"title": "T", "link": "l", "size": 5, "epoch": 1790942400}
    assert _duplicate_upload_from_raw(raw, "T", "pick")["_posted_epoch"] == 1790942400
    raw["epoch"] = "junk"
    assert "_posted_epoch" not in _duplicate_upload_from_raw(raw, "T", "pick")


@pytest.mark.parametrize(
    "title",
    [
        "The.Matrix.1999.3D.HSBS.1080p.BluRay.x264-GRP",
        "The.Matrix.1999.German.DL.1080p.BluRay.x264-GRP",
        "The.Matrix.1999.MULTi.1080p.BluRay.x264-GRP",
        "The.Matrix.1999.1080p.BluRay.x264.DUBBED-GRP",
        "The.Matrix.1999.1080p.BluRay.x264.HC-GRP",
    ],
)
def test_same_release_rejects_language_and_3d_variants(title):
    # An NZBGet failover plays whatever it promotes (no stream-proxy byte check),
    # so a 3D / dubbed / foreign-language / hardsub variant is a different release.
    pick = {"title": "The.Matrix.1999.1080p.BluRay.x264-GRP", "link": "p"}
    assert not same_release(pick, {"title": title, "link": "x"})


def test_capped_submit_stops_fetching_once_the_cap_is_met():
    from resources.lib.nzbget_resolver import _submit_candidates

    rows = [{"link": "u{}".format(i), "title": "t", "score": 1} for i in range(10)]
    with patch(
        _FETCH, side_effect=lambda url, **_kw: _nzb([url + "@x"])
    ) as fetch, patch(_APPEND, return_value=(1, None)), patch(
        _VETO, return_value=False
    ):
        live = _submit_candidates(rows, "k", lambda *_a: "", limits=(1, None))
    assert live == [1]
    # A cap of one prefetches one NZB at a time and never refills after the
    # cap is met: exactly one indexer grab.
    assert fetch.call_count == 1


def test_loader_extras_drop_language_and_3d_variants_of_the_pick():
    from resources.lib.nzbget_resolver import _extra_backups_from_loader

    pick = {"title": "The.Matrix.1999.1080p.BluRay.x264-GRP", "link": "p"}
    cands = [
        {"link": "de", "title": "The.Matrix.1999.German.DL.1080p.BluRay.x264-GRP"},
        {"link": "3d", "title": "The.Matrix.1999.3D.HSBS.1080p.BluRay.x264-GRP"},
        {"link": "ok", "title": "The.Matrix.1999.1080p.BluRay.x265-OTHER"},
    ]
    got = _extra_backups_from_loader(lambda: cands, [], limit=None, pick=pick)
    assert [e["link"] for e in got] == ["ok"]


def test_completed_backup_is_ledger_recorded_under_its_own_title():
    # Codex r7: only the member NZBGet actually COMPLETED vouches for a row;
    # parked backups never downloaded.
    from resources.lib.nzbget_resolver import _record_fleet_pubdates

    dupe = {
        "backups": [
            {"title": "Show S01E02 1080p WEB H264-GRP", "pubdate": _PUB, "_nzbid": 7},
            {"title": "Parked", "pubdate": _PUB_90S, "_nzbid": 8},
        ],
        "extras": [{"title": "Alt Name", "pubdate": _PUB_90S, "_nzbid": 9}],
    }
    with patch("resources.lib.nzbget_resolver.record_download") as record:
        _record_fleet_pubdates(dupe, "Pick", 7)
        _record_fleet_pubdates(dupe, "Pick", 9)
        _record_fleet_pubdates(dupe, "Pick", 42)  # the pick itself completed
        _record_fleet_pubdates(dupe, "Pick", None)
    assert [c.args for c in record.call_args_list] == [
        ("Show S01E02 1080p WEB H264-GRP", _PUB),
        ("Alt Name", _PUB_90S),
    ]


def test_hydra_duplicate_lookup_runs_once_per_selection():
    from resources.lib.router_fallback import _fetch_fallback_extra_uploads

    selected = {"link": "p", "title": "T"}
    uploads = [{"link": "u", "title": "T"}]
    with patch(
        "resources.lib.router._hydra_duplicate_lookup_enabled", return_value=True
    ), patch(
        "resources.lib.hydra.fetch_release_duplicate_uploads", return_value=uploads
    ) as fetch:
        first = _fetch_fallback_extra_uploads(selected, None)
        second = _fetch_fallback_extra_uploads(selected, None)
    assert first == second == uploads
    fetch.assert_called_once()


def test_nzb_fetch_is_size_capped():
    from resources.lib import nzbget_api

    with patch.object(nzbget_api, "_http_get", return_value="<nzb/>") as get:
        nzbget_api.fetch_nzb_bytes("http://i/x.nzb")
    assert get.call_args.kwargs["max_bytes"] == nzbget_api._MAX_NZB_BYTES


def test_every_nzb_is_downloaded_and_spooled_before_the_first_send(tmp_path):
    # User design: download + dedupe everything first, keep the unique NZBs on
    # disk, send them all, and delete the spool only after all were sent.
    from resources.lib.nzbget_resolver_dupes import _submit_candidates

    events = []
    spool_files = []

    def _fetch(url, **_kw):
        events.append(("fetch", url))
        return _nzb([url + "@x"])

    def _append(url, name, **kwargs):
        spool_files.append(sorted(p.name for p in tmp_path.rglob("*.nzb")))
        events.append(("append", url))
        return len(events), None

    rows = [{"link": "u{}".format(i), "title": "t", "score": 9 - i} for i in range(6)]
    with patch(_FETCH, side_effect=_fetch), patch(_APPEND, side_effect=_append):
        live = _submit_candidates(
            rows, "k", lambda *_a: "", dedup=FleetDedup(spool_base=str(tmp_path))
        )
    kinds = [kind for kind, _url in events]
    assert kinds == ["fetch"] * 6 + ["append"] * 6
    assert [url for kind, url in events if kind == "append"] == [
        r["link"] for r in rows
    ]
    assert all(len(files) == 6 for files in spool_files)  # kept until all sent
    assert not list(tmp_path.rglob("*.nzb"))  # deleted after the batch
    assert len(live) == 6


def test_spool_keeps_bodies_in_memory_when_disk_is_unavailable():
    spool = NzbSpool(base_dir="/nonexistent/dir")
    with patch(
        "resources.lib.nzbget_fleet_dedup.tempfile.mkdtemp", side_effect=OSError
    ):
        memory_only = NzbSpool()
    assert NzbSpool.load(memory_only.save(b"abc")) == b"abc"
    assert NzbSpool.load(spool.save(b"xyz")) == b"xyz"  # system temp fallback
    spool.close()


def test_capped_fleet_backfills_a_vetoed_send_from_the_next_candidate():
    # Codex r4: cap 1, first append COPY-vetoed -> the second picker backup is
    # collected and sent; nothing beyond it is ever downloaded.
    from resources.lib.nzbget_resolver_dupes import _submit_candidates

    rows = [{"link": "u{}".format(i), "title": "t", "score": 9 - i} for i in range(5)]
    with patch(_FETCH, side_effect=_valid) as fetch, patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append, patch(_VETO, side_effect=[True, False]):
        live = _submit_candidates(rows, "k", lambda *_a: "", limits=(1, None))
    assert live == [2]
    assert [c.args[0] for c in append.call_args_list] == ["u0", "u1"]
    assert fetch.call_count == 2


def test_capped_round_never_fetches_past_what_it_can_use():
    # Codex r5: cap 4 and every append succeeds -> exactly 4 grabs.
    from resources.lib.nzbget_resolver_dupes import _submit_candidates

    rows = [{"link": "u{}".format(i), "title": "t", "score": 1} for i in range(10)]
    with patch(_FETCH, side_effect=_valid) as fetch, patch(
        _APPEND, side_effect=[(i, None) for i in range(1, 5)]
    ), patch(_VETO, return_value=False):
        live = _submit_candidates(rows, "k", lambda *_a: "", limits=(4, None))
    assert live == [1, 2, 3, 4]
    assert fetch.call_count == 4


def test_spool_memory_fallback_is_bounded():
    # Codex r5 (P1): with no writable folder, bodies past the memory budget are
    # not held -- the caller sends them as plain URL appends instead.
    with patch(
        "resources.lib.nzbget_fleet_dedup.tempfile.mkdtemp", side_effect=OSError
    ), patch.object(NzbSpool, "MEMORY_BUDGET", 10):
        spool = NzbSpool()
        assert spool.save(b"123456") == b"123456"
        assert spool.save(b"123456") is None  # 12 bytes > 10-byte budget


def test_unstorable_backup_is_dropped_not_refetched():
    # Codex r9: a backup body that can't be stored is dropped -- re-sending its
    # URL would make append_nzb fetch it on the resolve thread (uncancelable,
    # and a second indexer grab).
    from resources.lib.nzbget_resolver import _submit_candidates

    rows = [{"link": "u0", "title": "t", "score": 1}]
    # No spool folder (in-memory path) and the memory budget is exhausted.
    with patch(_FETCH, side_effect=_valid) as fetch, patch.object(
        NzbSpool, "reserve", return_value=None
    ), patch.object(NzbSpool, "save", return_value=None), patch(
        _APPEND, return_value=(1, None)
    ) as append:
        assert not _submit_candidates(rows, "k", lambda *_a: "")
    append.assert_not_called()
    assert fetch.call_count == 1


# --- foreground fleet (submit_fleet) ----------------------------------------


class _Dialog:
    def __init__(self, cancel_after=None):
        self.lines = []
        self._cancel_after = cancel_after

    def update(self, percent, message=""):
        self.lines.append((percent, message))

    def iscanceled(self):
        downloads = [m for _p, m in self.lines if m.startswith("Downloading NZBs")]
        return self._cancel_after is not None and len(downloads) >= self._cancel_after


def _fleet_ctx(dupe, dialog=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        settings_getter=lambda *_a: "",
        dupe=dupe,
        dialog=dialog if dialog is not None else _Dialog(),
        cancel_event=threading.Event(),
        submitted_nzbids=[],
    )


def _fleet_dupe(backups, **extra):
    dupe = {
        "key": "k",
        "pick_score": 1000,
        "score_base": 1000,
        "pick": {"link": "pick", "title": "T", "size": "100", "pubdate": _PUB},
        "backups": [
            dict(b, score=b.get("score", 999 - i)) for i, b in enumerate(backups)
        ],
        "max_backups": -1,
    }
    dupe.update(extra)
    return dupe


def _strings(msg_id):
    return {
        30615: "Looking for duplicate NZBs...",
        30616: "Downloading NZBs {} of {}",
        30617: "Sending {} NZBs to NZBGet...",
    }[msg_id]


@pytest.fixture
def _fleet_env():
    """No Kodi config reads, the real dialog strings, a counting NZBGet."""
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch("resources.lib.nzbget_resolver._warn_if_healthcheck_pauses"), patch(
        "resources.lib.nzbget_resolver._fleet_spool_base", return_value=None
    ), patch(
        "resources.lib.nzbget_resolver._string", side_effect=_strings
    ), patch(
        "resources.lib.nzbget_resolver._fmt",
        side_effect=lambda msg_id, *a: _strings(msg_id).format(*a),
    ):
        yield


def test_fleet_downloads_everything_then_sends_pick_first(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    events = []
    rows = [{"link": "b{}".format(i), "title": "t{}".format(i)} for i in range(3)]
    ctx = _fleet_ctx(_fleet_dupe(rows))

    def _fetch(url, **_kw):
        events.append("fetch " + url)
        return _valid(url)

    def _append(url, name, **kw):
        events.append("append " + url)
        return len(events), None

    with patch(_FETCH, side_effect=_fetch), patch(_APPEND, side_effect=_append) as ap:
        nzbid, error = submit_fleet(ctx, "pick", "T", "k")
    assert [e.split()[0] for e in events] == ["fetch"] * 4 + ["append"] * 4
    assert [e for e in events if e.startswith("append")][0] == "append pick"
    scores = [c.kwargs["dupe_score"] for c in ap.call_args_list]
    assert scores[0] == 1000 and scores == sorted(scores, reverse=True)
    assert nzbid == 5 and error is None
    messages = [m for _p, m in ctx.dialog.lines]
    assert messages[0] == "Looking for duplicate NZBs..."
    assert messages[1:5] == ["Downloading NZBs {} of 4".format(i) for i in range(1, 5)]
    assert messages[5] == "Sending 4 NZBs to NZBGet..."
    assert ctx.submitted_nzbids == [5, 6, 7, 8]


def test_fleet_skips_a_backup_of_the_picks_own_posting(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    shared = _ids("s", 200)
    bodies = {
        "pick": shared,
        "relist": shared[:-1] + ["reup@post.example"],
        "other": _ids("o", 200),
    }
    ctx = _fleet_ctx(_fleet_dupe([{"link": "relist"}, {"link": "other"}]))
    with patch(_FETCH, side_effect=_posting_bodies(bodies)), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "other"]


def test_fleet_pick_falls_back_to_a_mirror_of_its_posting(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    mirror = {"link": "mirror", "size": "100", "pubdate": _PUB_90S}
    hydra = [{"link": "hydra-mirror", "size": 100, "_posted_epoch": 1791028860}]
    ctx = _fleet_ctx(_fleet_dupe([mirror], hydra_uploads=lambda: hydra))
    fetched = []

    def _fetch(url, **_kw):
        fetched.append(url)
        if url in ("pick", "mirror"):
            raise OSError("indexer down")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, return_value=(9, None)
    ) as append:
        nzbid, _err = submit_fleet(ctx, "pick", "T", "k")
    assert fetched == ["pick", "mirror", "hydra-mirror"]
    # The pick keeps its slot (link, title, top score); the mirror only supplied
    # the bytes, and no mirror is ever sent as a separate backup.
    assert append.call_count == 1
    assert append.call_args.args[:2] == ("pick", "T")
    assert append.call_args.kwargs["nzb_bytes"] == _valid("hydra-mirror")
    assert nzbid == 9


def test_fleet_failed_pick_append_sends_no_backups(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}, {"link": "b1"}]))
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, return_value=(None, "Unauthorized")
    ) as append:
        nzbid, error = submit_fleet(ctx, "pick", "T", "k")
    assert append.call_count == 1
    assert (nzbid, error) == (None, "Unauthorized")


def test_fleet_cancel_while_downloading_sends_nothing(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    rows = [{"link": "b{}".format(i)} for i in range(6)]
    ctx = _fleet_ctx(_fleet_dupe(rows), dialog=_Dialog(cancel_after=2))
    with patch(_FETCH, side_effect=_valid), patch(_APPEND) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    append.assert_not_called()
    assert ctx.cancel_event.is_set()


def test_resolve_cancel_during_fleet_deletes_appends_and_exits_silently():
    from resources.lib.nzbget_resolver import _submit_poll_resolve

    failures = []
    ctx = _fleet_ctx({"key": "k"})
    ctx.on_failure = failures.append

    def _fleet(ctx_, *_a):
        ctx_.submitted_nzbids.append(77)
        ctx_.cancel_event.set()
        return None, None

    with patch(
        "resources.lib.nzbget_fleet_run.submit_fleet", side_effect=_fleet
    ), patch(
        "resources.lib.nzbget_resolver._cancel_jobs_in_background"
    ) as cancel, patch(
        "resources.lib.nzbget_resolver.poll_nzbget_job"
    ) as poll:
        assert _submit_poll_resolve(ctx, "pick", "T", None, None) is False
    cancel.assert_called_once()
    assert cancel.call_args.args[0] == [77]
    assert failures == [None]
    poll.assert_not_called()


def test_fleet_with_dupecheck_off_sends_only_the_pick(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}]))
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=True
    ), patch(_FETCH, side_effect=_valid) as fetch, patch(
        _APPEND, return_value=(3, None)
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in fetch.call_args_list] == ["pick"]
    assert [c.args[0] for c in append.call_args_list] == ["pick"]


def test_fleet_cap_backfills_a_vetoed_backup(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    rows = [{"link": "b{}".format(i)} for i in range(4)]
    ctx = _fleet_ctx(_fleet_dupe(rows, max_backups=1))
    with patch(_FETCH, side_effect=_valid) as fetch, patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ) as append, patch(_VETO, side_effect=[False, True, False]):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "b0", "b1"]
    assert fetch.call_count == 3  # pick + the cap + one replacement, no more


def test_fleet_unlimited_skips_the_veto_probe_and_flags_extras(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    dupe = _fleet_dupe(
        [{"link": "b0"}],
        loader=lambda: [{"link": "x", "title": "X", "pubdate": _PUB}],
    )
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ), patch(_VETO) as veto:
        submit_fleet(ctx, "pick", "T", "k")
    veto.assert_not_called()
    assert [e["link"] for e in dupe["extras"] if e.get("_submitted")] == ["x"]


def test_fleet_dead_backup_is_rescued_by_a_hydra_mirror(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    dead = {"link": "dead", "title": "t", "size": "200", "pubdate": _PUB}
    mirror = {"link": "mirror", "size": 200, "_posted_epoch": 1791028860}
    ctx = _fleet_ctx(_fleet_dupe([dead], hydra_uploads=lambda: [mirror]))

    def _fetch(url, **_kw):
        if url == "dead":
            raise OSError("gone")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    sent = append.call_args_list[1]
    assert sent.args[0] == "dead"  # the head keeps its slot
    assert sent.kwargs["nzb_bytes"] == _valid("mirror")


def test_fleet_retries_the_pick_fetch_so_its_relisting_is_still_caught(_fleet_env):
    # Codex r6: a transient pick fetch failure must not leave the fleet without
    # the pick's fingerprint (a relisting of it would take a backup slot).
    from resources.lib.nzbget_fleet_run import submit_fleet

    shared = _ids("s", 200)
    attempts = {"pick": 0}

    def _fetch(url, **_kw):
        if url == "pick":
            attempts["pick"] += 1
            if attempts["pick"] == 1:
                raise OSError("transient")
            return _nzb(shared)
        if url == "relist":
            return _nzb(shared[:-1] + ["reup@post.example"])
        return _valid(url)

    ctx = _fleet_ctx(_fleet_dupe([{"link": "relist"}, {"link": "other"}]))
    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "other"]
    assert append.call_args_list[0].kwargs["nzb_bytes"] == _nzb(shared)


def test_copy_vetoed_rows_are_not_ledger_recorded(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    dupe = _fleet_dupe([{"link": "b0"}, {"link": "b1"}], max_backups=2)
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ), patch(_VETO, side_effect=[False, True, False]):
        submit_fleet(ctx, "pick", "T", "k")
    flags = {b["link"]: b["_submitted"] for b in dupe["backups"]}
    assert flags == {"b0": False, "b1": True}


def test_fleet_never_lets_append_nzb_fetch_on_the_resolve_thread(_fleet_env):
    # Codex r9: every fleet append carries its body; a backup with none is
    # dropped and a pick with none fails the resolve with a clear error.
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([{"link": "dead"}, {"link": "ok"}]))

    def _fetch(url, **_kw):
        if url == "dead":
            raise OSError("gone")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "ok"]
    assert all("nzb_bytes" in c.kwargs for c in append.call_args_list)


def test_fleet_pick_that_cannot_be_downloaded_fails_without_a_blind_fetch(
    _fleet_env,
):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}]))

    def _fetch(url, **_kw):
        if url == "pick":
            raise OSError("indexer down")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(_APPEND) as append:
        nzbid, error = submit_fleet(ctx, "pick", "T", "k")
    append.assert_not_called()
    assert (nzbid, error) == (None, "NZB download failed")


def test_nzbids_compare_across_json_number_formats():
    # Codex r9: NZBGet history may serialize NZBID as a string.
    from resources.lib.nzbget_resolver import _record_fleet_pubdates, _stale_successes

    with patch(
        "resources.lib.nzbget_resolver._preexisting_success_ids",
        return_value=("5", "6"),
    ):
        assert _stale_successes("k", None, {"owned_nzbids": lambda: [6]}) == ("5",)
    dupe = {"backups": [{"title": "B", "pubdate": _PUB, "_nzbid": 7}]}
    with patch("resources.lib.nzbget_resolver.record_download") as record:
        _record_fleet_pubdates(dupe, "Pick", "7")
    assert [c.args for c in record.call_args_list] == [("B", _PUB)]


def test_cancel_during_send_stops_the_remaining_appends(_fleet_env):
    # Codex r7: the dialog is re-checked before every append.
    from resources.lib.nzbget_fleet_run import submit_fleet

    class _SendCancel(_Dialog):
        def iscanceled(self):
            sends = [m for _p, m in self.lines if m.startswith("Sending")]
            return len(sends) >= 3

    rows = [{"link": "b{}".format(i)} for i in range(6)]
    ctx = _fleet_ctx(_fleet_dupe(rows), dialog=_SendCancel())
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(i, None) for i in range(1, 8)]
    ) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    assert append.call_count == 2  # the 3rd tick saw the cancel
    assert ctx.submitted_nzbids == [1, 2]  # for the caller to delete


def test_stalled_fetch_wait_notices_a_cancel_without_waiting_it_out():
    # Codex r7: a hung indexer must not pin the resolve thread.
    release = threading.Event()
    cancel = threading.Event()
    waits = []

    def _fetch(url, **_kw):
        release.wait(10)
        return _valid(url)

    def _on_wait():
        waits.append(1)
        cancel.set()

    start = time.monotonic()
    got = list(
        prefetched_clusters([[{"link": "slow"}]], _fetch, cancel, on_wait=_on_wait)
    )
    release.set()
    assert time.monotonic() - start < 2
    assert waits and not got


def test_fleet_progress_treats_kodi_shutdown_as_cancel():
    from resources.lib.nzbget_fleet_run import _FleetProgress

    cancel = threading.Event()
    monitor = type("M", (), {"abortRequested": lambda self: True})()
    with patch("resources.lib.nzbget_resolver.xbmc.Monitor", return_value=monitor):
        assert _FleetProgress(None, cancel).canceled() is True
    assert cancel.is_set()


def test_capped_fleet_vetoed_pick_does_not_let_backups_exceed_the_cap(_fleet_env):
    # Codex r7: the cap bounds BACKUPS; a COPY-vetoed pick is not a backup slot.
    from resources.lib.nzbget_fleet_run import submit_fleet

    rows = [{"link": "b{}".format(i)} for i in range(4)]
    ctx = _fleet_ctx(_fleet_dupe(rows, max_backups=1))
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ) as append, patch(_VETO, side_effect=[True, False]):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "b0"]


def test_slow_hydra_lookup_is_abandoned_on_cancel(_fleet_env):
    # Codex r7: "Looking for duplicate NZBs..." must stay cancelable.
    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()

    def _slow_hydra():
        release.wait(10)
        return []

    class _CancelNow(_Dialog):
        def iscanceled(self):
            return True

    ctx = _fleet_ctx(_fleet_dupe([], hydra_uploads=_slow_hydra), dialog=_CancelNow())
    start = time.monotonic()
    with patch(_FETCH, side_effect=_valid), patch(_APPEND) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    release.set()
    assert time.monotonic() - start < 2
    append.assert_not_called()


def test_owned_backup_success_is_never_treated_as_stale():
    # Codex r7: a backup appended by the foreground fleet can complete before
    # polling starts; only successes from OTHER resolves are stale.
    from resources.lib.nzbget_resolver import _stale_successes

    with patch(
        "resources.lib.nzbget_resolver._preexisting_success_ids", return_value=(5, 6)
    ):
        assert _stale_successes("k", None, {"owned_nzbids": lambda: [1, 6]}) == (5,)
        assert _stale_successes("k", None, None) == (5, 6)


def test_failed_pick_stops_downloading_backups(_fleet_env):
    # Codex r10: no pick, no fleet -> don't spend grabs on backups.
    from resources.lib.nzbget_fleet_run import submit_fleet

    rows = [{"link": "b{}".format(i)} for i in range(20)]
    ctx = _fleet_ctx(_fleet_dupe(rows))
    fetched = []

    def _fetch(url, **_kw):
        fetched.append(url)
        if url == "pick":
            raise OSError("indexer down")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(_APPEND) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, "NZB download failed")
    append.assert_not_called()
    # Only the prefetch window already in flight (plus the pick's retry).
    assert len(fetched) <= 1 + 4 + 1


def test_spool_memory_budget_is_released_after_send():
    # Codex r10: a capped round's replacements must fit once earlier in-memory
    # bodies were sent.
    with patch(
        "resources.lib.nzbget_fleet_dedup.tempfile.mkdtemp", side_effect=OSError
    ), patch.object(NzbSpool, "MEMORY_BUDGET", 10):
        spool = NzbSpool()
        first = spool.save(b"12345678")
        assert spool.save(b"12345678") is None  # over budget
        spool.release(first)
        assert spool.save(b"12345678") == b"12345678"


def test_config_probe_is_abortable_and_reads_only_a_snapshot(_fleet_env):
    # Codex r10: a hung NZBGet config RPC must not pin the resolve thread, and
    # the probe thread never reads Kodi settings.
    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()
    getters = []

    def _hung(getter, **_kw):
        getters.append(getter)
        release.wait(10)
        return False

    class _CancelNow(_Dialog):
        def iscanceled(self):
            return True

    ctx = _fleet_ctx(_fleet_dupe([]), dialog=_CancelNow())
    start = time.monotonic()
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", side_effect=_hung
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api._get_settings",
        return_value=("http://n", "u", "p", "tv"),
    ), patch(
        _APPEND
    ) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    release.set()
    assert time.monotonic() - start < 2
    append.assert_not_called()
    assert getters and getters[0]("nzbget_url") == "http://n"


@pytest.mark.parametrize(
    "pick_title,row_title,expected",
    [
        ("Dune.1984.1080p.BluRay.x264-GRP", "Dune.1080p.BluRay.x264-GRP", False),
        ("Dune.1984.1080p.BluRay.x264-GRP", "Dune.2021.1080p.BluRay.x264-GRP", False),
        ("Dune.1984.1080p.BluRay.x264-GRP", "Dune 1984 1080p BluRay x264-GRP", True),
        ("Show.S01E02.1080p.WEB.h264-GRP", "Show.2019.S01E02.1080p.WEB.h264-GRP", True),
    ],
)
def test_nzbget_gates_require_the_movie_year(pick_title, row_title, expected):
    # Codex r11 (P1): a yearless candidate could be a different film.
    from resources.lib.nzbget_fleet_dedup import same_variant

    pick = {"title": pick_title, "link": "p"}
    row = {"title": row_title, "link": "x"}
    assert same_variant(pick, row) is expected
    if not expected:
        assert not same_release(pick, row)


def test_stale_successes_use_the_snapshot_taken_before_the_fleet():
    # Codex r11: a foreign success that lands while the fleet downloads is
    # not stale; only the pre-fleet snapshot (minus owned ids) is.
    from resources.lib.nzbget_resolver import _stale_successes

    with patch("resources.lib.nzbget_resolver._preexisting_success_ids") as late:
        got = _stale_successes(
            "k",
            None,
            {"owned_nzbids": lambda: [6], "preexisting_successes": (5, 6)},
        )
    assert got == (5,)
    late.assert_not_called()


def test_fleet_snapshots_successes_abortably_before_any_download(_fleet_env):
    # Codex r11/r12: the snapshot precedes every fetch and runs behind the
    # abortable wait; the poll then gets it via ctx.preexisting_successes.
    from resources.lib.nzbget_fleet_run import submit_fleet

    order = []
    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}]))

    def _snapshot(*_a, **_kw):
        order.append("snapshot")
        return (3,)

    def _fetch(url, **_kw):
        order.append("fetch " + url)
        return _valid(url)

    with patch(
        "resources.lib.nzbget_resolver._preexisting_success_ids", side_effect=_snapshot
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows", return_value=[]
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api._get_settings",
        return_value=("http://n", "u", "p", ""),
    ), patch(
        _FETCH, side_effect=_fetch
    ), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ):
        submit_fleet(ctx, "pick", "T", "k")
    assert order[0] == "snapshot"
    assert ctx.preexisting_successes == (3,)


def test_resolve_passes_the_fleet_snapshot_to_the_poll():
    from resources.lib.nzbget_resolver import _submit_poll_resolve

    ctx = _fleet_ctx({"key": "k"})
    ctx.on_failure = lambda _m: None
    ctx.timeout = 1
    ctx.interval = 0

    def _fleet(ctx_, *_a):
        ctx_.preexisting_successes = (3,)
        return 9, None

    with patch(
        "resources.lib.nzbget_fleet_run.submit_fleet", side_effect=_fleet
    ), patch(
        "resources.lib.nzbget_resolver.poll_nzbget_job",
        return_value={"outcome": "timeout"},
    ) as poll:
        _submit_poll_resolve(ctx, "pick", "T", None, None)
    assert poll.call_args.kwargs["fleet"]["preexisting_successes"] == (3,)


def test_hung_history_snapshot_is_cancelable(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()

    def _hung(*_a):
        release.wait(10)
        return ()

    class _CancelNow(_Dialog):
        def iscanceled(self):
            return True

    ctx = _fleet_ctx(_fleet_dupe([]), dialog=_CancelNow())
    start = time.monotonic()
    with patch(
        "resources.lib.nzbget_resolver._preexisting_success_ids", side_effect=_hung
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api._get_settings",
        return_value=("http://n", "u", "p", ""),
    ), patch(
        _APPEND
    ) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    release.set()
    assert time.monotonic() - start < 2
    append.assert_not_called()


def test_capped_fleet_skips_the_loader_when_rows_cover_the_cap(_fleet_env):
    # Codex r12: the loader downloads manifests; don't run it when the picker
    # rows + Hydra uploads already cover a positive cap.
    from resources.lib.nzbget_fleet_run import submit_fleet

    loader_calls = []
    dupe = _fleet_dupe(
        [{"link": "b0"}],
        max_backups=1,
        loader=lambda: loader_calls.append(1) or [{"link": "x"}],
    )
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ), patch(_VETO, return_value=False):
        submit_fleet(ctx, "pick", "T", "k")
    assert not loader_calls


def test_capped_fleet_consults_the_loader_when_rows_fail_to_fill_the_cap(
    _fleet_env,
):
    # Codex r13: a dead picker backup "covered" the cap by count only; the
    # loader must still supply the replacement.
    from resources.lib.nzbget_fleet_run import submit_fleet

    dupe = _fleet_dupe(
        [{"link": "dead"}],
        max_backups=1,
        loader=lambda: [{"link": "x", "title": "X"}],
    )
    ctx = _fleet_ctx(dupe)

    def _fetch(url, **_kw):
        if url == "dead":
            raise OSError("gone")
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append, patch(_VETO, return_value=False):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "x"]
    scores = [c.kwargs["dupe_score"] for c in append.call_args_list]
    assert scores[0] > scores[1]
    assert [e["link"] for e in dupe["extras"] if e.get("_submitted")] == ["x"]


def test_spool_files_are_freed_after_each_round(tmp_path):
    # Codex r13: a capped fleet's replacement rounds need the disk the
    # already-sent round no longer does.
    from resources.lib.nzbget_resolver_dupes import _submit_candidates

    rows = [{"link": "u{}".format(i), "title": "t", "score": 1} for i in range(4)]
    counts = []

    def _append(url, name, **kw):
        counts.append(len(list(tmp_path.rglob("*.nzb"))))
        return len(counts), None

    with patch(_FETCH, side_effect=_valid), patch(_APPEND, side_effect=_append), patch(
        _VETO, side_effect=[True, True, False]
    ):
        _submit_candidates(
            rows,
            "k",
            lambda *_a: "",
            dedup=FleetDedup(spool_base=str(tmp_path)),
            limits=(1, None),
        )
    # One NZB spooled per round, and the previous round's file is gone.
    assert counts == [1, 1, 1]
    assert not list(tmp_path.rglob("*.nzb"))


def test_failed_spool_write_leaves_no_partial_file(tmp_path):
    spool = NzbSpool(base_dir=str(tmp_path))
    real_open = open

    def _full_disk(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if "w" in mode:
            real_open(path, *args, **kwargs).close()
            raise OSError("No space left on device")
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=_full_disk):
        assert spool.save(b"abc") == b"abc"  # memory fallback
    assert not list(tmp_path.rglob("*.nzb"))
    spool.close()


def test_unsent_posting_does_not_block_a_later_mirror(_fleet_env):
    # Codex r14: a kept row whose append FAILED must not mark its posting
    # covered; a later phase's (loader) relisting of it is still sent.
    from resources.lib.nzbget_fleet_run import submit_fleet

    shared = _ids("s", 100)
    bodies = {"pick": _ids("p", 50), "b0": shared, "relist": shared[:-1] + ["r@x"]}
    dupe = _fleet_dupe(
        [{"link": "b0"}],
        max_backups=1,
        loader=lambda: [{"link": "relist", "title": "R"}],
    )
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_posting_bodies(bodies)), patch(
        _APPEND, side_effect=[(1, None), (None, "rejected"), (3, None)]
    ) as append, patch(_VETO, return_value=False):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick", "b0", "relist"]


def test_loader_is_asked_for_more_after_a_rejected_candidate(_fleet_env):
    # Codex r14: cap 1, the loader's first candidate is COPY-vetoed -> the
    # fleet raises its demand and the loader's next candidate fills the slot.
    from resources.lib.nzbget_fleet_run import submit_fleet

    limit = {"n": None}
    pool = [{"link": "x1", "title": "X1"}, {"link": "x2", "title": "X2"}]
    asked = []

    def _loader():
        asked.append(limit["n"])
        return pool[: limit["n"]]

    dupe = _fleet_dupe([], max_backups=1, loader=_loader, loader_limit=limit)
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ) as append, patch(
        _VETO, side_effect=[False, True, False]
    ):  # pick, x1, x2
        submit_fleet(ctx, "pick", "T", "k")
    assert asked == [1, 2]
    assert [c.args[0] for c in append.call_args_list] == ["pick", "x1", "x2"]


def test_shutdown_during_the_fleet_leaves_appended_jobs_running():
    # Codex r14: Kodi shutdown is not a user cancel -- nothing is deleted.
    from resources.lib.nzbget_resolver import _submit_poll_resolve

    failures = []
    ctx = _fleet_ctx({"key": "k"})
    ctx.on_failure = failures.append

    def _fleet(ctx_, *_a):
        ctx_.submitted_nzbids.append(77)
        ctx_.fleet_aborted = True
        ctx_.cancel_event.set()
        return None, None

    with patch(
        "resources.lib.nzbget_fleet_run.submit_fleet", side_effect=_fleet
    ), patch("resources.lib.nzbget_resolver.nzbget_api.cancel_jobs") as cancel, patch(
        "resources.lib.nzbget_resolver._string", return_value="aborted"
    ):
        assert _submit_poll_resolve(ctx, "pick", "T", None, None) is True
    cancel.assert_not_called()
    assert failures == ["aborted"]


def test_hung_append_is_abandoned_on_cancel_and_deleted_when_it_lands():
    # Codex r14: one stalled append RPC must not pin the resolve thread; if it
    # lands after the user canceled, it is deleted from NZBGet.
    from resources.lib.nzbget_resolver_dupes import _append_abortably

    release = threading.Event()
    cancel = threading.Event()
    deleted = []

    def _slow_append(*_a, **_k):
        release.wait(5)
        return 55, None

    dedup = FleetDedup(progress=lambda *_a: cancel.set())
    with patch(_APPEND, side_effect=_slow_append), patch(
        "resources.lib.nzbget_resolver.nzbget_api.cancel_jobs",
        side_effect=lambda ids, settings_getter=None: deleted.extend(ids),
    ):
        start = time.monotonic()
        got = _append_abortably(
            {"link": "u", "title": "t", "score": 1},
            b"<nzb/>",
            ("k", lambda *_a: "", False),
            (cancel, dedup),
        )
        assert time.monotonic() - start < 2
        assert got == (None, False)
        release.set()
        for _ in range(100):
            if deleted:
                break
            time.sleep(0.02)
    assert deleted == [55]


def test_in_memory_fallback_caps_backup_fetches_not_the_pick(_fleet_env):
    # Without a spool folder, parallel backup bodies are held in memory, so
    # they keep the tighter cap; the pick keeps the full ceiling.
    from resources.lib.nzbget_fleet_run import submit_fleet
    from resources.lib.nzbget_resolver_dupes import _FLEET_NZB_MAX_BYTES

    caps = {}

    def _fetch(url, max_bytes=None):
        caps[url] = max_bytes
        return _valid(url)

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}, {"link": "b1"}]))
    with patch.object(NzbSpool, "reserve", return_value=None), patch(
        _FETCH, side_effect=_fetch
    ), patch(_APPEND, side_effect=[(1, None), (2, None), (3, None)]):
        submit_fleet(ctx, "pick", "T", "k")
    assert caps["pick"] is None
    assert caps["b0"] == caps["b1"] == _FLEET_NZB_MAX_BYTES


def test_each_nzb_streams_to_disk_in_one_download(_fleet_env, tmp_path):
    # Codex r17: every NZB is streamed straight to a spool file at the full
    # ceiling -- one GET each, never held whole in memory, never re-fetched.
    from resources.lib import nzbget_api
    from resources.lib.nzbget_fleet_run import submit_fleet

    downloads = []

    def _download(url, dest_path, max_bytes=None):
        downloads.append((url, max_bytes))
        with open(dest_path, "wb") as out:
            out.write(_valid(url))

    ctx = _fleet_ctx(_fleet_dupe([{"link": "big"}, {"link": "b1"}]))
    with patch(
        "resources.lib.nzbget_resolver._fleet_spool_base", return_value=str(tmp_path)
    ), patch(_FETCH, side_effect=_valid), patch.object(
        nzbget_api, "download_nzb", side_effect=_download
    ), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    # Backups stream to disk; the pick is fetched into memory (Codex r18).
    assert sorted(downloads) == [("b1", None), ("big", None)]
    assert [c.args[0] for c in append.call_args_list] == ["pick", "big", "b1"]
    # The fleet's spool is gone; only the pick parked for the FORCE rescue
    # remains, until the resolve ends.
    assert not list(tmp_path.rglob("nzbdav-fleet-*/*.nzb"))
    assert [p.name for p in tmp_path.glob("*.nzb")] == [
        pathlib.Path(ctx.pick_nzb_path).name
    ]


def test_loader_batches_share_the_fleet_replacement_budget(_fleet_env):
    # Codex r16: cap 1 + budget 6; six rejected picker backups exhaust it, so
    # the loader is never consulted for more appends.
    from resources.lib.nzbget_fleet_run import submit_fleet

    loader_calls = []
    rows = [{"link": "b{}".format(i)} for i in range(6)]
    dupe = _fleet_dupe(
        rows,
        max_backups=1,
        loader=lambda: loader_calls.append(1) or [{"link": "x"}],
        loader_limit={"n": None},
    )
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(i, None) for i in range(1, 9)]
    ) as append, patch(_VETO, side_effect=[False] + [True] * 7):
        submit_fleet(ctx, "pick", "T", "k")
    assert append.call_count == 1 + 6  # the pick + the whole budget
    assert not loader_calls


def test_dialog_teardown_never_hides_a_kodi_shutdown():
    from resources.lib.nzbget_fleet_run import _FleetProgress

    class _Broken:  # pylint: disable=too-few-public-methods
        def iscanceled(self):
            raise RuntimeError("dialog gone")

    cancel = threading.Event()
    monitor = type("M", (), {"abortRequested": lambda self: True})()
    with patch("resources.lib.nzbget_resolver.xbmc.Monitor", return_value=monitor):
        progress = _FleetProgress(_Broken(), cancel)
        assert progress.canceled() is True
    assert progress.aborted and cancel.is_set()


def test_file_fingerprint_matches_the_in_memory_one(tmp_path):
    from resources.lib.nzbget_fleet_dedup import posting_fingerprint_file

    body = _nzb(_ids("a", 700))
    path = tmp_path / "x.nzb"
    path.write_bytes(body)
    assert posting_fingerprint_file(str(path)) == posting_fingerprint(body)
    (tmp_path / "bad.nzb").write_bytes(b"<html>rate limited")
    assert posting_fingerprint_file(str(tmp_path / "bad.nzb")) is None


def test_file_fingerprint_refuses_entity_declarations(tmp_path):
    from resources.lib.nzbget_fleet_dedup import posting_fingerprint_file

    evil = (
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY a "aaaa">]>'
        b"<nzb><file><segments><segment>&a;</segment></segments></file></nzb>"
    )
    path = tmp_path / "evil.nzb"
    path.write_bytes(evil)
    assert posting_fingerprint_file(str(path)) is None


def test_http_download_streams_caps_and_removes_partial_files(tmp_path):
    import io

    from resources.lib import http_util

    class _Resp(io.BytesIO):
        status = 200

    dest = tmp_path / "out.nzb"
    with patch.object(http_util, "urlopen", return_value=_Resp(b"x" * 200000)):
        assert http_util.http_download("http://i/a.nzb", str(dest)) == 200000
    assert dest.read_bytes() == b"x" * 200000
    with patch.object(http_util, "urlopen", return_value=_Resp(b"x" * 200000)):
        with pytest.raises(http_util.HttpResponseTooLarge):
            http_util.http_download("http://i/a.nzb", str(dest), max_bytes=1000)
    assert not dest.exists()


def test_loader_stop_event_prevents_new_manifest_fetches():
    # Codex r17: the fleet stops the loader's engine on cancel/shutdown.
    from types import SimpleNamespace

    from resources.lib import fallback_streams_select_streaming as engine

    stop = threading.Event()
    stop.set()
    selected = {"title": "T", "link": "p", "_fallback_stop": stop}
    state = SimpleNamespace(
        candidates=[],
        seen_article_digests=set(),
        max_candidates=3,
        seen_candidate_links=set(),
    )
    with patch.object(engine, "_start_selection_manifest_fetch") as start:
        engine._attach_selection_candidates_streaming(
            selected, iter([{"link": "c1"}, {"link": "c2"}]), state, False
        )
    start.assert_not_called()


def test_padded_prolog_cannot_hide_an_entity_declaration(tmp_path):
    # Codex r18 (P1): >1 MiB of whitespace/comments before an internal DOCTYPE.
    from resources.lib import xml_safety
    from resources.lib.nzbget_fleet_dedup import posting_fingerprint_file

    padding = b"<!--" + b"x" * (1100 * 1024) + b"-->\n"
    evil = (
        b'<?xml version="1.0"?>'
        + padding
        + b'<!DOCTYPE nzb [<!ENTITY a "aaaa">]>'
        + b"<nzb><file><segments><segment>&a;</segment></segments></file></nzb>"
    )
    path = tmp_path / "evil.nzb"
    path.write_bytes(evil)
    with patch.object(xml_safety, "_USING_DEFUSEDXML", False):
        assert posting_fingerprint_file(str(path)) is None
        # A normal NZB with an external DTD reference still parses.
        good = tmp_path / "good.nzb"
        good.write_bytes(
            b'<?xml version="1.0"?><!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN"'
            b' "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">'
            + _nzb(_ids("g", 3)).split(b"?>", 1)[1]
        )
        assert posting_fingerprint_file(str(good)) == posting_fingerprint(
            _nzb(_ids("g", 3))
        )


def test_streaming_fingerprint_detaches_parsed_segments(tmp_path):
    # Codex r18 (P1): segments must not accumulate under their parent.
    from resources.lib import nzbget_fleet_dedup

    path = tmp_path / "x.nzb"
    path.write_bytes(_nzb(_ids("a", 2000)))
    seen = []
    real = nzbget_fleet_dedup._local_name

    def _spy(tag):
        return real(tag)

    from resources.lib import xml_safety

    real_iterparse = xml_safety.safe_iterparse

    def _watch(p, events=("end",)):
        for event, elem in real_iterparse(p, events=events):
            yield event, elem
            if event == "end" and real(elem.tag) == "segments":
                seen.append(len(list(elem)))

    with patch.object(xml_safety, "safe_iterparse", side_effect=_watch), patch.object(
        nzbget_fleet_dedup, "_local_name", side_effect=_spy
    ):
        fingerprint = nzbget_fleet_dedup.posting_fingerprint_file(str(path))
    assert fingerprint is not None and len(fingerprint) == 2000
    assert seen == [0]  # every segment was detached as soon as it ended


def test_pick_is_fetched_into_memory_so_a_full_disk_cannot_fail_it(_fleet_env):
    # Codex r18: the spool's disk being full must not fail the pick.
    from resources.lib import nzbget_api
    from resources.lib.nzbget_fleet_run import submit_fleet

    def _disk_full(url, dest_path, max_bytes=None):
        raise OSError("No space left on device")

    ctx = _fleet_ctx(_fleet_dupe([]))
    with patch(_FETCH, side_effect=_valid), patch.object(
        nzbget_api, "download_nzb", side_effect=_disk_full
    ), patch(_APPEND, return_value=(5, None)) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (5, None)
    assert append.call_args.kwargs["nzb_bytes"] == _valid("pick")


def test_loader_widens_past_filtered_out_rows(_fleet_env):
    # Codex r18: the loader's first rows are a German variant (filtered out);
    # the fleet keeps widening until a suitable candidate appears.
    from resources.lib.nzbget_fleet_run import submit_fleet

    limit = {"n": None}
    pool = [
        {"link": "de", "title": "The.Matrix.1999.German.DL.1080p.BluRay.x264-GRP"},
        {"link": "ok", "title": "The.Matrix.1999.1080p.BluRay.x265-OTHER"},
    ]
    asked = []

    def _loader():
        asked.append(limit["n"])
        return pool[: limit["n"]]

    dupe = _fleet_dupe([], max_backups=1, loader=_loader, loader_limit=limit)
    dupe["pick"]["title"] = "The.Matrix.1999.1080p.BluRay.x264-GRP"
    ctx = _fleet_ctx(dupe)
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append, patch(_VETO, return_value=False):
        submit_fleet(ctx, "pick", "The.Matrix.1999.1080p.BluRay.x264-GRP", "k")
    assert asked == [1, 2]
    assert [c.args[0] for c in append.call_args_list] == ["pick", "ok"]


def test_cancel_cleanup_runs_off_the_resolve_thread_on_a_snapshot():
    # Codex r20: a stalled NZBGet must not hold a cancel open.
    from resources.lib.nzbget_resolver import _cancel_jobs_in_background

    release = threading.Event()
    seen = {}

    def _slow_cancel(ids, settings_getter=None):
        seen["ids"] = ids
        seen["url"] = settings_getter("nzbget_url")
        release.wait(5)

    with patch(
        "resources.lib.nzbget_resolver.nzbget_api._get_settings",
        return_value=("http://n", "u", "p", ""),
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.cancel_jobs", side_effect=_slow_cancel
    ):
        start = time.monotonic()
        _cancel_jobs_in_background([5, 6], None)
        assert time.monotonic() - start < 1
        for _ in range(100):
            if seen:
                break
            time.sleep(0.01)
        release.set()
    assert seen == {"ids": [5, 6], "url": "http://n"}


def test_fleet_scores_are_lifted_above_nzbgets_highest_same_key_score(_fleet_env):
    # Codex r20: after a clock rollback the wall-clock base can fall below an
    # earlier same-key item; the fleet shifts every score above it.
    from resources.lib.nzbget_fleet_run import submit_fleet

    dupe = _fleet_dupe([{"link": "b0"}, {"link": "b1"}])
    ctx = _fleet_ctx(dupe)
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.max_dupe_score_by_dupekey",
        return_value=5000,
    ), patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(1, None), (2, None), (3, None)]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    scores = [c.kwargs["dupe_score"] for c in append.call_args_list]
    assert scores[0] == 5001 and scores == sorted(scores, reverse=True)
    assert dupe["pick_score"] == 5001  # the FORCE rescue re-sends at this score


def test_max_dupe_score_reads_queue_and_history():
    from resources.lib import nzbget_api

    rows = {
        "history": [
            {"DupeKey": "k", "DupeScore": 7},
            {"DupeKey": "x", "DupeScore": 99},
        ],
        "listgroups": [{"DupeKey": "k", "DupeScore": "12"}],
    }
    with patch.object(
        nzbget_api,
        "_rpc_call",
        side_effect=lambda m, p, settings_getter=None: (rows[m], None),
    ):
        assert nzbget_api.max_dupe_score_by_dupekey("k") == 12
        assert nzbget_api.max_dupe_score_by_dupekey("none") is None


def test_force_rescue_resends_the_fleets_pick_body():
    # Codex r20: the pick URL may be dead/single-use; re-send the body.
    from types import SimpleNamespace

    from resources.lib.nzbget_resolver import _pick_rescue_callable

    ctx = SimpleNamespace(
        settings_getter=lambda *_a: "",
        dupe={"key": "k", "pick_score": 9},
        submitted_nzbids=[],
        dialog=None,
        cancel_event=threading.Event(),
        pick_nzb_path=None,
    )
    parked = pathlib.Path(tempfile.mkdtemp()) / "pick.nzb"
    parked.write_bytes(b"<nzb/>")
    ctx.pick_nzb_path = str(parked)
    rescue = _pick_rescue_callable(ctx, "http://dead/pick.nzb", "T")
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.active_group_by_name",
        return_value=False,
    ), patch(_APPEND, return_value=(42, None)) as append:
        assert rescue() == 42
    assert append.call_args.kwargs["nzb_bytes"] == b"<nzb/>"
    assert append.call_args.kwargs["dupe_mode"] == "FORCE"


def test_fleet_hands_the_pick_body_to_the_resolve(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([]))
    with patch(_FETCH, side_effect=_valid), patch(_APPEND, return_value=(1, None)):
        submit_fleet(ctx, "pick", "T", "k")
    # Parked on disk (Codex r21), not held in memory for the poll.
    assert pathlib.Path(ctx.pick_nzb_path).read_bytes() == _valid("pick")
    assert not hasattr(ctx, "pick_nzb_bytes")


def test_resolve_deletes_the_parked_pick_when_it_ends(tmp_path):
    from types import SimpleNamespace

    from resources.lib.nzbget_resolver import _discard_parked_pick

    parked = tmp_path / "nzbdav-pick-x.nzb"
    parked.write_bytes(b"x")
    ctx = SimpleNamespace(pick_nzb_path=str(parked))
    _discard_parked_pick(ctx)
    assert not parked.exists() and ctx.pick_nzb_path is None


def test_preflight_coalesces_rpcs_and_has_a_budget(_fleet_env):
    # Codex r21: one history + one listgroups + one config read, under a
    # shared budget -- an unresponsive NZBGet can't stall playback for minutes.
    from resources.lib import nzbget_api
    from resources.lib.nzbget_fleet_run import submit_fleet

    calls = []

    def _rpc(method, params, settings_getter=None):
        calls.append(method)
        return [], None

    ctx = _fleet_ctx(_fleet_dupe([]))
    with patch.object(nzbget_api, "_rpc_call", side_effect=_rpc), patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled",
        wraps=lambda getter, options=None: False,
    ), patch(_FETCH, side_effect=_valid), patch(_APPEND, return_value=(1, None)):
        submit_fleet(ctx, "pick", "T", "k")
    assert sorted(calls) == ["config", "history", "listgroups"]


def test_preflight_budget_abandons_a_hung_probe(_fleet_env):
    from resources.lib import nzbget_fleet_run
    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()

    def _hung(*_a, **_k):
        release.wait(10)
        return []

    ctx = _fleet_ctx(_fleet_dupe([]))
    start = time.monotonic()
    with patch.object(nzbget_fleet_run, "_PREFLIGHT_BUDGET_SECONDS", 0.5), patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows", side_effect=_hung
    ), patch(_FETCH, side_effect=_valid), patch(_APPEND, return_value=(1, None)):
        assert submit_fleet(ctx, "pick", "T", "k") == (1, None)
    release.set()
    assert time.monotonic() - start < 3


def test_max_dupe_score_matches_dupekeys_case_insensitively():
    from resources.lib import nzbget_api

    rows = {
        "history": [{"DupeKey": "IMDB=1|Movie", "DupeScore": 40}],
        "listgroups": [],
    }
    with patch.object(
        nzbget_api,
        "_rpc_call",
        side_effect=lambda m, p, settings_getter=None: (rows[m], None),
    ):
        assert nzbget_api.max_dupe_score_by_dupekey("imdb=1|movie") == 40


def test_failed_history_read_leaves_the_snapshot_unknown(_fleet_env):
    # Codex r22: a failed history RPC is "unknown" (None), not "no successes",
    # so the poll takes its own snapshot instead of trusting an empty one.
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([]))
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows", return_value=None
    ), patch(
        "resources.lib.nzbget_resolver._preexisting_success_ids"
    ) as snapshot, patch(
        _FETCH, side_effect=_valid
    ), patch(
        _APPEND, return_value=(1, None)
    ):
        submit_fleet(ctx, "pick", "T", "k")
    assert ctx.preexisting_successes is None
    snapshot.assert_not_called()


def test_append_landing_after_cancel_skips_the_veto_probe():
    from resources.lib.nzbget_resolver_dupes import _append_abortably

    cancel = threading.Event()

    def _append(*_a, **_k):
        cancel.set()  # the user canceled while this append was in flight
        return 9, None

    with patch(_APPEND, side_effect=_append), patch(_VETO) as veto, patch(
        "resources.lib.nzbget_resolver.nzbget_api.cancel_jobs"
    ):
        _append_abortably(
            {"link": "u", "title": "t", "score": 1},
            b"<nzb/>",
            ("k", lambda *_a: "", True),
            (cancel, FleetDedup()),
        )
        time.sleep(0.3)
    veto.assert_not_called()


def test_canceled_fleet_does_not_park_the_pick(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    class _CancelOnSend(_Dialog):
        def iscanceled(self):
            return any(m.startswith("Sending") for _p, m in self.lines)

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}]), dialog=_CancelOnSend())
    with patch("resources.lib.nzbget_fleet_run._park_pick_body") as park, patch(
        _FETCH, side_effect=_valid
    ), patch(_APPEND, return_value=(1, None)):
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    park.assert_not_called()


def test_preflight_worker_stops_between_rpcs_after_cancel(_fleet_env):
    # Codex r22: once the wait is abandoned, the probe starts no further RPC.
    from resources.lib.nzbget_fleet_run import _FleetProgress, _probe_nzbget_config

    cancel = threading.Event()
    calls = []

    def _history(*_a, **_k):
        calls.append("history")
        cancel.set()
        return []

    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows", side_effect=_history
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.max_dupe_score_by_dupekey",
        side_effect=lambda *a, **k: calls.append("listgroups"),
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.config_options",
        side_effect=lambda *a, **k: calls.append("config") or {},
    ):
        _probe_nzbget_config(lambda *_a: "", _FleetProgress(None, cancel), "k")
        time.sleep(0.3)
    assert calls == ["history"]


def test_in_memory_fingerprint_streams_instead_of_building_a_tree():
    # Codex r22 (P1): the pick's bytes use the streaming, detaching parser.
    from resources.lib import nzbget_fleet_dedup

    body = _nzb(_ids("a", 50))
    with patch(
        "resources.lib.nzb_manifest._parse_nzb_root",
        side_effect=AssertionError("must not build a full tree"),
    ):
        assert len(nzbget_fleet_dedup.posting_fingerprint(body)) == 50


def test_overlap_probe_works_in_bounded_chunks():
    # Codex r22 (P1): comparisons box at most _PROBE_CHUNK CRCs at a time,
    # and the result is the same as the exact whole-set count.
    from resources.lib import nzbget_fleet_dedup

    ids = _ids("a", 3000)
    left = posting_fingerprint(_nzb(ids))
    relisted = posting_fingerprint(_nzb(ids[:2990] + _ids("r", 10)))
    other = posting_fingerprint(_nzb(_ids("b", 3000)))
    with patch.object(nzbget_fleet_dedup, "_PROBE_CHUNK", 500):
        assert nzbget_fleet_dedup.same_posting(left, relisted)
        assert not nzbget_fleet_dedup.same_posting(left, other)


def test_unknown_dupecheck_sends_only_the_pick(_fleet_env):
    # Codex r23 (P1): config unreadable -> fail closed -> pick alone.
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}, {"link": "b1"}]))
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled",
        side_effect=lambda getter, options=None: options.get("dupecheck", "no") == "no",
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.config_options", return_value={}
    ), patch(
        _FETCH, side_effect=_valid
    ), patch(
        _APPEND, return_value=(1, None)
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick"]


def test_timed_out_preflight_sends_only_the_pick(_fleet_env):
    from resources.lib import nzbget_fleet_run
    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()

    def _hung(*_a, **_k):
        release.wait(10)
        return []

    ctx = _fleet_ctx(_fleet_dupe([{"link": "b0"}]))
    with patch.object(nzbget_fleet_run, "_PREFLIGHT_BUDGET_SECONDS", 0.4), patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows", side_effect=_hung
    ), patch(_FETCH, side_effect=_valid), patch(
        _APPEND, return_value=(1, None)
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    release.set()
    assert [c.args[0] for c in append.call_args_list] == ["pick"]


def test_pick_body_stays_in_memory_when_no_temp_folder_takes_it(_fleet_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _fleet_ctx(_fleet_dupe([]))
    with patch(
        "resources.lib.nzbget_fleet_run._park_pick_body", return_value=None
    ), patch(_FETCH, side_effect=_valid), patch(_APPEND, return_value=(1, None)):
        submit_fleet(ctx, "pick", "T", "k")
    assert ctx.pick_nzb_path is None
    assert ctx.pick_nzb_bytes == _valid("pick")


def test_force_rescue_starts_no_append_after_a_cancel_during_lookup():
    from types import SimpleNamespace

    from resources.lib.nzbget_resolver import _pick_rescue_callable

    cancel = threading.Event()
    ctx = SimpleNamespace(
        settings_getter=lambda *_a: "",
        dupe={"key": "k", "pick_score": 9},
        submitted_nzbids=[],
        dialog=None,
        cancel_event=cancel,
        pick_nzb_path=None,
    )

    def _lookup(*_a, **_k):
        cancel.set()  # canceled while the lookup RPC was in flight
        return False

    rescue = _pick_rescue_callable(ctx, "http://i/x.nzb", "T")
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.active_group_by_name",
        side_effect=_lookup,
    ), patch(_APPEND) as append:
        rescue()
        time.sleep(0.3)
    append.assert_not_called()


def test_fingerprints_past_the_memory_budget_spill_to_disk(tmp_path):
    # Codex r23 (P1): an unlimited fleet's retained fingerprints are bounded.
    dedup = FleetDedup(spool_base=str(tmp_path))
    first = posting_fingerprint(_nzb(_ids("a", 300)))
    second = posting_fingerprint(_nzb(_ids("b", 300)))
    with patch.object(FleetDedup, "MEMORY_BUDGET", 300 * 4):
        dedup.commit_posting(first)  # fits the budget: in memory
        dedup.commit_posting(second)  # over budget: spilled
    spilled = list(tmp_path.rglob("*.crc"))
    assert len(spilled) == 1
    # Spilled fingerprints still dedupe exactly.
    relisted = posting_fingerprint(_nzb(_ids("b", 299) + ["z@x"]))
    assert dedup.known_posting(relisted)
    assert not dedup.known_posting(posting_fingerprint(_nzb(_ids("c", 300))))
    dedup.close()
    assert not list(tmp_path.rglob("*.crc"))


def test_spilled_fingerprints_are_not_kept_alive_by_the_round(tmp_path):
    # Codex r24 (P1): past the budget a remembered fingerprint lives only on
    # disk -- FleetDedup keeps a token, never the array itself.
    import gc
    import weakref

    dedup = FleetDedup(spool_base=str(tmp_path))
    with patch.object(FleetDedup, "MEMORY_BUDGET", 0):
        fingerprint = posting_fingerprint(_nzb(_ids("a", 300)))
        ref = weakref.ref(fingerprint)
        token = dedup.remember_posting(fingerprint)
        del fingerprint
        gc.collect()
        assert ref() is None
        assert dedup.known_posting(posting_fingerprint(_nzb(_ids("a", 300))))
        dedup.commit_posting(token)
        dedup.end_round()
    assert dedup.known_posting(posting_fingerprint(_nzb(_ids("a", 300))))
    dedup.close()


def test_a_full_spill_disk_keeps_the_fingerprint_in_memory(tmp_path):
    # Codex r24 (P2): mkstemp failing (disk full, no inodes) degrades to memory.
    dedup = FleetDedup(spool_base=str(tmp_path))
    fingerprint = posting_fingerprint(_nzb(_ids("a", 300)))
    with patch.object(FleetDedup, "MEMORY_BUDGET", 0), patch(
        "resources.lib.nzbget_fleet_dedup.tempfile.mkstemp",
        side_effect=OSError(28, "No space left on device"),
    ):
        token = dedup.remember_posting(fingerprint)
    dedup.commit_posting(token)
    assert dedup.known_posting(posting_fingerprint(_nzb(_ids("a", 299) + ["z@x"])))
    dedup.close()
