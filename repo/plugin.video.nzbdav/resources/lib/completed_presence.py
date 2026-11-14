# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Is a finished download still in the completed folder?

NZBGet's history keeps a SUCCESS row long after the files it names were
deleted (a cleanup job, a manual tidy-up). The picker's "DL" tag and the
resolver's play-the-finished-files shortcut both trust that row, so this
module asks the file system first.

``files_gone`` answers True only on proof: the completed root is reachable
and holds something, and the download's own folder is missing or (on a local
path) holds no video. An unmounted share, an unreadable folder, or any error
is "can't tell," which is False: the caller keeps its old behavior, and the
remount dialog still gets its turn.
"""

import os
import stat
import threading

import xbmc
import xbmcvfs

from resources.lib import playback_diagnostics
from resources.lib.http_util import redact_text as _redact_text

# How deep a release folder is searched for a video (the resolver's depth).
_MAX_DEPTH = 3

# Seconds the picker waits for the whole batch. A share that stops answering
# must not hold the picker; whatever was not checked in time keeps its tag.
PICKER_BUDGET = 2.0


def _local_has_video(folder, depth=_MAX_DEPTH):
    """True or False for a readable tree, None when part of it can't be read."""
    subfolders = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_dir():
                    subfolders.append(entry.path)
                elif playback_diagnostics._is_video(entry.name):
                    return True
    except OSError:
        return None
    unknown = False
    if depth > 0:
        for path in subfolders:
            found = _local_has_video(path, depth - 1)
            if found:
                return True
            if found is None:
                unknown = True
    elif subfolders:
        # Folders below the search depth were not looked at.
        unknown = True
    return None if unknown else False


def _local_root_ready(root):
    """True when the local completed root is mounted and holds something."""
    mount = playback_diagnostics.find_mount_config(root)
    if mount is not None and not playback_diagnostics.is_mounted(mount.mountpoint):
        return False
    try:
        with os.scandir(root) as entries:
            return next(entries, None) is not None
    except OSError:
        return False


def _local_folder_gone(folder):
    try:
        mode = os.stat(folder).st_mode
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISDIR(mode):
        return False
    return _local_has_video(folder) is False


def _remote_root_ready(root):
    return bool(xbmcvfs.exists(root.rstrip("/") + "/"))


def _remote_folder_gone(folder):
    # Kodi's VFS returns an empty listing for a failed one, so only a missing
    # folder counts as proof on a remote root.
    return not xbmcvfs.exists(folder.rstrip("/") + "/")


def _checks(root):
    """``(root_ready, folder_gone)`` for a local path or a Kodi VFS URL."""
    if playback_diagnostics.is_local_path(root):
        local_root = root.rstrip("/") or "/"
        return (lambda: _local_root_ready(local_root)), _local_folder_gone
    return (lambda: _remote_root_ready(root)), _remote_folder_gone


def _log_error(error):
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: completed-folder check failed: {}".format(
            _redact_text(str(error))
        ),
        xbmc.LOGDEBUG,
    )


def files_gone(root, folder):
    """True when ``folder`` (inside ``root``) verifiably holds no download.

    The root is checked again after the folder: a share that drops between
    the two looks exactly like a deleted folder.
    """
    if not root or not folder:
        return False
    try:
        root_ready, folder_gone = _checks(root)
        return bool(root_ready() and folder_gone(folder) and root_ready())
    except Exception as error:  # pylint: disable=broad-except
        _log_error(error)
        return False


def gone_folders(root, folders, budget=PICKER_BUDGET):
    """Return the ``folders`` whose downloads are verifiably gone, as a set.

    Runs on a helper thread and waits at most ``budget`` seconds for it, so a
    stalled share costs the picker no more than that. A batch that did not
    finish in time reports nothing gone: every folder keeps its tag.
    """
    wanted = list(dict.fromkeys(folder for folder in folders if folder))
    if not root or not wanted:
        return set()
    gone = []
    state = {"confirmed": False}
    finished = threading.Event()

    def _check():
        try:
            root_ready, folder_gone = _checks(root)
            if not root_ready():
                return
            for folder in wanted:
                if folder_gone(folder):
                    gone.append(folder)
            # See files_gone: a "gone" only counts while the root still answers.
            state["confirmed"] = not gone or bool(root_ready())
        except Exception as error:  # pylint: disable=broad-except
            _log_error(error)
        finally:
            finished.set()

    try:
        threading.Thread(
            target=_check, name="nzbdav-completed-presence", daemon=True
        ).start()
    except Exception as error:  # pylint: disable=broad-except
        _log_error(error)
        return set()
    if not finished.wait(budget) or not state["confirmed"]:
        return set()
    return set(gone)
