# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""EBML gap concealment through the real pass-through proxy path.

Drives ``_StreamHandler._serve_proxy`` against a range-honouring fake upstream
that serves a small real MKV (``tests/fixtures/ebml/conceal_fixture.mkv``)
with configurable unreadable byte spans, standing in for missing Usenet
articles.

The fixture was generated with ffmpeg 7.1 (dev-only, not an add-on
dependency)::

    ffmpeg -f lavfi -i testsrc=size=96x64:rate=10 \\
        -f lavfi -i sine=frequency=440:sample_rate=4000 -t 5 \\
        -c:v mpeg4 -q:v 10 -g 5 -c:a pcm_u8 -ac 1 \\
        -cluster_time_limit 500 -cluster_size_limit 6000 \\
        -fflags +bitexact -map_metadata -1 conceal_fixture.mkv

Layout (byte offsets): header region up to 663, then ten known-size Clusters
starting at 663, 5647, 9397, 13072, 16847, 20727, 24449, 28106, 31826 and
35562, Cues at 37560. Blocks are about 1 KB, so the proxy's skip-probe sizes
are patched down to (256, 1024, 2048) to keep gaps at the same scale.

The ffmpeg demux check proves the concealed bytes demux past the gap; it is
NOT Kodi playback validation, which still needs a real device.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import ebml_conceal as ec
from resources.lib import stream_proxy
from resources.lib.stream_proxy import (
    _EBML_CONCEAL_STORE_KEY,
    _EBML_CONTAINER_KEY,
    _PASSTHROUGH_RUNTIME_SETTINGS_KEY,
    _STRICT_CONTRACT_MODE_OFF,
    _StreamHandler,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ebml" / "conceal_fixture.mkv"
DATA = FIXTURE.read_bytes()
SIZE = len(DATA)
PRIMARY = "http://host/movie.mkv"
FALLBACK = "http://fallback/movie.mkv"

CLUSTER_STARTS = (663, 5647, 9397, 13072, 16847, 20727, 24449, 28106, 31826, 35562)


class _Headers:  # pylint: disable=too-few-public-methods
    def __init__(self, values):
        self._values = {k.lower(): v for k, v in values.items()}

    def get(self, name, default=None):
        return self._values.get(name.lower(), default)


class _Response:
    """A 206 response that fails like a missing article at a dead byte."""

    def __init__(self, upstream, start, end):
        self._upstream = upstream
        self._pos = start
        self._end = end
        self.status = 206
        self.headers = _Headers(
            {
                "Content-Range": "bytes {}-{}/{}".format(start, end, SIZE),
                "Content-Length": str(end - start + 1),
            }
        )

    def getcode(self):
        return self.status

    def read(self, size=4096):
        if size is None or size < 0:
            size = 4096
        if self._pos > self._end:
            return b""
        dead = self._upstream.first_dead(self._pos, self._end)
        if dead == self._pos:
            self._upstream.gap_seen = True
            raise OSError("article missing at {}".format(self._pos))
        stop = min(self._pos + size, self._end + 1)
        if dead is not None:
            stop = min(stop, dead)
        chunk = self._upstream.data[self._pos : stop]
        self._pos = stop
        return chunk

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeUpstream:
    """Range-honouring stand-in for nzbdav/WebDAV with unreadable spans."""

    def __init__(self, data=DATA, missing=()):
        self.data = data
        self.missing = list(missing)
        self.requests = []
        self.fail_once = set()
        self.gap_seen = False
        self.lock = threading.Lock()

    def first_dead(self, start, end):
        best = None
        for lo, hi in self.missing:
            if lo <= end and hi > start:
                candidate = max(lo, start)
                best = candidate if best is None else min(best, candidate)
        return best

    def urlopen(self, req, timeout=None):  # pylint: disable=unused-argument
        spec = req.get_header("Range")
        start_text, end_text = spec.split("=", 1)[1].split("-", 1)
        start, end = int(start_text), int(end_text)
        with self.lock:
            self.requests.append((req.full_url, start, end))
        if (start, end) in self.fail_once:
            self.fail_once.discard((start, end))
            raise OSError("look-ahead connection reset")
        return _Response(self, start, end)

    def lookahead_requests(self, after):
        """Requests that only the look-ahead makes: start >= after, > 2 KB."""
        return [r for r in self.requests if r[1] >= after and r[2] - r[1] > 2048]


def _settings(**overrides):
    settings = {
        "contract_mode": _STRICT_CONTRACT_MODE_OFF,
        "density_breaker_enabled": False,
        "zero_fill_budget_enabled": False,
        "retry_ladder_enabled": False,
        "send_200_no_range_enabled": False,
        "passthrough_stall_wait_seconds": 0,
        "readahead_buffer_mb": 0,
    }
    settings.update(overrides)
    return settings


def _ctx(url=PRIMARY, content_type="video/x-matroska", **settings):
    return {
        "remote_url": url,
        "auth_header": None,
        "content_type": content_type,
        "content_length": SIZE,
        _PASSTHROUGH_RUNTIME_SETTINGS_KEY: _settings(**settings),
    }


def _handler(ctx, range_header):
    handler = _StreamHandler.__new__(_StreamHandler)
    handler.server = MagicMock()
    handler.server.stream_context = ctx
    handler.headers = {"Range": range_header} if range_header else {}
    handler.wfile = MagicMock()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    return handler


def _written(handler):
    return b"".join(bytes(call[0][0]) for call in handler.wfile.write.call_args_list)


def _header_values(handler, name):
    return [c[0][1] for c in handler.send_header.call_args_list if c[0][0] == name]


@pytest.fixture(name="proxy_env")
def fixture_proxy_env():
    """Patch probe sizes/delays to fixture scale; yield a serve() helper."""
    logs = []

    def serve(ctx, upstream, range_header="bytes=0-", write=None):
        handler = _handler(ctx, range_header)
        if write is not None:
            handler.wfile.write.side_effect = write
        with patch.object(
            stream_proxy, "urlopen", side_effect=upstream.urlopen
        ), patch.object(stream_proxy, "_SKIP_PROBE_SIZES", (256, 1024, 2048)), patch(
            "resources.lib.stream_proxy._PROBE_RETRY_DELAYS", ()
        ), patch.object(
            stream_proxy.xbmc, "log", side_effect=lambda msg, *_a: logs.append(msg)
        ):
            handler._serve_proxy(ctx)
        return handler, _written(handler)

    serve.logs = logs
    return serve


def _assert_parses(out):
    tracker = ec.EbmlStreamTracker(0, len(out))
    for i in range(0, len(out), 4096):
        tracker.feed(out[i : i + 4096])
    snap = tracker.snapshot()
    assert snap.synced, tracker.desync_reason
    assert snap.pos == len(out)


def _logged(logs, needle):
    return [m for m in logs if needle in m]


# ---------------------------------------------------------------------------
# Structural concealment on the proxy path
# ---------------------------------------------------------------------------


def test_gap_escaping_cluster_is_voided_to_next_verified_cluster(proxy_env):
    # Missing bytes start inside the last block of the Cluster at 13072; the
    # probe finds data again at 17024, past that Cluster's end (16847).
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx()
    handler, out = proxy_env(ctx, upstream)

    assert len(out) == SIZE
    assert out[:16000] == DATA[:16000]
    assert out[20727:] == DATA[20727:]  # resumed at the next verified Cluster
    assert out[16000:20727] != DATA[16000:20727]
    _assert_parses(out)
    # Exactly one bounded look-ahead read, starting where the probe resumed.
    assert [r[1] for r in upstream.lookahead_requests(17024)][:1] == [17024]
    plan = ctx[_EBML_CONCEAL_STORE_KEY].covering(PRIMARY, 16000)
    assert (plan.kind, plan.start, plan.end) == (ec.KIND_VOID, 16000, 20727)
    assert _logged(proxy_env.logs, "reason=ebml_conceal_void")
    assert _header_values(handler, "Content-Length") == [str(SIZE)]
    assert _header_values(handler, "Content-Range") == [
        "bytes 0-{}/{}".format(SIZE - 1, SIZE)
    ]


def test_gap_spanning_blocks_is_voided_to_cluster_end(proxy_env):
    upstream = FakeUpstream(missing=[(9500, 10500)])
    ctx = _ctx()
    _handler_obj, out = proxy_env(ctx, upstream)

    assert len(out) == SIZE
    assert out[:9500] == DATA[:9500]
    assert out[13072:] == DATA[13072:]
    _assert_parses(out)
    # No look-ahead needed: the only later read is the resume at Cluster end.
    assert upstream.lookahead_requests(10524) == [(PRIMARY, 13072, SIZE - 1)]
    plan = ctx[_EBML_CONCEAL_STORE_KEY].covering(PRIMARY, 9500)
    assert (plan.start, plan.end) == (9500, 13072)


def test_payload_only_gap_keeps_zero_padding(proxy_env):
    upstream = FakeUpstream(missing=[(9500, 9700)])
    ctx = _ctx()
    _handler_obj, out = proxy_env(ctx, upstream)

    expected = DATA[:9500] + bytes(256) + DATA[9756:]
    assert out == expected
    _assert_parses(out)
    assert _logged(proxy_env.logs, "reason=ebml_conceal_payload")


# ---------------------------------------------------------------------------
# Conservative fallbacks (legacy literal zero-fill, explicitly logged)
# ---------------------------------------------------------------------------


def _legacy(start, skip):
    return DATA[:start] + bytes(skip) + DATA[start + skip :]


def test_non_matroska_session_keeps_legacy_zero_fill(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx(url="http://host/movie.mp4", content_type="video/mp4")
    _handler_obj, out = proxy_env(ctx, upstream)
    assert out == _legacy(16000, 1024)
    assert upstream.lookahead_requests(17024)[0][1:] == (17024, SIZE - 1)
    assert len(upstream.lookahead_requests(17024)) == 1  # just the resume read
    assert _EBML_CONCEAL_STORE_KEY not in ctx


def test_non_ebml_bytes_disable_concealment_for_session(proxy_env):
    data = b"\x00\x00\x00\x20ftypisom" + DATA[12:]
    upstream = FakeUpstream(data=data, missing=[(16000, 16700)])
    ctx = _ctx()
    _handler_obj, out = proxy_env(ctx, upstream)
    assert out == data[:16000] + bytes(1024) + data[17024:]
    assert ctx[_EBML_CONTAINER_KEY] is False


def test_failed_lookahead_falls_back_to_legacy(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    upstream.fail_once.add((17024, SIZE - 1))
    ctx = _ctx()
    _handler_obj, out = proxy_env(ctx, upstream)
    assert out == _legacy(16000, 1024)
    assert _logged(proxy_env.logs, "no_verified_cluster:open_failed")
    assert _logged(proxy_env.logs, "NOT repaired")


def test_unavailable_preceding_context_falls_back(proxy_env):
    # The request starts mid-block; the gap comes before any verified Cluster
    # so there is no structural context to plan from.
    upstream = FakeUpstream(missing=[(11000, 11300)])
    ctx = _ctx()
    _handler_obj, out = proxy_env(ctx, upstream, "bytes=9500-")
    assert out == _legacy(11000, 1024)[9500:]
    assert _logged(proxy_env.logs, "detail=no_structural_context")
    assert upstream.lookahead_requests(12024) == [(PRIMARY, 12024, SIZE - 1)]


def test_gap_inside_already_emitted_header_falls_back(proxy_env):
    # 9413 starts a SimpleBlock; its ID byte is sent before the gap begins.
    upstream = FakeUpstream(missing=[(9414, 9800)])
    _handler_obj, out = proxy_env(_ctx(), upstream)
    assert out == _legacy(9414, 1024)
    assert _logged(proxy_env.logs, "detail=header_already_emitted")


@pytest.mark.parametrize(
    "overrides,patches,detail",
    [
        # 5% of 37760 bytes is 1888: the 1024-byte legacy skip fits, the
        # 4727-byte structural span (readable resync bytes included) does not.
        ({"zero_fill_budget_enabled": True}, {}, "session_budget"),
        (
            {"zero_fill_budget_enabled": True},
            {"_MAX_TOTAL_ZERO_FILL": 2000, "_SESSION_ZERO_FILL_RATIO_MAX": 1.0},
            "response_budget",
        ),
    ],
)
def test_budget_charges_full_concealed_span(proxy_env, overrides, patches, detail):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx(**overrides)
    with patch.multiple(stream_proxy, **patches) if patches else _null():
        _handler_obj, out = proxy_env(ctx, upstream)
    assert out == _legacy(16000, 1024)
    assert _logged(proxy_env.logs, "detail={} (span=4727)".format(detail))
    assert _EBML_CONCEAL_STORE_KEY not in ctx or (
        ctx[_EBML_CONCEAL_STORE_KEY].plan_count() == 0
    )


def test_plan_is_charged_and_logged_even_if_client_drops_mid_emit(proxy_env):
    # Kodi can drop the connection while a long recovery runs. The plan is
    # already committed (later requests replay it, uncharged), so the commit
    # itself must charge the budget and log, not the completed write.
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx()
    sent = [0]

    def write(data):
        if sent[0] + len(data) > 16000:
            raise BrokenPipeError("client went away")
        sent[0] += len(data)

    proxy_env(ctx, upstream, write=write)
    plan = ctx[_EBML_CONCEAL_STORE_KEY].covering(PRIMARY, 16000)
    assert plan is not None
    assert ctx["session_zero_fill_bytes"] == plan.span
    assert _logged(proxy_env.logs, "EBML-concealed unreadable span at byte 16000")


class _null:  # pylint: disable=invalid-name
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _kodi_like_monitor(abort_when):
    """Monitor whose waitForAbort follows real Kodi: timeout <= 0 waits forever.

    Live Kodi 21 blocks indefinitely on ``waitForAbort(0)`` (verified on a
    flatpak Kodi 21.3); a test double that returns immediately hid exactly
    that deadlock in the look-ahead. Here it fails the test instead.
    """
    monitor = MagicMock()

    def wait_for_abort(timeout=-1):
        if timeout is None or timeout <= 0:
            raise AssertionError("waitForAbort(%r) blocks forever in Kodi" % timeout)
        return abort_when()

    monitor.waitForAbort.side_effect = wait_for_abort
    monitor.abortRequested.side_effect = abort_when
    return monitor


def test_lookahead_never_calls_blocking_wait_for_abort(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    monitor = _kodi_like_monitor(lambda: False)
    with patch.object(stream_proxy.xbmc, "Monitor", return_value=monitor):
        _handler_obj, out = proxy_env(_ctx(), upstream)
    assert out[20727:] == DATA[20727:]
    assert _logged(proxy_env.logs, "reason=ebml_conceal_void")
    assert monitor.abortRequested.called


def test_cancellation_during_lookahead_falls_back(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    monitor = _kodi_like_monitor(lambda: upstream.gap_seen)
    with patch.object(stream_proxy.xbmc, "Monitor", return_value=monitor):
        _handler_obj, out = proxy_env(_ctx(), upstream)
    assert _logged(proxy_env.logs, "no_verified_cluster:aborted")
    # Aborted before opening the look-ahead: only the legacy resume read.
    assert upstream.lookahead_requests(17024) == [(PRIMARY, 17024, SIZE - 1)]
    assert out[16000:17024] == bytes(1024)


def test_verified_fallback_cutover_runs_before_concealment(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])

    def healthy_on_fallback(req, timeout=None):
        if req.full_url == FALLBACK:
            return _Response(FakeUpstream(), *_range(req))
        return upstream.urlopen(req, timeout)

    fallback = {"stream_url": FALLBACK, "stream_headers": {}}
    ctx = _ctx()
    ctx["fallback_sources"] = [fallback]
    picks = iter([fallback])
    with patch.object(
        _StreamHandler,
        "_select_live_fallback_source",
        side_effect=lambda *_a, **_k: next(picks, None),
    ):
        proxy = FakeProxy(healthy_on_fallback)
        _handler_obj, out = proxy_env(ctx, proxy)
    assert out == DATA
    assert ctx["remote_url"] == FALLBACK
    assert not _logged(proxy_env.logs, "ebml_conceal")


def _range(req):
    spec = req.get_header("Range").split("=", 1)[1]
    start, end = spec.split("-", 1)
    return int(start), int(end)


class FakeProxy:  # pylint: disable=too-few-public-methods
    def __init__(self, func):
        self.urlopen = func


# ---------------------------------------------------------------------------
# Repeat / overlapping ranges and source scoping
# ---------------------------------------------------------------------------


def test_repeated_and_overlapping_ranges_replay_identical_bytes(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx()
    _h, first = proxy_env(ctx, upstream)
    assert len(first) == SIZE

    # The articles come back: later reads must still match what was sent.
    upstream.missing = []
    _h, again = proxy_env(ctx, upstream, "bytes=15000-")
    assert again == first[15000:]
    # Upstream reads stopped at the plan start instead of reading its bytes.
    assert (PRIMARY, 15000, 15999) in upstream.requests

    h3, inside = proxy_env(ctx, upstream, "bytes=18000-19999")
    assert inside == first[18000:20000]
    assert _header_values(h3, "Content-Range") == ["bytes 18000-19999/{}".format(SIZE)]
    assert _header_values(h3, "Content-Length") == ["2000"]

    _h, head = proxy_env(ctx, upstream, "bytes=10000-16999")
    _h, tail = proxy_env(ctx, upstream, "bytes=17000-")
    assert head + tail == first[10000:]
    assert _logged(proxy_env.logs, "reason=ebml_conceal_replay")


def test_plans_are_scoped_to_their_source(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx()
    _h, first = proxy_env(ctx, upstream)
    assert ctx[_EBML_CONCEAL_STORE_KEY].covering(PRIMARY, 16000) is not None

    # After a cutover the session reads a different source: the primary's
    # plan must not be applied to it.
    ctx["remote_url"] = FALLBACK
    _h, fresh = proxy_env(ctx, FakeUpstream(), "bytes=15000-")
    assert fresh == DATA[15000:]
    assert ctx[_EBML_CONCEAL_STORE_KEY].covering(FALLBACK, 16000) is None
    assert first[16000:20727] != DATA[16000:20727]


# ---------------------------------------------------------------------------
# Real demuxer check (dev ffmpeg if available; not Kodi playback validation)
# ---------------------------------------------------------------------------


def _ffmpeg():
    return os.environ.get("NZBDAV_TEST_FFMPEG") or shutil.which("ffmpeg")


def _framecrc(ffmpeg, path):
    proc = subprocess.run(
        [
            ffmpeg,
            "-v",
            "warning",
            "-i",
            str(path),
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "framecrc",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    lines = [
        line
        for line in proc.stdout.decode("ascii", "replace").splitlines()
        if line and not line.startswith("#")
    ]
    return proc.returncode, lines, proc.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not available")
def test_ffmpeg_demuxes_past_concealed_gap(proxy_env, tmp_path):
    ffmpeg = _ffmpeg()
    upstream = FakeUpstream(missing=[(16000, 16700)])
    _h, out = proxy_env(_ctx(), upstream)
    original = tmp_path / "original.mkv"
    concealed = tmp_path / "concealed.mkv"
    original.write_bytes(DATA)
    concealed.write_bytes(out)

    rc_orig, orig_lines, _ = _framecrc(ffmpeg, original)
    rc, lines, stderr = _framecrc(ffmpeg, concealed)
    assert rc_orig == 0 and rc == 0, stderr
    # Demuxing continues to the end of the file past the gap,
    assert lines[-1] == orig_lines[-1]
    # every packet is a genuine one except at most the damaged block,
    foreign = [line for line in lines if line not in set(orig_lines)]
    assert len(foreign) <= 1, foreign
    # and only packets from the concealed Clusters' span went missing.
    assert len(orig_lines) - len(lines) <= 12
    # Legacy zero-fill makes ffmpeg log "0x00 at pos 16847 invalid as first
    # byte of an EBML number" and resync by scanning; concealment must not.
    assert "invalid" not in stderr.lower(), stderr


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not available")
def test_ffmpeg_flags_legacy_zero_fill_as_invalid_ebml(proxy_env, tmp_path):
    """Control: the preceding demux check can tell concealment from zero-fill."""
    upstream = FakeUpstream(missing=[(16000, 16700)])
    # A non-Matroska session takes the legacy path over the same MKV bytes.
    ctx = _ctx(url="http://host/movie.mp4", content_type="video/mp4")
    _h, out = proxy_env(ctx, upstream)
    assert out == _legacy(16000, 1024)
    legacy = tmp_path / "legacy.mkv"
    legacy.write_bytes(out)
    _rc, _lines, stderr = _framecrc(_ffmpeg(), legacy)
    assert "invalid as first byte of an EBML number" in stderr


def test_cached_byte0_prefix_never_covers_a_stored_plan(proxy_env):
    upstream = FakeUpstream(missing=[(16000, 16700)])
    ctx = _ctx()
    _h, first = proxy_env(ctx, upstream)

    # A byte-0 prefetch prefix that reaches past the plan start (original
    # bytes) must be cut at the plan so the replay still wins.
    upstream.missing = []
    _StreamHandler._cache_fallback_range(ctx, PRIMARY, None, SIZE, 0, DATA[:20000])
    _h, again = proxy_env(ctx, upstream)
    assert again == first


class _RacingStore(ec.ConcealPlanStore):  # pylint: disable=too-few-public-methods
    """Simulates another request committing the same gap's plan first."""

    def add(self, source_key, plan):
        super().add(source_key, plan)
        return False


def test_concurrent_commit_of_same_gap_replays_the_winner(proxy_env):
    reference = proxy_env(_ctx(), FakeUpstream(missing=[(16000, 16700)]))[1]
    ctx = _ctx()
    ctx[_EBML_CONCEAL_STORE_KEY] = _RacingStore()
    _h, out = proxy_env(ctx, FakeUpstream(missing=[(16000, 16700)]))
    assert out == reference
    assert ctx[_EBML_CONCEAL_STORE_KEY].plan_count() == 1


class _RacingLaterPlanStore(ec.ConcealPlanStore):
    """Another request commits a different plan inside this gap, once."""

    def __init__(self, racing_plan):
        super().__init__()
        self.racing_plan = racing_plan

    def add(self, source_key, plan):
        if self.racing_plan is None:
            return super().add(source_key, plan)
        super().add(source_key, self.racing_plan)
        self.racing_plan = None
        return False


def test_refused_commit_never_zero_fills_over_a_concurrent_plan(proxy_env):
    # While this request planned the gap at 16000, another request stored a
    # plan starting inside the pending legacy skip (16000 + 1024). The legacy
    # zeros must stop short of it, and its bytes must be replayed as stored.
    racing = ec.ConcealPlan(
        ec.KIND_PAYLOAD, 16300, 16500, ((16300, b"\xaa\xbb\xcc\xdd"),), "race"
    )
    ctx = _ctx()
    ctx[_EBML_CONCEAL_STORE_KEY] = _RacingLaterPlanStore(racing)
    _h, out = proxy_env(ctx, FakeUpstream(missing=[(16000, 16700)]))
    assert len(out) == SIZE
    assert out[16000:16300] == bytes(300)
    assert out[16300:16500] == racing.render(16300, 200)
