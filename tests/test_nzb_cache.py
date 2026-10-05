"""Canceled picks keep their NZB on the box for a day (owner request)."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import patch

from resources.lib import nzb_cache


def _nzb_file(tmp_path, name="pick.nzb", body=b"<nzb>pick</nzb>"):
    path = tmp_path / name
    path.write_bytes(body)
    return str(path)


def test_keep_then_load_within_a_day(tmp_path):
    source = _nzb_file(tmp_path)
    assert nzb_cache.keep("https://hydra/getnzb/api/1?apikey=S", source, now=1000)
    assert not os.path.exists(source)  # moved, not copied
    # Same release with a rotated key still hits; the key is never on disk.
    assert nzb_cache.load("https://hydra/getnzb/api/1?apikey=NEW", now=2000) == (
        b"<nzb>pick</nzb>"
    )
    for name in os.listdir(nzb_cache._cache_dir()):
        with open(os.path.join(nzb_cache._cache_dir(), name), "rb") as handle:
            assert b"apikey" not in handle.read()
        assert name.endswith(".nzb") and "apikey" not in name
    assert (
        nzb_cache.load("https://hydra/getnzb/api/1", now=1000 + nzb_cache.TTL_SECONDS)
        is None
    )


def test_missing_or_bad_inputs_fail_soft(tmp_path):
    assert nzb_cache.load("https://hydra/getnzb/api/404") is None
    assert not nzb_cache.keep("https://hydra/x", str(tmp_path / "missing.nzb"))
    assert not nzb_cache.keep("", _nzb_file(tmp_path))


def test_cache_is_capped(tmp_path):
    with patch.object(nzb_cache, "_MAX_FILES", 2):
        for i in range(3):
            source = _nzb_file(tmp_path, "p{}.nzb".format(i))
            nzb_cache.keep("https://hydra/{}".format(i), source, now=1000 + i)
    assert len(os.listdir(nzb_cache._cache_dir())) == 2
    assert nzb_cache.load("https://hydra/0", now=1010) is None


def _ctx(path, canceled=True, aborted=False):
    event = threading.Event()
    if canceled:
        event.set()
    return SimpleNamespace(
        pick_nzb_path=path,
        cancel_event=event,
        fleet_aborted=aborted,
        pick_nzb_bytes=None,
    )


def test_user_cancel_keeps_the_parked_pick(tmp_path):
    from resources.lib.nzbget_resolver import _discard_parked_pick

    ctx = _ctx(_nzb_file(tmp_path))
    _discard_parked_pick(ctx, "https://hydra/getnzb/api/7")
    assert ctx.pick_nzb_path is None
    assert nzb_cache.load("https://hydra/getnzb/api/7") == b"<nzb>pick</nzb>"


def test_success_or_shutdown_deletes_the_parked_pick(tmp_path):
    from resources.lib.nzbget_resolver import _discard_parked_pick

    for ctx in (
        _ctx(_nzb_file(tmp_path, "a.nzb"), canceled=False),
        _ctx(_nzb_file(tmp_path, "b.nzb"), aborted=True),
    ):
        path = ctx.pick_nzb_path
        _discard_parked_pick(ctx, "https://hydra/getnzb/api/8")
        assert not os.path.exists(path)
    assert nzb_cache.load("https://hydra/getnzb/api/8") is None
