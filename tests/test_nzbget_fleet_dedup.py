"""NZBGet duplicate fleet: same-release selection, same-posting dedup, prefetch."""

import threading
from unittest.mock import patch

import pytest
from resources.lib.nzbget_fleet_dedup import (
    FleetDedup,
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


def test_large_postings_compare_on_samples():
    ids = _ids("a", 5000)
    left = posting_fingerprint(_nzb(ids))
    assert left[0] == 5000 and len(left[1]) < 1000  # sampled
    relisted = posting_fingerprint(_nzb(ids[:-3] + _ids("r", 3)))
    other = posting_fingerprint(_nzb(_ids("b", 5000)))
    assert same_posting(left, relisted)
    assert not same_posting(left, other)


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


def test_clusters_remember_listings_across_phases():
    dedup = FleetDedup()
    dedup.clusters([{"link": "b", "size": "200", "pubdate": _PUB}])
    assert not dedup.clusters([{"link": "z", "size": "200", "pubdate": _PUB_90S}])


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
    assert [(m["link"], body) for m, body, _fp in got] == [
        ("a", _valid("a")),
        ("b2", _valid("b2")),
        ("c", _valid("c")),
    ]
    assert all(fp == posting_fingerprint(body) for _m, body, fp in got)


def test_prefetch_skips_a_non_nzb_body_to_the_next_listing():
    # An HTTP-200 login / rate-limit page is not a grab: try the next listing.
    pages = {"x": b"<html>rate limited</html>", "y": _valid("y")}
    got = list(prefetched_clusters([[{"link": "x"}, {"link": "y"}]], pages.get))
    assert [(m["link"], body) for m, body, _fp in got] == [("y", _valid("y"))]


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


def test_prefetch_degrades_to_inline_when_threads_cannot_start():
    with patch(
        "resources.lib.nzbget_fleet_dedup.threading.Thread",
        side_effect=RuntimeError("can't start new thread"),
    ):
        got = list(prefetched_clusters([[{"link": "a"}], [{"link": "b"}]], _valid))
    assert [body for _m, body, _fp in got] == [_valid("a"), _valid("b")]


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
    got = _same_release_backups(pick, rows, -1)
    assert [r["link"] for r in got] == ["b", "a"]
    assert got[1]["size"] == "9"
    assert [r["link"] for r in _same_release_backups(pick, rows, 1)] == ["b"]


# --- worker submit pipeline -------------------------------------------------


def _posting_bodies(mapping):
    return lambda url: _nzb(mapping[url]) if url in mapping else _raise(url)


def test_submit_candidates_skips_same_posting_and_appends_bodies():
    from resources.lib.nzbget_resolver import _submit_dupe_backups

    shared = _ids("s", 200)
    bodies = {
        "a": shared,
        "b": shared[:-1] + ["reup@post.example"],  # a's posting, relisted
        "c": _ids("c", 200),  # a byte-identical repost would share 0 IDs too
    }
    backups = [{"link": u, "title": u, "score": 10 - i} for i, u in enumerate("abc")]
    sink = []
    with patch(_FETCH, side_effect=_posting_bodies(bodies)), patch(
        _APPEND, side_effect=[(1, None), (2, None)]
    ) as append, patch(_VETO, return_value=False) as veto:
        live = _submit_dupe_backups(
            backups, "k", lambda *_a: "", submitted_sink=sink, dedup=FleetDedup()
        )
    assert [c.args[0] for c in append.call_args_list] == ["a", "c"]
    assert append.call_args_list[0].kwargs["nzb_bytes"] == _nzb(bodies["a"])
    assert live == sink == [1, 2]
    assert veto.call_count == 2


def test_unlimited_fleet_submits_everything_without_veto_probe():
    from resources.lib.nzbget_resolver import _spawn_dupe_backups

    from tests.test_nzbget_resolver import _dupe_ctx, _InlineThread

    backups = [
        {"link": "b{}".format(i), "title": "t", "score": 1000 - i} for i in range(40)
    ]
    dupe = {
        "key": "k",
        "pick_score": 1001,
        "score_base": 1001,
        "backups": backups,
        "max_backups": -1,
        "hydra_uploads": lambda: [{"link": "h{}".format(i)} for i in range(30)],
    }
    bodies = {"b{}".format(i): _ids("b{}-".format(i), 50) for i in range(40)}
    bodies.update({"h{}".format(i): _ids("h{}-".format(i), 50) for i in range(30)})
    counter = iter(range(1, 1000))
    with patch("resources.lib.nzbget_resolver.threading.Thread", _InlineThread), patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch("resources.lib.nzbget_resolver._warn_if_healthcheck_pauses"), patch(
        _FETCH, side_effect=_posting_bodies(bodies)
    ), patch(
        _APPEND, side_effect=lambda *a, **k: (next(counter), None)
    ) as append, patch(
        _VETO
    ) as veto:
        _spawn_dupe_backups(_dupe_ctx(dupe))
    urls = [c.args[0] for c in append.call_args_list]
    assert urls == ["b{}".format(i) for i in range(40)] + [
        "h{}".format(i) for i in range(30)
    ]
    scores = [c.kwargs["dupe_score"] for c in append.call_args_list]
    assert scores == sorted(scores, reverse=True) and max(scores) < 1001
    veto.assert_not_called()


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


def test_backup_of_the_picks_own_posting_is_skipped():
    from resources.lib.nzbget_resolver import _submit_backup_fleet

    shared = _ids("s", 200)
    bodies = {"relist": shared[:-1] + ["reup@post.example"], "other": _ids("o", 200)}
    dupe = {
        "key": "k",
        "backups": [
            {"link": "relist", "title": "t", "score": 9},
            {"link": "other", "title": "t", "score": 8},
        ],
        "max_backups": -1,
        "pick_fingerprint": posting_fingerprint(_nzb(shared)),
    }
    with patch(_FETCH, side_effect=_posting_bodies(bodies)), patch(
        _APPEND, return_value=(5, None)
    ) as append:
        _submit_backup_fleet(lambda *_a: "", threading.Event(), "k", dupe, [])
    assert [c.args[0] for c in append.call_args_list] == ["other"]


def test_submit_pick_fingerprints_and_appends_the_fetched_body():
    from types import SimpleNamespace

    from resources.lib.nzbget_resolver import _submit_pick

    body = _nzb(_ids("p", 20))
    ctx = SimpleNamespace(settings_getter=None, dupe={"pick_score": 7})
    with patch(_FETCH, return_value=body), patch(
        _APPEND, return_value=(1, None)
    ) as append:
        _submit_pick(ctx, "http://i/pick.nzb", "T", "k")
    assert append.call_args.kwargs["nzb_bytes"] == body
    assert ctx.dupe["pick_fingerprint"] == posting_fingerprint(body)


def test_submit_pick_without_fleet_does_not_prefetch():
    from types import SimpleNamespace

    from resources.lib.nzbget_resolver import _submit_pick

    ctx = SimpleNamespace(settings_getter=None, dupe=None)
    with patch(_FETCH) as fetch, patch(_APPEND, return_value=(1, None)) as append:
        _submit_pick(ctx, "http://i/pick.nzb", "T", "")
    fetch.assert_not_called()
    assert "nzb_bytes" not in append.call_args.kwargs


def test_capped_submit_stops_fetching_once_the_cap_is_met():
    from resources.lib.nzbget_resolver import _submit_candidates

    rows = [{"link": "u{}".format(i), "title": "t", "score": 1} for i in range(10)]
    with patch(
        "resources.lib.nzbget_fleet_dedup.threading.Thread",
        side_effect=RuntimeError("inline for determinism"),
    ), patch(_FETCH, side_effect=lambda url: _nzb([url + "@x"])) as fetch, patch(
        _APPEND, return_value=(1, None)
    ), patch(
        _VETO, return_value=False
    ):
        live = _submit_candidates(rows, "k", lambda *_a: "", limits=(1, None))
    assert live == [1]
    # The initial window of 4 plus the one refill started when the first
    # result was consumed -- never another fetch after the cap was met.
    assert fetch.call_count == 5


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


def test_fleet_pubdates_recorded_under_each_backups_own_title():
    from resources.lib.nzbget_resolver import _record_fleet_pubdates

    dupe = {
        "backups": [
            {"title": "Show S01E02 1080p WEB H264-GRP", "pubdate": _PUB},
            {"pubdate": _PUB_90S},
        ]
    }
    with patch("resources.lib.nzbget_resolver.record_download") as record:
        _record_fleet_pubdates(dupe, "Show.S01E02.1080p.WEB.h264-GRP")
    assert [c.args for c in record.call_args_list] == [
        ("Show S01E02 1080p WEB H264-GRP", _PUB),
        ("Show.S01E02.1080p.WEB.h264-GRP", _PUB_90S),
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
