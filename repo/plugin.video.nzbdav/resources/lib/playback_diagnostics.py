# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Explain why a finished download won't play, and offer to remount its share.

The NZBGet backend plays the completed file straight from a share or a
local/mounted folder. When that fails, the only signal used to be a short
toast. This module looks at the file system directly, independent of Kodi's
VFS, to say *why*: the network mount is down, the folder is missing or empty,
the release folder has no video, or the video can't be read. For a local
mount point backed by a systemd unit or an ``/etc/fstab`` entry it can also
remount the share. ``recent_kodi_errors`` pulls the error lines Kodi itself
logged so a player failure can show them.

Everything here is best effort: every probe swallows its own OS errors and
reports what it saw instead of raising into a failure path.
"""

import os
import re
import shutil
import subprocess  # nosec B404 -- fixed argv lists, no shell
import time

import xbmc
import xbmcgui
import xbmcvfs

from resources.lib.http_util import redact_text as _redact_text
from resources.lib.i18n import addon_name as _addon_name
from resources.lib.i18n import fmt as _fmt
from resources.lib.i18n import string as _string

_VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".mov")
_READ_PROBE_BYTES = 8192
_MAX_DEPTH = 3
# Where CoreELEC/LibreELEC (first) and ordinary systemd distros keep units.
_UNIT_DIRS = (
    "/storage/.config/system.d",
    "/etc/systemd/system",
    "/run/systemd/system",
    "/usr/lib/systemd/system",
    "/lib/systemd/system",
)
_PROC_MOUNTS = "/proc/mounts"
_FSTAB = "/etc/fstab"
_REMOUNT_TIMEOUT = 60
_REMOUNT_POLL = 0.25
_LOG_TAIL_BYTES = 512 * 1024
_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+\s+T:\d+\s+error <[^>]*>: (.*)$"
)
_PLAYBACK_ERROR = re.compile(
    r"VideoPlayer|OpenFile|OpenInputStream|CFile|DVD|Demux|InputStream|"
    r"smb|nfs|NeNeTeePee",
    re.IGNORECASE,
)

# Diagnosis kinds, most specific first.
MOUNT_DOWN = "mount_down"
ROOT_MISSING = "root_missing"
ROOT_EMPTY = "root_empty"
FOLDER_MISSING = "folder_missing"
NO_VIDEO = "no_video"
UNREADABLE = "unreadable"
OK = "ok"


class MountConfig:  # pylint: disable=too-few-public-methods
    """A configured mount point that holds the completed folder."""

    def __init__(self, mountpoint, source, unit=None, automount=False):
        self.mountpoint = mountpoint
        self.source = source  # "systemd" or "fstab"
        self.unit = unit  # systemd unit base name, for example "storage-nas"
        self.automount = automount


class Diagnosis:  # pylint: disable=too-few-public-methods
    """What the file system shows for a failed completed-folder lookup."""

    def __init__(self, kind, message, mount=None):
        self.kind = kind
        self.message = message
        self.mount = mount

    @property
    def can_remount(self):
        """True when remounting could plausibly fix this failure."""
        return (
            self.mount is not None
            and self.kind in (MOUNT_DOWN, ROOT_MISSING, ROOT_EMPTY)
            and _remount_supported()
        )


def is_local_path(path):
    """True for an absolute local path (not an ``smb://``-style URL)."""
    return bool(path) and "://" not in path and os.path.isabs(path)


def _unit_name(path):
    """Return the systemd unit base name for a mount path (``systemd-escape --path``).

    Escapes the path's UTF-8 bytes, and a dot only at the very start of the
    name, exactly as systemd does; a mismatch would miss the configured unit.
    """
    parts = [part for part in path.split("/") if part]
    raw = "/".join(parts).encode("utf-8", "surrogateescape")
    out = []
    for index, byte in enumerate(raw):
        char = chr(byte)
        if char == "/":
            out.append("-")
        elif (
            (byte < 128 and char.isalnum())
            or char in ":_"
            or (char == "." and index > 0)
        ):
            out.append(char)
        else:
            out.append("\\x{:02x}".format(byte))
    return "".join(out)


def _ancestors(path):
    """Yield ``path`` and each parent directory, longest first, excluding ``/``."""
    current = os.path.normpath(path)
    while current not in ("/", ""):
        yield current
        parent = os.path.dirname(current)
        # A Windows drive root is its own parent.
        if parent == current:
            return
        current = parent


def _decode_mount_field(field):
    """Undo the octal escapes ``/proc/mounts`` and fstab use for spaces etc."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), field)


def _read_table(path):
    """Return ``(mountpoint, fstype)`` rows from a mounts/fstab style file."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 3 or fields[0].startswith("#"):
                    continue
                rows.append(
                    (_decode_mount_field(fields[1]).rstrip("/") or "/", fields[2])
                )
    except OSError:
        return []
    return rows


def find_mount_config(path):
    """Return the configured mount point holding ``path``, or None.

    Looks for a systemd ``.mount``/``.automount`` unit first (how CoreELEC
    mounts NFS shares), then for an ``/etc/fstab`` entry.
    """
    if not is_local_path(path):
        return None
    fstab = {mountpoint for mountpoint, _fstype in _read_table(_FSTAB)}
    for ancestor in _ancestors(path):
        unit = _unit_name(ancestor)
        automount = False
        found = False
        for folder in _UNIT_DIRS:
            if os.path.isfile(os.path.join(folder, unit + ".automount")):
                automount = True
                found = True
            if os.path.isfile(os.path.join(folder, unit + ".mount")):
                found = True
        if found:
            return MountConfig(ancestor, "systemd", unit=unit, automount=automount)
        if ancestor in fstab:
            return MountConfig(ancestor, "fstab")
    return None


def is_mounted(mountpoint):
    """True when a real file system (not just an autofs trap) is mounted there."""
    target = mountpoint.rstrip("/") or "/"
    return any(
        point == target and fstype != "autofs"
        for point, fstype in _read_table(_PROC_MOUNTS)
    )


def _mount_down(mount):
    """True when ``mount`` is configured but nothing is mounted on it.

    Lists the mount point once first, so an armed automount gets its chance
    to mount before this reports it as down.
    """
    if is_mounted(mount.mountpoint):
        return False
    try:
        os.listdir(mount.mountpoint)
    except OSError:
        # Only the side effect matters; the re-check below decides.
        pass
    return not is_mounted(mount.mountpoint)


def mount_problem(root):
    """Return the ``MountConfig`` for ``root`` when its share is down, else None."""
    mount = find_mount_config(root)
    if mount is not None and _mount_down(mount):
        return mount
    return None


def _is_video(name):
    return name.lower().endswith(_VIDEO_EXTENSIONS)


def _videos_in_tree(folder, depth=_MAX_DEPTH):
    """Return ``(path, size)`` for every video at or below ``folder``."""
    found = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return found
    for name in entries:
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            if depth > 0:
                found.extend(_videos_in_tree(path, depth - 1))
        elif _is_video(name):
            try:
                found.append((path, os.path.getsize(path)))
            except OSError:
                found.append((path, -1))
    return found


def _read_error(path):
    """Return None when ``path`` reads, else the OS error text."""
    try:
        with open(path, "rb") as handle:
            if handle.read(_READ_PROBE_BYTES):
                return None
            return "file is empty"
    except OSError as error:
        return error.strerror or str(error)


def _diagnose_local(root, folder):
    mount = find_mount_config(root)
    if mount is not None and _mount_down(mount):
        return Diagnosis(MOUNT_DOWN, _fmt(30623, mount.mountpoint), mount)
    if not os.path.isdir(root):
        return Diagnosis(ROOT_MISSING, _fmt(30624, root), mount)
    try:
        empty = not os.listdir(root)
    except OSError as error:
        return Diagnosis(ROOT_MISSING, _fmt(30624, root) + " ({})".format(error), mount)
    if empty:
        return Diagnosis(ROOT_EMPTY, _fmt(30625, root), mount)
    if folder and not os.path.isdir(folder):
        return Diagnosis(FOLDER_MISSING, _fmt(30626, folder))
    videos = _videos_in_tree(folder) if folder else []
    if folder and not videos:
        return Diagnosis(NO_VIDEO, _fmt(30627, folder))
    if videos:
        path = max(videos, key=lambda row: row[1])[0]
        error = _read_error(path)
        if error:
            return Diagnosis(UNREADABLE, _fmt(30628, path, error))
    return Diagnosis(OK, _string(30629))


def _diagnose_remote(root, folder):
    """Kodi-VFS checks for an ``smb://``-style root (no mount to inspect)."""
    try:
        if not xbmcvfs.exists(root.rstrip("/") + "/"):
            return Diagnosis(ROOT_MISSING, _fmt(30624, _redact_text(root)))
        if folder and not xbmcvfs.exists(folder.rstrip("/") + "/"):
            return Diagnosis(FOLDER_MISSING, _fmt(30626, _redact_text(folder)))
    except Exception:  # pylint: disable=broad-except
        return Diagnosis(ROOT_MISSING, _fmt(30624, _redact_text(root)))
    return Diagnosis(OK, _string(30629))


def diagnose(root, folder=None):
    """Explain why the video under ``folder`` (inside ``root``) didn't play."""
    try:
        if is_local_path(root):
            return _diagnose_local(root.rstrip("/") or "/", folder)
        return _diagnose_remote(root, folder)
    except Exception as error:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: playback diagnosis failed: {}".format(error),
            xbmc.LOGWARNING,
        )
        return Diagnosis(OK, _string(30629))


def diagnose_file(path):
    """Diagnose a single local video path that Kodi failed to play."""
    if not is_local_path(path):
        return None
    folder = os.path.dirname(path)
    mount = find_mount_config(path)
    root = mount.mountpoint if mount is not None else folder
    diagnosis = diagnose(root, folder)
    if diagnosis.kind == OK and not os.path.isfile(path):
        return Diagnosis(FOLDER_MISSING, _fmt(30626, path))
    if diagnosis.kind == OK:
        error = _read_error(path)
        if error:
            return Diagnosis(UNREADABLE, _fmt(30628, path, error))
    return diagnosis


def _remount_supported():
    return os.name == "posix" and bool(
        shutil.which("systemctl") or shutil.which("mount")
    )


def _remount_commands(mount):
    if mount.source == "systemd":
        commands = [["systemctl", "reset-failed", mount.unit + ".mount"]]
        if mount.automount:
            commands.append(["systemctl", "restart", mount.unit + ".automount"])
        commands.append(["systemctl", "restart", mount.unit + ".mount"])
        return commands
    return [["mount", mount.mountpoint]]


def _stop_waiting(monitor, dialog):
    """True when Kodi is shutting down or the user canceled the remount."""
    return monitor.abortRequested() is True or dialog.iscanceled() is True


def _run_remount_command(command, monitor, dialog):
    """Run one command to completion; return its error text, or "" on success.

    Waits in short steps so Kodi shutdown and the progress dialog's Cancel
    button aren't held up for the whole timeout.
    """
    try:
        process = subprocess.Popen(  # nosec B603 pylint: disable=consider-using-with
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as error:
        return str(error)
    deadline = time.monotonic() + _REMOUNT_TIMEOUT
    while True:
        try:
            output, _unused = process.communicate(timeout=_REMOUNT_POLL)
            break
        except subprocess.TimeoutExpired:
            timed_out = time.monotonic() >= deadline
            if not timed_out and not _stop_waiting(monitor, dialog):
                continue
            process.kill()
            process.communicate()
            if timed_out:
                return "{} timed out after {} seconds".format(
                    " ".join(command), _REMOUNT_TIMEOUT
                )
            return _string(30635)
    if process.returncode == 0:
        return ""
    text = output.decode("utf-8", "replace").strip()
    return text or "{} exited {}".format(command[0], process.returncode)


def remount(mount):
    """Remount ``mount``; return ``(ok, detail)``.

    ``detail`` is the last command's error output when the mount still isn't
    up. Runs fixed argv lists (no shell) with a timeout so an unreachable NAS
    can't hang the caller forever, behind a progress dialog because a mount
    that can't reach its server takes that long to fail.
    """
    monitor = xbmc.Monitor()
    dialog = xbmcgui.DialogProgress()
    dialog.create(_addon_name(), _fmt(30634, mount.mountpoint))
    detail = ""
    try:
        for command in _remount_commands(mount):
            detail = _run_remount_command(command, monitor, dialog) or detail
            if _stop_waiting(monitor, dialog):
                break
    finally:
        dialog.close()
    ok = not _mount_down(mount)
    detail = detail or _string(30636)
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: remount {} -> {}{}".format(
            mount.mountpoint,
            "mounted" if ok else "still down",
            "" if ok else ": " + detail,
        ),
        xbmc.LOGINFO if ok else xbmc.LOGERROR,
    )
    return ok, ("" if ok else detail)


def _parse_log_time(stamp):
    try:
        return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def recent_kodi_errors(since, limit=5, log_path=None):
    """Return up to ``limit`` ERROR messages Kodi logged at or after ``since``.

    ``since`` is a ``time.time()`` value. Reads only the tail of kodi.log.
    Messages are redacted and trimmed for display.
    """
    path = log_path or xbmcvfs.translatePath("special://logpath/kodi.log")
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    messages = []
    for line in text.splitlines():
        match = _LOG_LINE.match(line)
        if not match:
            continue
        logged = _parse_log_time(match.group(1))
        if logged is None or logged < since - 1:
            continue
        message = _redact_text(match.group(2).strip())[:200]
        if message and (not messages or messages[-1] != message):
            messages.append(message)
    # Skins and other add-ons log unrelated errors too; when any line comes
    # from the player or file layer, show only those.
    playback = [message for message in messages if _PLAYBACK_ERROR.search(message)]
    return (playback or messages)[-limit:]


def failure_text(diagnosis=None, kodi_errors=None):
    """Join a diagnosis and Kodi's own error lines into dialog text."""
    lines = []
    if diagnosis is not None:
        lines.append(diagnosis.message)
    if kodi_errors:
        lines.append(_string(30630))
        lines.extend("- " + error for error in kodi_errors)
    return "[CR]".join(lines)


def show_failure_dialog(heading, text, remountable=False):
    """Show the failure; return True when the user chose to remount."""
    dialog = xbmcgui.Dialog()
    if remountable:
        return bool(
            dialog.yesno(heading, text, nolabel=_string(30631), yeslabel=_string(30632))
        )
    dialog.ok(heading, text)
    return False


def offer_remount(heading, diagnosis):
    """Show ``diagnosis``; remount on request. Return True if the share came back.

    Logs the diagnosis either way, so kodi.log records why playback failed.
    """
    xbmc.log(
        "NeNeTeePee-Stream-Kodi: playback failure diagnosis [{}]: {}".format(
            diagnosis.kind, diagnosis.message
        ),
        xbmc.LOGERROR,
    )
    if not show_failure_dialog(
        heading, failure_text(diagnosis), remountable=diagnosis.can_remount
    ):
        return False
    ok, detail = remount(diagnosis.mount)
    if not ok:
        xbmcgui.Dialog().ok(heading, _fmt(30633, diagnosis.mount.mountpoint, detail))
    return ok
