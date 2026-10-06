"""Bulk NZBGet protocol and resolver behavior."""

import base64
import json
from unittest.mock import patch

import pytest
from resources.lib import nzbget_api


@pytest.fixture(autouse=True)
def _no_live_config():
    with patch.object(nzbget_api, "config_options", return_value={}):
        yield


def test_appendfleet_uses_authenticated_long_rpc_without_scores():
    members = [
        {"NZBFilename": "movie.nzb", "Content": base64.b64encode(b"nzb").decode()}
    ]
    reply = {"Chosen": 42, "Members": [{"NZBID": 42, "Alive": 99}], "Complete": False}

    def getter(key, default=""):
        return {
            "nzbget_url": "http://server",
            "nzbget_username": "u",
            "nzbget_password": "p",
            "nzbget_category": "movies",
        }.get(key, default)

    with patch.object(
        nzbget_api, "_http_post_json", return_value=json.dumps({"result": reply})
    ) as post:
        assert nzbget_api.append_fleet(members, "key", getter) == (reply, None)
    args, kwargs = post.call_args
    assert args[0] == "http://server/jsonrpc"
    assert args[1]["params"] == [
        {
            "DupeKey": "key",
            "Category": "movies",
            "Priority": 0,
            "Timeout": 45,
            "Members": members,
        }
    ]
    assert kwargs["timeout"] == 300
    assert kwargs["basic_auth"] == ("u", "p")


@pytest.mark.parametrize("count", [0, 51])
def test_appendfleet_rejects_invalid_member_count(count):
    with patch.object(nzbget_api, "_rpc_call") as rpc:
        result, error = nzbget_api.append_fleet(
            [{"NZBFilename": "x.nzb", "URL": "http://x"}] * count, "key"
        )
    assert result is None and error
    rpc.assert_not_called()


@pytest.mark.parametrize(
    "error,unknown",
    [
        ({"code": -32601, "message": "Method not found"}, True),
        ({"code": 1, "message": "Invalid method"}, True),
        ({"code": 1, "message": "Invalid procedure"}, True),
        ({"code": 1, "message": "Unknown method: appendfleet"}, True),
        ({"code": 2, "message": "Invalid parameter"}, False),
    ],
)
def test_only_unknown_rpc_method_allows_fallback(error, unknown):
    with patch.object(
        nzbget_api, "_http_post_json", return_value=json.dumps({"error": error})
    ):
        _, returned = nzbget_api._rpc_call(
            "appendfleet", [], settings_getter=lambda _key, default="": default
        )
    assert nzbget_api.is_unknown_method(returned) is unknown


def _body(article):
    return (
        '<nzb><file subject="movie.mkv"><segments><segment bytes="10" number="1">'
        + article
        + "</segment></segments></file></nzb>"
    ).encode()


@pytest.mark.parametrize(
    "reply,error,expected",
    [
        (
            {
                "Chosen": 22,
                "Members": [
                    {"NZBID": 22, "Status": "QUEUED", "Alive": 99, "Rank": 1},
                    {"NZBID": 11, "Status": "BACKUP", "Alive": 98, "Rank": 2},
                ],
                "Complete": False,
            },
            None,
            22,
        ),
        ({"Chosen": 0, "Reason": "ALL_DEAD", "Members": []}, None, None),
        (None, "network timeout", None),
    ],
)
def test_bulk_dedup_tracks_server_choice_and_never_appends(reply, error, expected):
    import threading
    from types import SimpleNamespace

    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx = SimpleNamespace(
        settings_getter=lambda _key, default="": default,
        dupe={
            "key": "k",
            "backups": [
                {"link": "mirror", "title": "T"},
                {"link": "other", "title": "T"},
            ],
            "max_backups": -1,
        },
        dialog=None,
        cancel_event=threading.Event(),
        submitted_nzbids=[],
        adopted_nzbids=[],
    )
    with patch.object(nzbget_api, "history_rows", return_value=[]), patch.object(
        nzbget_api, "queue_rows", return_value=[]
    ), patch.object(
        nzbget_api,
        "fetch_nzb_bytes",
        side_effect=lambda url, **_kw: _body("other" if url == "other" else "pick"),
    ), patch.object(
        nzbget_api,
        "download_nzb",
        side_effect=lambda url, path, **_kw: __import__("pathlib")
        .Path(path)
        .write_bytes(_body("other" if url == "other" else "pick")),
    ), patch.object(
        nzbget_api, "append_fleet", return_value=(reply, error)
    ) as fleet, patch.object(
        nzbget_api, "append_nzb"
    ) as append, patch(
        "resources.lib.nzbget_fleet_run._fleet_backups",
        side_effect=lambda dupe, *_a, **_kw: dupe["backups"],
    ):
        nzbid, _error = submit_fleet(ctx, "pick", "T", "k")
    assert nzbid == expected
    assert len(fleet.call_args.args[0]) == 2
    append.assert_not_called()
    assert ctx.bulk_fleet is True
    if expected:
        assert ctx.fleet_pick_nzbid == 22
        assert sorted(ctx.submitted_nzbids) == [11, 22]


@pytest.fixture(name="bulk_env")
def _bulk_env():
    """A real collector with deterministic manifest downloads and server replies."""
    import pathlib
    import threading
    from contextlib import ExitStack
    from types import SimpleNamespace

    ctx = SimpleNamespace(
        settings_getter=lambda _key, default="": default,
        dupe={"key": "k", "backups": [], "max_backups": -1},
        dialog=None,
        cancel_event=threading.Event(),
        submitted_nzbids=[],
        adopted_nzbids=[],
    )
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "resources.lib.nzbget_resolver._reuse_completed_job",
                return_value="smb://host/done/movie.mkv",
            )
        )
        for name, value in [("history_rows", []), ("queue_rows", [])]:
            stack.enter_context(patch.object(nzbget_api, name, return_value=value))
        fetch = stack.enter_context(
            patch.object(
                nzbget_api, "fetch_nzb_bytes", side_effect=lambda url, **_kw: _body(url)
            )
        )
        stack.enter_context(
            patch.object(
                nzbget_api,
                "download_nzb",
                side_effect=lambda url, path, **_kw: pathlib.Path(path).write_bytes(
                    _body(url)
                ),
            )
        )
        stack.enter_context(
            patch(
                "resources.lib.nzbget_fleet_run._fleet_backups",
                side_effect=lambda dupe, *_a, **_kw: dupe["backups"],
            )
        )
        fleet = stack.enter_context(
            patch.object(
                nzbget_api,
                "append_fleet",
                return_value=({"Chosen": 42, "Members": [], "Complete": False}, None),
            )
        )
        append = stack.enter_context(
            patch.object(nzbget_api, "append_nzb", side_effect=[(42, None), (43, None)])
        )
        yield ctx, fleet, append, fetch


def test_bulk_stops_at_fifty_unique_members(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    ctx.dupe["backups"] = [{"link": str(i), "title": "T"} for i in range(60)]
    assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert len(fleet.call_args.args[0]) == 50
    assert fleet.call_count == 1
    append.assert_not_called()


def test_unknown_method_fallback_reuses_deduplicated_manifests(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, fetch = bulk_env
    ctx.dupe["backups"] = [{"link": "backup", "title": "T"}]
    fleet.return_value = (
        None,
        nzbget_api.RpcError("Invalid method", {"code": 1, "message": "Invalid method"}),
    )
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch("resources.lib.nzbget_resolver._warn_if_healthcheck_pauses"), patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append", return_value=False
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert ctx.bulk_fleet is False
    assert [call.args[0] for call in append.call_args_list] == ["pick", "backup"]
    assert (
        append.call_args_list[0].kwargs["dupe_score"]
        > append.call_args_list[1].kwargs["dupe_score"]
    )
    assert fetch.call_count == 1  # pick fetched once; backup streamed once
    from resources.lib.nzbget_resolver import _discard_parked_pick

    _discard_parked_pick(ctx)


@pytest.mark.parametrize("state", ["SUCCESS/ALL", "DOWNLOADING"])
def test_existing_release_is_reused_without_grabs_or_submissions(bulk_env, state):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, fetch = bulk_env
    row = {
        "NZBID": 77,
        "DupeKey": "k",
        "Status": state,
        "DestDir": "/done",
        "Name": "T",
    }
    with patch.object(
        nzbget_api,
        "history_rows",
        return_value=[row] if state.startswith("SUCCESS") else [],
    ), patch.object(
        nzbget_api,
        "queue_rows",
        return_value=[] if state.startswith("SUCCESS") else [row],
    ):
        nzbid, error = submit_fleet(ctx, "pick", "T", "k")
    assert error is None
    assert nzbid == (None if state.startswith("SUCCESS") else 77)
    fleet.assert_not_called()
    append.assert_not_called()
    fetch.assert_not_called()
    if nzbid is None:
        assert ctx.bulk_completed["dest_dir"] == "/done"


def test_already_downloaded_reply_finds_completed_file(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    fleet.return_value = (
        {"Chosen": 0, "Members": [], "Reason": "ALREADY_DOWNLOADED"},
        None,
    )
    entry = {"present": True, "nzbid": 77, "dest_dir": "/done", "job_name": "T"}
    with patch.object(nzbget_api, "history_success_by_dupekey", return_value=entry):
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    assert ctx.bulk_completed == entry
    append.assert_not_called()


def test_bulk_poll_never_offers_force_resubmission(bulk_env):
    from resources.lib.nzbget_resolver import _submit_poll_resolve

    ctx, _fleet, append, _fetch = bulk_env
    ctx.timeout, ctx.interval = 300, 1
    ctx.on_failure = lambda _message: None
    with patch(
        "resources.lib.nzbget_resolver.poll_nzbget_job",
        return_value={"outcome": "failed", "status": "FAILURE/HEALTH"},
    ) as poll:
        _submit_poll_resolve(ctx, "pick", "T", "", "")
    assert poll.call_args.kwargs["fleet"]["rescue"] is None
    assert poll.call_args.args[0] == 42
    append.assert_not_called()


def test_spooled_fleet_is_encoded_and_posted_as_stream(tmp_path):
    path = tmp_path / "manifest.nzb"
    path.write_bytes(b"abc" * 20000 + b"end")
    captured = {}

    def post(_url, payload, **kwargs):
        assert hasattr(payload, "read")
        captured.update(json.loads(payload.read()))
        assert kwargs["timeout"] == 300
        return json.dumps({"result": {"Chosen": 42, "Members": []}})

    with patch.object(nzbget_api, "_http_post_json", side_effect=post):
        assert (
            nzbget_api.append_fleet(
                [{"NZBFilename": "x.nzb", "ContentPath": str(path)}],
                "k",
                lambda _key, default="": default,
            )[1]
            is None
        )
    member = captured["params"][0]["Members"][0]
    assert set(member) == {"NZBFilename", "Content"}
    assert base64.b64decode(member["Content"]) == path.read_bytes()


@pytest.mark.parametrize("shutdown", [False, True])
def test_fleet_returning_during_cancel_is_cleaned_unless_shutdown(bulk_env, shutdown):
    import threading

    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    cleaned = threading.Event()

    def accepted(*_args):
        ctx.cancel_event.set()
        return {"Chosen": 42, "Members": [{"NZBID": 43, "Status": "BACKUP"}]}, None

    fleet.side_effect = accepted
    with patch.object(
        nzbget_api, "cancel_jobs", side_effect=lambda *_a, **_kw: cleaned.set()
    ) as cancel, patch("resources.lib.nzbget_resolver.xbmc.Monitor") as monitor:
        monitor.return_value.abortRequested.side_effect = (
            lambda: shutdown and ctx.cancel_event.is_set()
        )
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
        if shutdown:
            cancel.assert_not_called()
            assert ctx.fleet_aborted is True
        else:
            assert cleaned.wait(5)
            assert sorted(cancel.call_args.args[0]) == [42, 43]
    append.assert_not_called()


def test_abandoned_fleet_rpc_cleans_up_late_acceptance(bulk_env):
    import threading

    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    finish = threading.Event()
    cleaned = threading.Event()

    def accept_later(*_args):
        ctx.cancel_event.set()
        assert finish.wait(5)
        return {"Chosen": 42, "Members": []}, None

    fleet.side_effect = accept_later
    with patch.object(
        nzbget_api, "cancel_jobs", side_effect=lambda *_a, **_kw: cleaned.set()
    ):
        try:
            assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
        finally:
            finish.set()
        assert cleaned.wait(5)
    append.assert_not_called()


def test_prepared_legacy_fallback_preserves_force_when_history_unreadable(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    fleet.return_value = (
        None,
        nzbget_api.RpcError(
            "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
        ),
    )
    with patch.object(nzbget_api, "history_rows", return_value=None):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert append.call_args.kwargs["dupe_mode"] == "FORCE"
    from resources.lib.nzbget_resolver import _discard_parked_pick

    _discard_parked_pick(ctx)


def test_same_slice_cancel_returns_before_stalled_delete(bulk_env):
    import threading

    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, _append, _fetch = bulk_env
    release = threading.Event()
    entered = threading.Event()
    returned = threading.Event()
    finished = threading.Event()

    def accepted(*_args):
        ctx.cancel_event.set()
        return {"Chosen": 42, "Members": []}, None

    def delete(*_args, **_kwargs):
        entered.set()
        try:
            return release.wait(5)
        finally:
            finished.set()

    def resolve():
        submit_fleet(ctx, "pick", "T", "k")
        returned.set()

    fleet.side_effect = accepted
    with patch.object(nzbget_api, "cancel_jobs", side_effect=delete):
        worker = threading.Thread(target=resolve, daemon=True)
        worker.start()
        try:
            assert entered.wait(5)
            assert returned.wait(1), "Canceled resolver waited for NZBGet deletion"
        finally:
            release.set()
            worker.join(5)
            assert finished.wait(5)


def test_reused_queue_keeps_parked_backups_in_failover_ownership(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    history = [{"NZBID": 99, "DupeKey": "k", "Status": "DELETED/DUPE"}]
    queue = [
        {"NZBID": 78, "DupeKey": "k", "Status": "PAUSED"},
        {"NZBID": 77, "DupeKey": "k", "Status": "DOWNLOADING"},
    ]
    with patch.object(nzbget_api, "history_rows", return_value=history), patch.object(
        nzbget_api, "queue_rows", return_value=queue
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (77, None)
    assert set(ctx.adopted_nzbids) == {77, 78, 99}
    fleet.assert_not_called()
    append.assert_not_called()


def test_capped_bulk_skips_loader_when_picker_fills_slots(bulk_env):
    from unittest.mock import Mock

    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, _fleet, _append, _fetch = bulk_env
    loader = Mock(return_value=[])
    ctx.dupe.update(
        max_backups=1, backups=[{"link": "backup", "title": "T"}], loader=loader
    )

    def discover(dupe, _progress, include_loader=True, **_kwargs):
        return dupe["backups"] + (dupe["loader"]() if include_loader else [])

    with patch("resources.lib.nzbget_fleet_run._fleet_backups", side_effect=discover):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    loader.assert_not_called()


def test_bulk_checks_healthcheck_warning(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, _fleet, _append, _fetch = bulk_env
    with patch.object(
        nzbget_api, "config_options", return_value={"healthcheck": "pause"}
    ), patch("resources.lib.nzbget_resolver._warn_if_healthcheck_pauses") as warn:
        submit_fleet(ctx, "pick", "T", "k")
    assert warn.call_args.kwargs["options"]["healthcheck"] == "pause"


def test_abandoned_preflight_cannot_overwrite_accepted_fleet(bulk_env):
    import threading

    from resources.lib import nzbget_bulk
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, _fleet, _append, _fetch = bulk_env
    release = threading.Event()
    done = threading.Event()
    original_wait, original_probe = nzbget_bulk.call_abortable, nzbget_bulk._existing

    def slow_history(*_args):
        assert release.wait(5)
        return [
            {"NZBID": 99, "DupeKey": "k", "Status": "SUCCESS/ALL", "DestDir": "/stale"}
        ]

    def wait(func, *args, **kwargs):
        if kwargs.get("deadline") == 20:
            kwargs["deadline"] = 0.03
        return original_wait(func, *args, **kwargs)

    def probe(*args):
        try:
            return original_probe(*args)
        finally:
            done.set()

    with patch.object(
        nzbget_api, "history_rows", side_effect=slow_history
    ), patch.object(nzbget_bulk, "call_abortable", side_effect=wait), patch.object(
        nzbget_bulk, "_existing", side_effect=probe
    ):
        try:
            assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
        finally:
            release.set()
            assert done.wait(5)
    assert not getattr(ctx, "bulk_completed", None)
    assert ctx.fleet_pick_nzbid == 42


def test_missing_completed_file_fails_without_a_second_download(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, fetch = bulk_env
    history = [
        {"NZBID": 77, "DupeKey": "k", "Status": "SUCCESS/ALL", "DestDir": "/missing"}
    ]
    with patch.object(nzbget_api, "history_rows", return_value=history), patch(
        "resources.lib.nzbget_resolver._reuse_completed_job", return_value=None
    ), patch(
        "resources.lib.nzbget_resolver._string",
        return_value="Completed file unavailable",
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (
            None,
            "Completed file unavailable",
        )
    assert not getattr(ctx, "bulk_completed", None)
    fleet.assert_not_called()
    append.assert_not_called()
    fetch.assert_not_called()


def test_capped_legacy_refills_a_rejected_prepared_backup(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    ctx.dupe.update(
        max_backups=1,
        backups=[{"link": "backup", "title": "T"}],
        loader=lambda: [{"link": "replacement", "title": "T"}],
        loader_limit={"n": 1},
    )
    fleet.return_value = (
        None,
        nzbget_api.RpcError(
            "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
        ),
    )
    append.side_effect = [(42, None), (43, None), (44, None)]
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append",
        side_effect=[False, True, False],
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert [call.args[0] for call in append.call_args_list] == [
        "pick",
        "backup",
        "replacement",
    ]
    from resources.lib.nzbget_resolver import _discard_parked_pick

    _discard_parked_pick(ctx)


def test_completed_history_returns_before_unrelated_probes(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, _fleet, _append, _fetch = bulk_env
    history = [
        {"NZBID": 77, "DupeKey": "k", "Status": "SUCCESS/ALL", "DestDir": "/done"}
    ]
    with patch.object(nzbget_api, "history_rows", return_value=history), patch.object(
        nzbget_api, "queue_rows"
    ) as queue, patch.object(nzbget_api, "config_options") as config:
        assert submit_fleet(ctx, "pick", "T", "k") == (None, None)
    queue.assert_not_called()
    config.assert_not_called()


def test_capped_legacy_refills_from_unused_picker_rows(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    ctx.dupe.update(
        max_backups=1,
        backups=[{"link": "backup", "title": "T"}, {"link": "unused", "title": "T"}],
    )
    fleet.return_value = (
        None,
        nzbget_api.RpcError(
            "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
        ),
    )
    append.side_effect = [(42, None), (43, None), (44, None)]
    with patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append",
        side_effect=[False, True, False],
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert [call.args[0] for call in append.call_args_list] == [
        "pick",
        "backup",
        "unused",
    ]
    from resources.lib.nzbget_resolver import _discard_parked_pick

    _discard_parked_pick(ctx)


def test_bulk_loader_sees_only_remaining_unique_slots(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, _append, _fetch = bulk_env
    limit = {"n": 2}

    def loader():
        assert limit["n"] == 1
        return [{"link": "loader-copy", "title": "T"}]

    ctx.dupe.update(
        max_backups=2,
        backups=[{"link": "backup", "title": "T"}],
        loader=loader,
        loader_limit=limit,
    )
    assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert len(fleet.call_args.args[0]) == 3


@pytest.mark.parametrize("legacy", [False, True])
def test_parked_backup_is_adopted_before_any_indexer_grab(bulk_env, legacy):
    from resources.lib import nzbget_submit_ledger
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, fetch = bulk_env
    link = "http://indexer/held"
    ctx.dupe.update(max_backups=1, backups=[{"link": link, "title": "T"}])
    history = [{"NZBID": 77, "DupeKey": "k", "Status": "DELETED/DUPE"}]
    held = [
        {"link": nzbget_submit_ledger.link_key(link), "nzbid": 77, "state": "parked"}
    ]
    if legacy:
        fleet.return_value = (
            None,
            nzbget_api.RpcError(
                "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
            ),
        )
    with patch.object(nzbget_api, "history_rows", return_value=history), patch.object(
        nzbget_submit_ledger, "held", return_value=held
    ), patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch.object(
        nzbget_api, "download_nzb"
    ) as download:
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert ctx.adopted_nzbids == [77]
    assert len(fleet.call_args.args[0]) == 1
    download.assert_not_called()
    assert fetch.call_count == 1
    assert append.call_count == (1 if legacy else 0)
    if legacy:
        from resources.lib.nzbget_resolver import _discard_parked_pick

        _discard_parked_pick(ctx)


def test_legacy_scores_include_unused_hydra_refill(bulk_env):
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    ctx.dupe.update(max_backups=1, backups=[{"link": "backup", "title": "T"}])
    extra = {"link": "hydra", "title": "T"}

    def discover(dupe, *_args, **_kwargs):
        dupe["extras"] = [extra]
        return dupe["backups"] + [extra]

    fleet.return_value = (
        None,
        nzbget_api.RpcError(
            "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
        ),
    )
    append.side_effect = [(42, None), (43, None), (44, None)]
    with patch(
        "resources.lib.nzbget_fleet_run._fleet_backups", side_effect=discover
    ), patch(
        "resources.lib.nzbget_resolver._dupe_check_disabled", return_value=False
    ), patch(
        "resources.lib.nzbget_resolver._copy_vetoed_after_append",
        side_effect=[False, True, False],
    ):
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    scores = [call.kwargs["dupe_score"] for call in append.call_args_list]
    assert scores[0] > scores[1] > scores[2] > 0
    from resources.lib.nzbget_resolver import _discard_parked_pick

    _discard_parked_pick(ctx)


def test_unreadable_reuse_preserves_its_existing_notification(bulk_env):
    from unittest.mock import Mock

    from resources.lib import nzbget_resolver as core

    ctx, fleet, append, _fetch = bulk_env
    ctx.on_failure = Mock()
    history = [
        {"NZBID": 77, "DupeKey": "k", "Status": "SUCCESS/ALL", "DestDir": "/done"}
    ]
    with patch.object(nzbget_api, "history_rows", return_value=history), patch.object(
        core, "_reuse_completed_job", return_value=core.SMB_UNREADABLE
    ):
        assert core._submit_poll_resolve(ctx, "pick", "T", "", "") is False
    ctx.on_failure.assert_called_once_with(None)
    fleet.assert_not_called()
    append.assert_not_called()


def test_adopted_backup_consumes_legacy_attempt_budget(bulk_env):
    from resources.lib import nzbget_resolver as core
    from resources.lib import nzbget_submit_ledger
    from resources.lib.nzbget_fleet_run import submit_fleet

    ctx, fleet, append, _fetch = bulk_env
    link = "http://indexer/held"
    ctx.dupe.update(
        max_backups=2,
        backups=[
            {"link": link, "title": "T"},
            {"link": "backup", "title": "T"},
            {"link": "unused", "title": "T"},
        ],
    )
    history = [{"NZBID": 77, "DupeKey": "k", "Status": "DELETED/DUPE"}]
    held = [
        {"link": nzbget_submit_ledger.link_key(link), "nzbid": 77, "state": "parked"}
    ]
    fleet.return_value = (
        None,
        nzbget_api.RpcError(
            "Invalid procedure", {"code": 1, "message": "Invalid procedure"}
        ),
    )
    append.side_effect = [(42, None), (43, None), (44, None)]
    original = core._submit_candidates
    with patch.object(nzbget_api, "history_rows", return_value=history), patch.object(
        nzbget_submit_ledger, "held", return_value=held
    ), patch.object(core, "_dupe_check_disabled", return_value=False), patch.object(
        core, "_copy_vetoed_after_append", side_effect=[False, True, False]
    ), patch.object(
        core, "_submit_candidates", side_effect=original
    ) as submit:
        assert submit_fleet(ctx, "pick", "T", "k") == (42, None)
    assert submit.call_args.kwargs["limits"] == (1, 5)
    core._discard_parked_pick(ctx)
