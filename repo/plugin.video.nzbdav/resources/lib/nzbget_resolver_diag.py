# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
# pylint: disable=cyclic-import

"""Turn a post-download NZBGet failure into a diagnosis dialog.

NZBGet can finish a download that Kodi then can't find, most often because
the network share holding the completed folder isn't mounted. The resolver
records each completed-folder lookup on its submit context
(``note_probe``); ``diagnosed_failure`` wraps the entry point's failure
callback so that, when the failure came after such a lookup, the user gets a
dialog explaining what the file system shows instead of a five-second toast.
When the share is down and can be remounted, the dialog offers that, and a
successful remount retries the lookup and starts playback.

The wrapper still ends in exactly one ``on_success`` or ``on_failure`` call,
so the handle-based path keeps its ``setResolvedUrl`` contract.
"""

import xbmc

import resources.lib.nzbget_resolver as _core  # noqa: F401  pylint: disable=unused-import
from resources.lib import playback_diagnostics

# Seconds to look for the video again right after a successful remount.
_REMOUNT_RETRY_BUDGET = 20.0

PROBE_MISS = "miss"
PROBE_UNREADABLE = "unreadable"


def note_probe(ctx, folder, result):
    """Record a completed-folder lookup on ``ctx`` for a later failure dialog."""
    if ctx is None:
        return
    if result is _core.SMB_UNREADABLE:
        ctx.smb_probe = (folder, PROBE_UNREADABLE)
    elif not result:
        ctx.smb_probe = (folder, PROBE_MISS)
    else:
        ctx.smb_probe = None


def share_down(ctx):
    """True when the completed folder's mount is configured but not mounted."""
    try:
        return playback_diagnostics.mount_problem(ctx.smb_root) is not None
    except Exception:  # pylint: disable=broad-except
        return False


def _post_download(message, probe):
    if probe is None:
        return False
    if message is None:
        return probe[1] == PROBE_UNREADABLE
    return message in (_core._string(30223), _core._string(30620))


def _canceled(ctx):
    dialog = getattr(ctx, "dialog", None)
    if dialog is None:
        return False
    try:
        return bool(dialog.iscanceled())
    except Exception:  # pylint: disable=broad-except
        return False


def _retry_after_remount(ctx, folder):
    """Look for the video again; return its URL or None."""
    if not folder:
        return None
    return _core.resolve_smb_video(
        folder,
        interval=getattr(ctx, "interval", 1.0) or 1.0,
        budget=_REMOUNT_RETRY_BUDGET,
        requested_episode=_core._requested_episode(ctx.episode_context),
    )


def _diagnose_and_offer(ctx, folder):
    """Show the diagnosis; return a playable URL if a remount recovered it."""
    _core._close_dialog(ctx.dialog)
    heading = _core._addon_name()
    diagnosis = playback_diagnostics.diagnose(ctx.smb_root, folder)
    if not playback_diagnostics.offer_remount(heading, diagnosis):
        return None
    url = _retry_after_remount(ctx, folder)
    if not url:
        _core.xbmcgui.Dialog().ok(heading, _core._fmt(30621, folder or ctx.smb_root))
    return url or None


def diagnosed_failure(holder, on_failure):
    """Wrap ``on_failure`` so post-download failures explain themselves.

    ``holder`` is a dict whose ``"ctx"`` entry is filled in once the submit
    context exists; failures before that pass straight through.
    """

    def _fail(message):
        ctx = holder.get("ctx")
        probe = getattr(ctx, "smb_probe", None) if ctx is not None else None
        if not _post_download(message, probe) or _canceled(ctx):
            on_failure(message)
            return
        try:
            url = _diagnose_and_offer(ctx, probe[0])
        except Exception as error:  # pylint: disable=broad-except
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: failure dialog error: {}".format(error),
                xbmc.LOGWARNING,
            )
            on_failure(message)
            return
        if url:
            ctx.on_success(url)
            return
        # The dialog replaced the toast; still resolve the failure exactly once.
        on_failure(None)

    return _fail
