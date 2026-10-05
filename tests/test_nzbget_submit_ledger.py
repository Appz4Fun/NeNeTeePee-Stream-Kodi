"""NZBGet resubmit ledger: a replay skips backups NZBGet still holds (#372)."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from resources.lib import nzbget_api, nzbget_submit_ledger

from tests.test_nzbget_fleet_dedup import (  # noqa: F401  pylint: disable=unused-import
    _APPEND,
    _FETCH,
    _download_through_fetch_stub,
    _fleet_dupe,
    _fleet_env,
    _valid,
)

_HISTORY = "resources.lib.nzbget_resolver.nzbget_api.history_rows"
_QUEUE = "resources.lib.nzbget_resolver.nzbget_api.queue_rows"


# --- link_key ----------------------------------------------------------------


def test_link_key_strips_credentials_and_normalizes():
    key = nzbget_submit_ledger.link_key(
        "HTTP://User:Pw@Hydra.LAN:5076/getnzb/api/42?r=x&apikey=SECRET&t=get&i=7"
    )
    assert key == "http://hydra.lan:5076/getnzb/api/42?t=get"
    assert "SECRET" not in key
    assert nzbget_submit_ledger.link_key("https://a/b?b=2&a=1") == "https://a/b?a=1&b=2"
    # The shared redaction set (key, auth, access_token...) and nested URLs.
    nested = nzbget_submit_ledger.link_key(
        "https://p/dl?key=S1&auth=S2&access_token=S3&id=9"
        "&link=https%3A%2F%2Fidx%2Fget%3Fpassword%3DS4%26id%3D9"
    )
    assert not any(secret in nested for secret in ("S1", "S2", "S3", "S4"))
    assert "id=9" in nested
    assert nzbget_submit_ledger.link_key("") == ""


# --- record / held / forget --------------------------------------------------


def _row(link, nzbid, **extra):
    return dict({"link": link, "_nzbid": nzbid}, **extra)


def test_held_returns_fresh_same_key_entries_nzbget_still_holds():
    nzbget_submit_ledger.record(
        [_row("https://idx/a?apikey=k", 1), _row("https://idx/b", 2)], "Key", now=1000
    )
    nzbget_submit_ledger.record([_row("https://idx/c", 3)], "other", now=1000)
    states = {1: "parked", 2: "dead", 3: "parked"}
    got = nzbget_submit_ledger.held("key", states, now=2000)
    assert [(e["link"], e["nzbid"], e["state"]) for e in got] == [
        ("https://idx/a", 1, "parked"),
        ("https://idx/b", 2, "dead"),
    ]
    # NZBGet no longer holds #1: not held.
    still = nzbget_submit_ledger.held("key", {2: "dead"}, now=2000)
    assert [e["nzbid"] for e in still] == [2]


def test_entries_expire_after_a_day():
    nzbget_submit_ledger.record([_row("https://idx/a", 1)], "k", now=1000)
    ttl = nzbget_submit_ledger.TTL_SECONDS
    assert nzbget_submit_ledger.held("k", {1: "parked"}, now=1000 + ttl - 1)
    assert not nzbget_submit_ledger.held("k", {1: "parked"}, now=1000 + ttl)


def test_record_skips_rows_nzbget_did_not_accept_and_replaces_a_relink():
    nzbget_submit_ledger.record(
        [_row("https://idx/a", None), _row("", 5), "junk", _row("https://idx/b", 6)],
        "k",
        now=1000,
    )
    nzbget_submit_ledger.record([_row("https://idx/b", 9)], "k", now=1100)
    got = nzbget_submit_ledger.held("k", {6: "parked", 9: "parked"}, now=1200)
    assert [(e["link"], e["nzbid"]) for e in got] == [("https://idx/b", 9)]


def test_a_corrupt_ledger_reads_as_empty_and_is_rewritten():
    path = nzbget_submit_ledger._path()
    with open(path, "w", encoding="utf-8") as out:
        out.write("{not json")
    assert not nzbget_submit_ledger.held("k", {1: "parked"})
    nzbget_submit_ledger.record([_row("https://idx/a", 1)], "k")
    with open(path, encoding="utf-8") as handle:
        assert [e["nzbid"] for e in json.load(handle)] == [1]


def test_forget_drops_deleted_jobs():
    nzbget_submit_ledger.record(
        [_row("https://idx/a", 1), _row("https://idx/b", 2)], "k", now=1000
    )
    nzbget_submit_ledger.forget(["1", None])
    got = nzbget_submit_ledger.held("k", {1: "parked", 2: "parked"}, now=1000)
    assert [e["nzbid"] for e in got] == [2]


# --- dupekey_member_states ---------------------------------------------------


def test_member_states_classify_parked_dead_and_resendable_rows():
    history = [
        {"NZBID": 1, "DupeKey": "K", "Status": "DELETED/DUPE"},
        {"NZBID": "2", "DupeKey": "k", "Status": "FAILURE/HEALTH"},
        {"NZBID": 3, "DupeKey": "k", "Status": "DELETED/COPY"},
        {"NZBID": 4, "DupeKey": "k", "Status": "DELETED/MANUAL"},
        {"NZBID": 5, "DupeKey": "k", "Status": "SUCCESS/ALL"},
        {"NZBID": 6, "DupeKey": "other", "Status": "DELETED/DUPE"},
    ]
    queue = [{"NZBID": 7, "DupeKey": "k", "Status": "QUEUED"}]
    assert nzbget_api.dupekey_member_states("k", history, queue) == {
        1: "parked",
        2: "dead",
        3: "dead",
        7: "parked",
    }


# --- the fleet -----------------------------------------------------------------


def _ctx(dupe):
    return SimpleNamespace(
        settings_getter=lambda *_a: "",
        dupe=dupe,
        dialog=None,
        cancel_event=threading.Event(),
        submitted_nzbids=[],
        adopted_nzbids=[],
    )


def _rows(count):
    return [
        {"link": "https://idx/b{}?apikey=s".format(i), "title": "t{}".format(i)}
        for i in range(count)
    ]


@pytest.fixture
def _nzbget_history():
    """A patched NZBGet history/queue for the fleet's probe (``rows["history"]``)."""
    rows = {"history": [], "queue": []}
    with patch(_HISTORY, side_effect=lambda *_a, **_k: list(rows["history"])), patch(
        _QUEUE, side_effect=lambda *_a, **_k: list(rows["queue"])
    ):
        yield rows


def _play(rows, nzbids):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _ctx(_fleet_dupe(rows))
    fetched = []

    def _fetch(url, **_kw):
        fetched.append(url)
        return _valid(url)

    with patch(_FETCH, side_effect=_fetch), patch(
        _APPEND, side_effect=[(n, None) for n in nzbids]
    ) as append:
        submit_fleet(ctx, "pick", "T", "k")
    return ctx, fetched, [c.args[0] for c in append.call_args_list]


@pytest.mark.usefixtures("_fleet_env")
def test_replay_skips_parked_backups_and_adopts_them(_nzbget_history):
    _ctx1, _fetched, sent = _play(_rows(3), [10, 11, 12, 13])
    assert len(sent) == 4
    # NZBGet parked the three backups; the pick (10) downloaded.
    _nzbget_history["history"] = [
        {"NZBID": n, "DupeKey": "k", "Status": "DELETED/DUPE", "DupeScore": 900}
        for n in (11, 12, 13)
    ]
    ctx, fetched, sent = _play(_rows(3), [20])
    assert sent == ["pick"]
    assert fetched == ["pick"]  # no indexer grab for a held backup
    assert ctx.adopted_nzbids == [11, 12, 13]
    assert [b.get("_nzbid") for b in ctx.dupe["backups"]] == [11, 12, 13]


@pytest.mark.usefixtures("_fleet_env")
def test_replay_resends_deleted_or_unknown_backups(_nzbget_history):
    _play(_rows(2), [10, 11, 12])
    _nzbget_history["history"] = [
        {"NZBID": 11, "DupeKey": "k", "Status": "DELETED/MANUAL"},
        {"NZBID": 12, "DupeKey": "OTHER", "Status": "DELETED/DUPE"},
    ]
    ctx, _fetched, sent = _play(_rows(2), [20, 21, 22])
    assert len(sent) == 3
    assert ctx.adopted_nzbids == []


@pytest.mark.usefixtures("_fleet_env")
def test_dead_backups_are_skipped_but_not_adopted(_nzbget_history):
    _play(_rows(1), [10, 11])
    _nzbget_history["history"] = [
        {"NZBID": 11, "DupeKey": "k", "Status": "FAILURE/HEALTH"}
    ]
    ctx, _fetched, sent = _play(_rows(1), [20])
    assert sent == ["pick"]
    assert ctx.adopted_nzbids == []


@pytest.mark.usefixtures("_fleet_env")
def test_another_indexers_listing_of_a_held_posting_is_skipped(_nzbget_history):
    listed = {"size": "5000", "pubdate": "Sat, 03 Oct 2026 12:00:00 +0000"}
    _play([dict(_rows(1)[0], **listed)], [10, 11])
    _nzbget_history["history"] = [
        {"NZBID": 11, "DupeKey": "k", "Status": "DELETED/DUPE"}
    ]
    mirror = dict(listed, link="https://other/get/9", title="t0")
    _ctx2, fetched, sent = _play([mirror], [20])
    assert sent == ["pick"] and "https://other/get/9" not in fetched


@pytest.mark.usefixtures("_fleet_env")
def test_unreadable_history_sends_the_pick_alone_with_force():
    # Codex r28 (P2): with NZBGet's history unknown the pick's score can't be
    # lifted above an older same-key item, so SCORE could park it: it goes
    # alone, FORCE, after one retry of the read.
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _ctx(_fleet_dupe(_rows(2)))
    with patch(_HISTORY, return_value=None) as history, patch(
        _QUEUE, return_value=[]
    ), patch(_FETCH, side_effect=_valid), patch(
        _APPEND, return_value=(20, None)
    ) as append:
        assert submit_fleet(ctx, "pick", "T", "k") == (20, None)
    assert [c.args[0] for c in append.call_args_list] == ["pick"]
    assert append.call_args.kwargs["dupe_mode"] == "FORCE"
    assert history.call_count == 2


@pytest.mark.usefixtures("_fleet_env")
def test_the_ledger_never_stores_indexer_keys(_nzbget_history):
    _play(_rows(2), [10, 11, 12])
    with open(nzbget_submit_ledger._path(), encoding="utf-8") as handle:
        text = handle.read()
    assert "apikey" not in text and "b0" in text and "b1" in text


# --- the resolve ---------------------------------------------------------------


def test_adopted_backups_count_as_this_resolves_for_failover():
    from resources.lib import nzbget_resolver

    seen = {}

    def _poll(nzbid, *_a, **kw):
        seen["owned"] = kw["fleet"]["owned_nzbids"]()
        return {"outcome": "timeout"}

    ctx = SimpleNamespace(
        settings_getter=lambda *_a: "",
        dupe={"key": "k"},
        dialog=None,
        cancel_event=threading.Event(),
        submitted_nzbids=[5, 6],
        adopted_nzbids=[11, 6],
        timeout=1,
        interval=1,
        on_failure=lambda *_a: None,
    )
    poll = patch.object(nzbget_resolver, "poll_nzbget_job", side_effect=_poll)
    rescue = patch.object(nzbget_resolver, "_pick_rescue_callable", return_value=None)
    submit = patch(
        "resources.lib.nzbget_fleet_run.submit_fleet", return_value=(5, None)
    )
    with submit, poll, rescue:
        nzbget_resolver._submit_poll_resolve(ctx, "pick", "T", None, None)
    assert seen["owned"] == [5, 6, 11]


def _settle(predicate, timeout=2.0):
    """Poll ``predicate``: the cancel cleanup runs on a daemon thread."""
    import time

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_background_cancel_forgets_the_deleted_jobs():
    from resources.lib import nzbget_resolver

    nzbget_submit_ledger.record([_row("https://idx/a", 7)], "k")
    with patch.object(nzbget_resolver.nzbget_api, "cancel_jobs", return_value=True):
        nzbget_resolver._cancel_jobs_in_background([7], lambda *_a: "")
        assert _settle(lambda: not nzbget_submit_ledger.held("k", {7: "parked"}))


def test_a_failed_cancel_keeps_the_ledger_entries():
    # Codex r29 (P2): the jobs may still be in NZBGet -- a replay must still
    # recognize (and reuse) them.
    from resources.lib import nzbget_resolver

    nzbget_submit_ledger.record([_row("https://idx/a", 7)], "k")
    done = threading.Event()

    def _cancel(*_a, **_k):
        done.set()
        return False

    with patch.object(nzbget_resolver.nzbget_api, "cancel_jobs", side_effect=_cancel):
        nzbget_resolver._cancel_jobs_in_background([7], lambda *_a: "")
        assert done.wait(2)
    _settle(lambda: False, timeout=0.1)
    assert nzbget_submit_ledger.held("k", {7: "parked"})


def test_cancel_jobs_reports_a_failed_delete():
    with patch.object(
        nzbget_api, "_rpc_call", side_effect=[(True, None), (None, "timeout")]
    ):
        assert nzbget_api.cancel_jobs([7]) is False
    with patch.object(nzbget_api, "_rpc_call", return_value=(True, None)):
        assert nzbget_api.cancel_jobs(["7"]) is True
    assert nzbget_api.cancel_jobs([]) is True


@pytest.mark.usefixtures("_fleet_env")
def test_adopted_backups_take_a_slot_of_the_cap(_nzbget_history):
    # Codex r27 (P2): with a cap of 1, an adopted parked backup fills the only
    # slot -- no fresh backup is sent on top of it.
    from resources.lib.nzbget_fleet_run import submit_fleet

    _play(_rows(1), [10, 11])
    _nzbget_history["history"] = [
        {"NZBID": 11, "DupeKey": "k", "Status": "DELETED/DUPE"}
    ]
    rows = _rows(1) + [{"link": "https://idx/fresh", "title": "fresh"}]
    ctx = _ctx(_fleet_dupe(rows, max_backups=1))
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, side_effect=[(20, None), (21, None)]
    ) as append, patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append", return_value=False
    ):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick"]
    assert ctx.adopted_nzbids == [11]


@pytest.mark.usefixtures("_fleet_env")
def test_adoption_is_bounded_by_the_cap(_nzbget_history):
    # Codex r28 (P2): a replay with a lower cap adopts only as many held copies
    # as it has slots; the rest are still skipped (never re-sent).
    from resources.lib.nzbget_fleet_run import submit_fleet

    _play(_rows(3), [10, 11, 12, 13])
    _nzbget_history["history"] = [
        {"NZBID": n, "DupeKey": "k", "Status": "DELETED/DUPE"} for n in (11, 12, 13)
    ]
    ctx = _ctx(_fleet_dupe(_rows(3), max_backups=1))
    with patch(_FETCH, side_effect=_valid), patch(
        _APPEND, return_value=(20, None)
    ) as append, patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append", return_value=False
    ):
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick"]
    assert ctx.adopted_nzbids == [11]


def test_a_false_editqueue_result_is_a_failed_delete():
    # Codex r30 (P2): NZBGet answers ``false`` for a failed edit.
    with patch.object(nzbget_api, "_rpc_call", return_value=(False, None)):
        assert nzbget_api.cancel_jobs([7]) is False


@pytest.mark.usefixtures("_fleet_env")
def test_no_force_while_the_queue_holds_a_same_key_download():
    # Codex r30 (P2): history unreadable but the queue shows this key active --
    # FORCE would start a parallel download, so the pick keeps SCORE.
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = _ctx(_fleet_dupe(_rows(1)))
    active = [{"NZBID": 5, "DupeKey": "k", "Status": "DOWNLOADING"}]
    with patch(_HISTORY, return_value=None), patch(_QUEUE, return_value=active), patch(
        _FETCH, side_effect=_valid
    ), patch(_APPEND, return_value=(20, None)) as append:
        submit_fleet(ctx, "pick", "T", "k")
    assert [c.args[0] for c in append.call_args_list] == ["pick"]
    assert append.call_args.kwargs["dupe_mode"] == "SCORE"


def test_adopted_slots_also_shrink_the_attempt_budget():
    # Codex r30 (P2): adopted base slots never become replacement allowance.
    from resources.lib import nzbget_fleet_run
    from resources.lib.nzbget_fleet_dedup import FleetDedup

    held = [
        {
            "link": nzbget_submit_ledger.link_key("https://idx/b0"),
            "nzbid": 11,
            "state": "parked",
        }
    ]
    dedup = FleetDedup()
    run = ("k", lambda *_a: "", _ctx({}), dedup, held)
    seen = {}

    def _submit(fresh, *_a, **kw):
        seen["limits"] = kw["limits"]
        return []

    candidates = [{"link": "pick", "_is_pick": True}, {"link": "https://idx/b0"}]
    with patch("resources.lib.nzbget_resolver._submit_candidates", side_effect=_submit):
        live = nzbget_fleet_run._send_batch(run, candidates, (5, 10), True)
    assert seen["limits"] == (4, 9)
    assert live == [11] and dedup.attempts_used == 1


def _late_append(cancel_ok, is_pick=True):
    """An append that lands after a cancel abandoned its wait."""
    from resources.lib.nzbget_fleet_dedup import FleetDedup
    from resources.lib.nzbget_resolver_dupes import _append_abortably

    cancel = threading.Event()
    release = threading.Event()
    deleted = threading.Event()

    def _slow(*_a, **_k):
        release.wait(5)
        return 77

    def _cancel(*_a, **_k):
        deleted.set()
        return cancel_ok

    candidate = {"link": "https://idx/late?apikey=s", "title": "late"}
    if is_pick:
        candidate["_is_pick"] = True
    timer = threading.Timer(0.1, cancel.set)
    with patch(
        "resources.lib.nzbget_resolver._append_one_backup", side_effect=_slow
    ), patch.object(nzbget_api, "cancel_jobs", side_effect=_cancel), patch.object(
        nzbget_api, "cancel_queued_jobs", side_effect=_cancel
    ):
        timer.start()
        assert _append_abortably(
            candidate, b"<nzb/>", ("k", lambda *_a: "", False), (cancel, FleetDedup())
        ) == (None, False)
        release.set()
        assert deleted.wait(2)
        _settle(lambda: False, timeout=0.1)


def test_a_late_append_whose_delete_fails_stays_in_the_ledger():
    # Codex r30 (P2): the job is still in NZBGet -- a replay must reuse it.
    _late_append(cancel_ok=False)
    held = nzbget_submit_ledger.held("k", {77: "parked"})
    assert [e["link"] for e in held] == ["https://idx/late"]


def test_a_late_append_that_was_deleted_is_forgotten():
    _late_append(cancel_ok=True)
    assert not nzbget_submit_ledger.held("k", {77: "parked"})


def test_a_late_backup_append_stays_in_the_ledger_after_a_cancel():
    # A canceled play keeps its parked backups (only a queued copy is deleted),
    # so the late backup stays recorded for a replay to reuse.
    _late_append(cancel_ok=True, is_pick=False)
    assert nzbget_submit_ledger.held("k", {77: "parked"})


def test_cancel_queued_jobs_never_touches_history():
    calls = []

    def _rpc(_method, params, settings_getter=None):
        calls.append(params[0])
        return True, None

    with patch.object(nzbget_api, "_rpc_call", side_effect=_rpc):
        assert nzbget_api.cancel_queued_jobs(["7", 8, "x"]) is True
    assert calls == ["GroupFinalDelete"]
    assert nzbget_api.cancel_queued_jobs([]) is True
