# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Tests for the NZBGet post-download failure dialog wrapper."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from resources.lib import nzbget_resolver as core
from resources.lib import nzbget_resolver_diag as rdiag
from resources.lib.i18n import string as _string


def _ctx(probe=None):
    ctx = SimpleNamespace(
        smb_root="/storage/nas/nzbget/downloads",
        smb_probe=probe,
        dialog=MagicMock(),
        interval=1.0,
        episode_context=None,
        on_success=MagicMock(),
    )
    ctx.dialog.iscanceled.return_value = False
    return ctx


def test_note_probe_records_miss_unreadable_and_clears_on_success():
    ctx = _ctx()
    rdiag.note_probe(ctx, "/f", None)
    assert ctx.smb_probe == ("/f", rdiag.PROBE_MISS)
    rdiag.note_probe(ctx, "/f", core.SMB_UNREADABLE)
    assert ctx.smb_probe == ("/f", rdiag.PROBE_UNREADABLE)
    rdiag.note_probe(ctx, "/f", "/f/video.mkv")
    assert ctx.smb_probe is None


def test_unrelated_failures_pass_through_unchanged():
    on_failure = MagicMock()
    holder = {"ctx": _ctx(("/f", rdiag.PROBE_MISS))}
    with patch.object(rdiag, "_diagnose_and_offer") as offer:
        rdiag.diagnosed_failure(holder, on_failure)(_string(30101))
    offer.assert_not_called()
    on_failure.assert_called_once_with(_string(30101))


def test_failure_before_ctx_passes_through():
    on_failure = MagicMock()
    rdiag.diagnosed_failure({}, on_failure)(_string(30221))
    on_failure.assert_called_once_with(_string(30221))


def test_no_video_failure_shows_dialog_instead_of_toast():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    with patch.object(rdiag, "_diagnose_and_offer", return_value=None) as offer:
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(_string(30223))
    offer.assert_called_once_with(ctx, "/f")
    on_failure.assert_called_once_with(None)
    ctx.on_success.assert_not_called()


def test_canceled_lookup_skips_dialog():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    ctx.dialog.iscanceled.return_value = True
    with patch.object(rdiag, "_diagnose_and_offer") as offer:
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(_string(30223))
    offer.assert_not_called()
    on_failure.assert_called_once_with(_string(30223))


def test_kodi_shutdown_skips_dialog():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    with patch.object(rdiag, "_diagnose_and_offer") as offer, patch.object(
        rdiag.xbmc.Monitor.return_value, "abortRequested", return_value=True
    ):
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(_string(30223))
    offer.assert_not_called()
    on_failure.assert_called_once_with(_string(30223))


def test_shutdown_during_remount_retry_skips_second_dialog():
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    dialog = MagicMock()
    with patch.object(
        rdiag.playback_diagnostics, "offer_remount", return_value=True
    ), patch.object(rdiag.playback_diagnostics, "diagnose"), patch.object(
        core, "resolve_smb_video", return_value=None
    ), patch.object(
        core.xbmcgui, "Dialog", return_value=dialog
    ), patch.object(
        rdiag.xbmc.Monitor.return_value, "abortRequested", return_value=True
    ):
        assert rdiag._diagnose_and_offer(ctx, "/f") is None
    dialog.ok.assert_not_called()


def test_unreadable_silent_failure_gets_dialog():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_UNREADABLE))
    with patch.object(rdiag, "_diagnose_and_offer", return_value=None) as offer:
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(None)
    offer.assert_called_once()
    on_failure.assert_called_once_with(None)


def test_successful_remount_plays_instead_of_failing():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    with patch.object(rdiag, "_diagnose_and_offer", return_value="/f/video.mkv"):
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(_string(30620))
    ctx.on_success.assert_called_once_with("/f/video.mkv")
    on_failure.assert_not_called()


def test_dialog_error_falls_back_to_original_failure():
    on_failure = MagicMock()
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    with patch.object(rdiag, "_diagnose_and_offer", side_effect=RuntimeError("x")):
        rdiag.diagnosed_failure({"ctx": ctx}, on_failure)(_string(30223))
    on_failure.assert_called_once_with(_string(30223))


def test_diagnose_and_offer_retries_lookup_after_remount():
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    with patch.object(
        rdiag.playback_diagnostics, "offer_remount", return_value=True
    ), patch.object(rdiag.playback_diagnostics, "diagnose"), patch.object(
        core, "resolve_smb_video", return_value="/f/video.mkv"
    ) as lookup:
        assert rdiag._diagnose_and_offer(ctx, "/f") == "/f/video.mkv"
    assert lookup.call_args.kwargs["budget"] == rdiag._REMOUNT_RETRY_BUDGET
    ctx.dialog.close.assert_called_once()


def test_remount_retry_keeps_the_inventory_callback():
    ctx = _ctx()
    recorder = MagicMock()
    rdiag.note_probe(ctx, "/f", None, recorder)
    with patch.object(
        rdiag.playback_diagnostics, "offer_remount", return_value=True
    ), patch.object(rdiag.playback_diagnostics, "diagnose"), patch.object(
        core, "resolve_smb_video", return_value="/f/video.mkv"
    ) as lookup:
        assert rdiag._diagnose_and_offer(ctx, "/f") == "/f/video.mkv"
    assert lookup.call_args.kwargs["on_inventory"] is recorder


def test_completed_lookups_record_their_inventory_callback():
    ctx = core._SubmitCtx("/storage/nas/dl", "", None, None, 1.0, 60)
    recorder = MagicMock()
    with patch.object(core, "resolve_smb_video", return_value=None):
        core._resolve_completed_smb(
            "/export/media/dl/Release", ctx, on_inventory=recorder
        )
    assert ctx.smb_probe_inventory is recorder


def test_diagnose_and_offer_reports_when_retry_still_misses():
    ctx = _ctx(("/f", rdiag.PROBE_MISS))
    dialog = MagicMock()
    with patch.object(
        rdiag.playback_diagnostics, "offer_remount", return_value=True
    ), patch.object(rdiag.playback_diagnostics, "diagnose"), patch.object(
        core, "resolve_smb_video", return_value=None
    ), patch.object(
        core.xbmcgui, "Dialog", return_value=dialog
    ):
        assert rdiag._diagnose_and_offer(ctx, "/f") is None
    dialog.ok.assert_called_once()


def test_reuse_or_submit_does_not_resubmit_when_share_is_down():
    ctx = core._SubmitCtx("/storage/nas/dl", "", None, None, 1.0, 60)
    ctx.on_failure = MagicMock()
    ctx.on_success = MagicMock()
    completed = {"dest_dir": "/export/media/dl/Release", "nzbid": 1, "name": "Release"}
    with patch.object(core, "resolve_smb_video", return_value=None), patch.object(
        rdiag, "share_down", return_value=True
    ), patch.object(core, "_submit_poll_resolve") as submit:
        leave = core._reuse_or_submit(ctx, "http://nzb", "Release", completed, (0, 0))
    assert leave is False
    submit.assert_not_called()
    ctx.on_failure.assert_called_once_with(_string(30223))
