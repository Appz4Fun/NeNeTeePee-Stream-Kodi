# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Tests for the "is the finished download still there?" checks.

History outlives the files: the picker's DL tag and the resolver's
play-the-finished-files shortcut must only trust a SUCCESS row whose folder
still holds the download, and a pick whose files were deleted must ask before
downloading again.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import completed_presence as presence
from resources.lib import nzbget_api
from resources.lib import nzbget_resolver as core
from resources.lib.i18n import string as _string


@pytest.fixture(name="root")
def _root(tmp_path):
    """A completed folder holding one finished release."""
    release = tmp_path / "Kept.Release"
    release.mkdir()
    (release / "kept.mkv").write_bytes(b"video")
    return tmp_path


# --- files_gone: local paths -------------------------------------------------


def test_missing_folder_is_gone(root):
    assert presence.files_gone(str(root), str(root / "Deleted.Release")) is True


def test_folder_with_a_video_is_not_gone(root):
    assert presence.files_gone(str(root), str(root / "Kept.Release")) is False


def test_folder_without_a_video_is_gone(root):
    emptied = root / "Emptied.Release"
    emptied.mkdir()
    (emptied / "release.nfo").write_text("leftover")
    assert presence.files_gone(str(root), str(emptied)) is True


def test_video_in_a_subfolder_counts(root):
    nested = root / "Nested.Release" / "inner" / "deeper"
    nested.mkdir(parents=True)
    (nested / "Movie.MKV").write_bytes(b"video")
    assert presence.files_gone(str(root), str(root / "Nested.Release")) is False


def test_folders_below_the_search_depth_are_not_proof(root):
    deep = root / "Deep.Release" / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "movie.mkv").write_bytes(b"video")
    assert presence.files_gone(str(root), str(root / "Deep.Release")) is False


def test_missing_root_proves_nothing(tmp_path):
    root = tmp_path / "not-mounted"
    assert presence.files_gone(str(root), str(root / "Release")) is False


def test_empty_root_proves_nothing(tmp_path):
    # An unmounted share leaves an empty mount point behind.
    assert presence.files_gone(str(tmp_path), str(tmp_path / "Release")) is False


def test_unmounted_share_proves_nothing(root):
    mount = SimpleNamespace(mountpoint=str(root))
    with patch.object(
        presence.playback_diagnostics, "find_mount_config", return_value=mount
    ), patch.object(presence.playback_diagnostics, "is_mounted", return_value=False):
        assert presence.files_gone(str(root), str(root / "Deleted.Release")) is False


def test_share_dropping_after_the_folder_check_proves_nothing(root):
    answers = iter([True, False])
    with patch.object(
        presence, "_local_root_ready", side_effect=lambda _r: next(answers)
    ):
        assert presence.files_gone(str(root), str(root / "Deleted.Release")) is False


def test_unreadable_folder_proves_nothing(root):
    with patch.object(presence.os, "stat", side_effect=PermissionError("denied")):
        assert presence.files_gone(str(root), str(root / "Kept.Release")) is False


def test_unreadable_subfolder_proves_nothing(root):
    release = root / "Partly.Unreadable"
    (release / "locked").mkdir(parents=True)
    real_scandir = presence.os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError("denied")
        return real_scandir(path)

    with patch.object(presence.os, "scandir", side_effect=scandir):
        assert presence.files_gone(str(root), str(release)) is False


def test_a_file_in_the_folders_place_proves_nothing(root):
    (root / "Odd.Release").write_bytes(b"not a folder")
    assert presence.files_gone(str(root), str(root / "Odd.Release")) is False


def test_blank_arguments_prove_nothing(root):
    assert presence.files_gone("", str(root / "Deleted.Release")) is False
    assert presence.files_gone(str(root), None) is False


def test_an_error_proves_nothing(root):
    with patch.object(presence, "_checks", side_effect=RuntimeError("boom")):
        assert presence.files_gone(str(root), str(root / "Deleted.Release")) is False


# --- files_gone: Kodi VFS URLs ----------------------------------------------


def _vfs(existing):
    return patch.object(
        presence.xbmcvfs, "exists", side_effect=lambda path: path in existing
    )


def test_remote_missing_folder_is_gone():
    with _vfs({"smb://nas/done/"}):
        assert presence.files_gone("smb://nas/done", "smb://nas/done/Release") is True


def test_remote_existing_folder_is_not_gone():
    with _vfs({"smb://nas/done/", "smb://nas/done/Release/"}):
        assert presence.files_gone("smb://nas/done", "smb://nas/done/Release") is False


def test_remote_unreachable_root_proves_nothing():
    with _vfs(set()):
        assert presence.files_gone("smb://nas/done", "smb://nas/done/Release") is False


# --- gone_folders: the picker's batch ---------------------------------------


def test_batch_reports_only_the_deleted_folders(root):
    kept = str(root / "Kept.Release")
    deleted = str(root / "Deleted.Release")
    other = str(root / "Also.Deleted")
    assert presence.gone_folders(str(root), [kept, deleted, None, other, deleted]) == {
        deleted,
        other,
    }


def test_batch_with_nothing_to_check_starts_no_thread(root):
    with patch.object(presence.threading, "Thread") as thread:
        assert presence.gone_folders(str(root), []) == set()
        assert presence.gone_folders("", ["/x"]) == set()
    thread.assert_not_called()


def test_batch_with_an_unready_root_reports_nothing(tmp_path):
    assert presence.gone_folders(str(tmp_path), [str(tmp_path / "Release")]) == set()


def test_batch_that_outlasts_its_budget_reports_nothing(root):
    release = threading.Event()

    def stalled(_folder):
        release.wait(5)
        return True

    with patch.object(presence, "_local_folder_gone", side_effect=stalled):
        try:
            gone = presence.gone_folders(
                str(root), [str(root / "Deleted.Release")], budget=0.05
            )
        finally:
            release.set()
    assert gone == set()


def test_batch_discards_its_findings_when_the_share_drops(root):
    answers = iter([True, False])
    deleted = str(root / "Deleted.Release")
    with patch.object(
        presence, "_local_root_ready", side_effect=lambda _r: next(answers)
    ):
        assert presence.gone_folders(str(root), [deleted]) == set()


def test_batch_survives_a_thread_that_cannot_start(root):
    with patch.object(presence.threading, "Thread", side_effect=RuntimeError("no")):
        assert presence.gone_folders(str(root), [str(root / "Deleted")]) == set()


# --- the picker's DL tag -----------------------------------------------------


class _DoneHistory(dict):
    _lookup_done = True


def _job(name, dest_dir):
    return {
        "name": name,
        "status": "SUCCESS/ALL",
        "bytes": 60_000_000_000,
        "nzbid": 7,
        "dest_dir": dest_dir,
    }


def _getter(root):
    values = {"nzbget_enabled": "true", "nzbget_smb_root": str(root)}
    return lambda key, default="": values.get(key, default)


def _tag(results, history, getter, base="/server/done"):
    from resources.lib.router import _tag_available

    with patch.object(
        nzbget_api, "completed_history", return_value=_DoneHistory(history)
    ), patch.object(nzbget_api, "completed_base_dir", return_value=base) as base_dir:
        completed = _tag_available(results, settings_getter=getter)
    return completed, base_dir


def test_picker_drops_the_dl_tag_when_the_files_were_deleted(root):
    kept = {"title": "Kept.Release", "size": "60500000000"}
    deleted = {"title": "Deleted.Release", "size": "60500000000"}
    history = {
        "Kept.Release": _job("Kept.Release", "/server/done/Kept.Release"),
        "Deleted.Release": _job("Deleted.Release", "/server/done/Deleted.Release"),
    }
    completed, base_dir = _tag([kept, deleted], history, _getter(root))

    assert kept.get("_available") is True
    assert kept.get("_nzbget_completed_job") == history["Kept.Release"]
    assert "_available" not in deleted
    assert "_nzbget_completed_job" not in deleted
    # The check must not hold the picker longer than the history call may.
    assert base_dir.call_args.kwargs["timeout"] == nzbget_api._PICKER_RPC_TIMEOUT
    from resources.lib.router import _completed_lookup_was_done

    assert _completed_lookup_was_done(completed) is True


def test_picker_asks_the_file_system_only_when_something_matched(root):
    result = {"title": "Unrelated.Release", "size": "60500000000"}
    history = {"Deleted.Release": _job("Deleted.Release", "/server/done/Deleted")}
    with patch.object(presence, "gone_folders") as batch:
        _completed, base_dir = _tag([result], history, _getter(root))
    base_dir.assert_not_called()
    batch.assert_not_called()
    assert "_available" not in result


def test_picker_keeps_the_tag_without_a_completed_folder_setting(root):
    result = {"title": "Deleted.Release", "size": "60500000000"}
    history = {"Deleted.Release": _job("Deleted.Release", "/server/done/Deleted")}
    getter = _getter("")
    _completed, base_dir = _tag([result], history, getter)
    assert result.get("_available") is True
    base_dir.assert_not_called()


def test_picker_keeps_the_tag_when_the_share_is_not_mounted(tmp_path):
    # Empty mount point: nothing can be proven, and the play-time remount
    # dialog needs the completed match to still be attached.
    result = {"title": "Deleted.Release", "size": "60500000000"}
    history = {"Deleted.Release": _job("Deleted.Release", "/server/done/Deleted")}
    _tag([result], history, _getter(tmp_path))
    assert result.get("_available") is True
    assert result.get("_nzbget_completed_job") == history["Deleted.Release"]


def test_picker_keeps_the_tag_when_the_check_fails(root):
    result = {"title": "Deleted.Release", "size": "60500000000"}
    history = {"Deleted.Release": _job("Deleted.Release", "/server/done/Deleted")}
    with patch.object(presence, "gone_folders", side_effect=RuntimeError("boom")):
        _tag([result], history, _getter(root))
    assert result.get("_available") is True


def test_picker_maps_folders_the_way_the_resolver_does(root):
    # No usable completed base (NZBGet's default is a ${MainDir} template):
    # the resolver's fallback mapping applies, and so must the picker's.
    result = {"title": "Deleted.Release", "size": "60500000000"}
    history = {"Deleted.Release": _job("Deleted.Release", "/other/dst/Deleted.Release")}
    with patch.object(presence, "gone_folders", return_value=set()) as batch:
        _tag([result], history, _getter(root), base=None)
    assert list(batch.call_args.args[1]) == [
        core.nzbget_smb_target(str(root), "/other/dst/Deleted.Release", "", None)
    ]


def test_completed_base_dir_passes_its_timeout():
    with patch.object(nzbget_api, "_rpc_call", return_value=(None, "down")) as rpc:
        assert nzbget_api.completed_base_dir(timeout=7) is None
    assert rpc.call_args.kwargs["timeout"] == 7


# --- playing a pick whose files are gone ------------------------------------


def _ctx(root, dialog=None):
    ctx = core._SubmitCtx(str(root), "", "/server/done", dialog, 1.0, 60)
    ctx.on_failure = MagicMock()
    ctx.on_success = MagicMock()
    return ctx


def _play(ctx, completed, answer, nzb_url="http://nzb"):
    """Run ``_reuse_or_submit``; return ``(leave, submit, probe, yesno)``."""
    dialog = MagicMock()
    dialog.yesno.return_value = answer
    with patch.object(
        core, "resolve_smb_video", return_value=None
    ) as probe, patch.object(
        core, "_submit_poll_resolve", return_value=False
    ) as submit, patch.object(
        core.xbmcgui, "Dialog", return_value=dialog
    ):
        leave = core._reuse_or_submit(ctx, nzb_url, "Release", completed, (0, 0))
    return leave, submit, probe, dialog.yesno


_DELETED = {"dest_dir": "/server/done/Deleted.Release", "nzbid": 1, "name": "Deleted"}
_KEPT = {"dest_dir": "/server/done/Kept.Release", "nzbid": 2, "name": "Kept"}


def test_download_again_submits_the_pick(root):
    ctx = _ctx(root)
    leave, submit, probe, yesno = _play(ctx, _DELETED, True)

    assert leave is False
    submit.assert_called_once()
    # Proof the files are gone makes the three-second probe pointless.
    probe.assert_not_called()
    yesno.assert_called_once()
    assert yesno.call_args.args[1] == _string(30707)
    assert yesno.call_args.kwargs == {
        "nolabel": _string(30709),
        "yeslabel": _string(30708),
    }
    ctx.on_failure.assert_not_called()
    ctx.on_success.assert_not_called()


def test_cancel_ends_the_play_without_a_download(root):
    ctx = _ctx(root)
    leave, submit, _probe, yesno = _play(ctx, _DELETED, False)

    assert leave is False
    yesno.assert_called_once()
    submit.assert_not_called()
    # The silent exit: the user just answered a dialog, no toast on top.
    ctx.on_failure.assert_called_once_with(None)
    ctx.on_success.assert_not_called()


def test_cancel_does_not_raise_the_failure_dialog(root):
    from resources.lib import nzbget_resolver_diag as rdiag

    ctx = _ctx(root)
    on_failure = MagicMock()
    ctx.on_failure = rdiag.diagnosed_failure({"ctx": ctx}, on_failure)
    with patch.object(rdiag, "_diagnose_and_offer") as offer:
        _play(ctx, _DELETED, False)
    offer.assert_not_called()
    on_failure.assert_called_once_with(None)


def test_files_still_there_play_without_a_prompt(root):
    ctx = _ctx(root)
    dialog = MagicMock()
    video = str(root / "Kept.Release" / "kept.mkv")
    with patch.object(core, "resolve_smb_video", return_value=video), patch.object(
        core, "_submit_poll_resolve"
    ) as submit, patch.object(core.xbmcgui, "Dialog", return_value=dialog):
        core._reuse_or_submit(ctx, "http://nzb", "Release", _KEPT, (0, 0))
    ctx.on_success.assert_called_once_with(video)
    submit.assert_not_called()
    dialog.yesno.assert_not_called()


def test_a_pick_without_a_completed_match_is_never_asked(root):
    ctx = _ctx(root)
    _leave, submit, _probe, yesno = _play(ctx, None, False)
    submit.assert_called_once()
    yesno.assert_not_called()


def test_no_prompt_when_there_is_nothing_to_download(root):
    ctx = _ctx(root)
    _leave, submit, _probe, yesno = _play(ctx, _DELETED, True, nzb_url="")
    yesno.assert_not_called()
    submit.assert_not_called()
    ctx.on_failure.assert_called_once_with(_string(30223))


def test_a_share_the_probe_woke_up_is_looked_at_again(root):
    # First look: share not mounted (can't tell). The probe wakes it, misses,
    # and the second look proves the folder is gone.
    ctx = _ctx(root)
    looks = iter([None, str(root / "Deleted.Release")])
    with patch.object(
        core, "_gone_completed_folder", side_effect=lambda *_a: next(looks)
    ):
        _leave, submit, probe, yesno = _play(ctx, _DELETED, False)
    probe.assert_called_once()
    yesno.assert_called_once()
    submit.assert_not_called()


def test_a_canceled_probe_is_not_followed_by_a_prompt(root):
    progress = MagicMock()
    progress.iscanceled.return_value = True
    ctx = _ctx(root, dialog=progress)
    looks = iter([None, str(root / "Deleted.Release")])
    with patch.object(
        core, "_gone_completed_folder", side_effect=lambda *_a: next(looks, None)
    ):
        _leave, _submit, _probe, yesno = _play(ctx, _DELETED, True)
    yesno.assert_not_called()


def test_a_prompt_kodi_refuses_to_show_declines(root):
    ctx = _ctx(root)
    dialog = MagicMock()
    dialog.yesno.side_effect = RuntimeError("no window")
    with patch.object(core, "_submit_poll_resolve") as submit, patch.object(
        core.xbmcgui, "Dialog", return_value=dialog
    ):
        core._reuse_or_submit(ctx, "http://nzb", "Release", _DELETED, (0, 0))
    submit.assert_not_called()
    ctx.on_failure.assert_called_once_with(None)


def test_prompt_and_log_hide_share_credentials():
    dialog = MagicMock()
    dialog.yesno.return_value = True
    with patch.object(core.xbmcgui, "Dialog", return_value=dialog), patch.object(
        core.xbmc, "log"
    ) as log:
        assert core._confirm_redownload("smb://user:secret@nas/done/Release") is True
    assert "secret" not in dialog.yesno.call_args.args[1]
    assert "Release" in log.call_args.args[0]
    assert "secret" not in log.call_args.args[0]


def test_gone_folder_needs_proof(root):
    ctx = _ctx(root)
    assert core._gone_completed_folder(_DELETED, ctx) == str(root / "Deleted.Release")
    assert core._gone_completed_folder(_KEPT, ctx) is None
    assert core._gone_completed_folder(None, ctx) is None
    assert core._gone_completed_folder({"nzbid": 1}, ctx) is None
    assert core._gone_completed_folder(_DELETED, SimpleNamespace()) is None
