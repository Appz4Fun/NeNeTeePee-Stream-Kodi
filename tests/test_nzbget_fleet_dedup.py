"""NZBGet duplicate fleet: same-release selection, same-posting dedup, prefetch."""

import threading
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


def _valid(url):
    return _nzb([url + "@x"])


def test_prefetch_yields_in_rank_order_and_tries_next_listing():
    clusters = [[{"link": "a"}], [{"link": "b1"}, {"link": "b2"}], [{"link": "c"}]]

    def _fetch(url):
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

    def _fetch(url):
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
                lambda url: fetched.append(url) or _valid(url),
            )
        )
    assert got == [({"link": "a"}, None, None), ({"link": "b"}, None, None)]
    assert not fetched


def _raise(_url):
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
    return lambda url: _nzb(mapping[url]) if url in mapping else _raise(url)


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
    with patch(_FETCH, side_effect=lambda url: _nzb([url + "@x"])) as fetch, patch(
        _APPEND, return_value=(1, None)
    ), patch(_VETO, return_value=False):
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

    def _fetch(url):
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
    with patch(_FETCH, side_effect=_valid) as fetch, patch.object(
        NzbSpool, "save", return_value=None
    ), patch(_APPEND, return_value=(1, None)) as append:
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

    def _fetch(url):
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

    def _fetch(url):
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
    ), patch("resources.lib.nzbget_resolver.nzbget_api.cancel_jobs") as cancel, patch(
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

    def _fetch(url):
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

    def _fetch(url):
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

    def _fetch(url):
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

    def _fetch(url):
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
    import time

    release = threading.Event()
    cancel = threading.Event()
    waits = []

    def _fetch(url):
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
    import time

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

    def _fetch(url):
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
    import time

    from resources.lib.nzbget_fleet_run import submit_fleet

    release = threading.Event()
    getters = []

    def _hung(getter):
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
