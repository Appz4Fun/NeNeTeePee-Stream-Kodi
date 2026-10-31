# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Tests for the playback failure diagnosis and remount helpers."""

import ntpath
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import playback_diagnostics as diag


@pytest.fixture(name="fs")
def fs_fixture(tmp_path, monkeypatch):
    """A fake root with its own unit dir, /proc/mounts and fstab."""
    units = tmp_path / "units"
    units.mkdir()
    mounts = tmp_path / "mounts"
    mounts.write_text("")
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    monkeypatch.setattr(diag, "_UNIT_DIRS", (str(units),))
    monkeypatch.setattr(diag, "_PROC_MOUNTS", str(mounts))
    monkeypatch.setattr(diag, "_FSTAB", str(fstab))
    share = tmp_path / "share"
    share.mkdir()
    unit = diag._unit_name(str(share))
    return {
        "tmp": tmp_path,
        "units": units,
        "mounts": mounts,
        "fstab": fstab,
        "share": share,
        "unit": unit,
    }


def _mounted(fs, fstype="nfs4"):
    fs["mounts"].write_text(
        "nas:/export/media {} {} ro,hard 0 0\n".format(fs["share"], fstype)
    )


def test_unit_name_matches_systemd_escape():
    assert diag._unit_name("/storage/nas") == "storage-nas"
    # Expected values are real ``systemd-escape --path`` output.
    assert diag._unit_name("/mnt/my-nas/.x") == "mnt-my\\x2dnas-.x"
    assert diag._unit_name("/.y/z") == "\\x2ey-z"
    assert diag._unit_name("/mnt/m\u00e9dia") == "mnt-m\\xc3\\xa9dia"
    assert diag._unit_name("/mnt/a b") == "mnt-a\\x20b"


def test_ancestors_stop_at_windows_drive_root():
    with patch.object(diag.os, "path", ntpath):
        assert list(diag._ancestors("C:/Downloads/Completed")) == [
            "C:\\Downloads\\Completed",
            "C:\\Downloads",
            "C:\\",
        ]


def test_ancestors_exclude_posix_root():
    assert list(diag._ancestors("/mnt/nas/")) == ["/mnt/nas", "/mnt"]


def test_find_mount_config_prefers_systemd_automount(fs):
    (fs["units"] / (fs["unit"] + ".mount")).write_text("")
    (fs["units"] / (fs["unit"] + ".automount")).write_text("")
    root = fs["share"] / "nzbget" / "downloads"

    mount = diag.find_mount_config(str(root))

    assert mount.mountpoint == str(fs["share"])
    assert mount.source == "systemd"
    assert mount.unit == fs["unit"]
    assert mount.automount is True


def test_find_mount_config_reads_fstab(fs):
    fs["fstab"].write_text("nas:/x {} nfs defaults 0 0\n".format(fs["share"]))

    mount = diag.find_mount_config(str(fs["share"] / "downloads"))

    assert mount.source == "fstab"
    assert mount.mountpoint == str(fs["share"])


def test_find_mount_config_ignores_remote_roots(fs):
    assert diag.find_mount_config("smb://host/share") is None


def test_autofs_trap_alone_is_not_mounted(fs):
    _mounted(fs, fstype="autofs")
    assert diag.is_mounted(str(fs["share"])) is False
    _mounted(fs)
    assert diag.is_mounted(str(fs["share"])) is True


def test_diagnose_reports_unmounted_share(fs):
    (fs["units"] / (fs["unit"] + ".mount")).write_text("")
    root = str(fs["share"] / "nzbget" / "downloads")

    with patch.object(diag, "_remount_supported", return_value=True):
        result = diag.diagnose(root, root + "/Release")

    assert result.kind == diag.MOUNT_DOWN
    assert str(fs["share"]) in result.message
    assert result.can_remount is True


def test_diagnose_walks_down_to_unreadable_video(fs):
    _mounted(fs)
    (fs["units"] / (fs["unit"] + ".mount")).write_text("")
    release = fs["share"] / "Release"
    release.mkdir()
    (release / "video.mkv").write_bytes(b"")

    result = diag.diagnose(str(fs["share"]), str(release))

    assert result.kind == diag.UNREADABLE
    assert result.can_remount is False


@pytest.mark.parametrize(
    "setup, kind",
    [
        ("missing_root", diag.ROOT_MISSING),
        ("empty_root", diag.ROOT_EMPTY),
        ("missing_folder", diag.FOLDER_MISSING),
        ("no_video", diag.NO_VIDEO),
        ("ok", diag.OK),
    ],
)
def test_diagnose_kinds_without_mount(tmp_path, monkeypatch, setup, kind):
    monkeypatch.setattr(diag, "_UNIT_DIRS", ())
    monkeypatch.setattr(diag, "_FSTAB", str(tmp_path / "nofstab"))
    root = tmp_path / "root"
    release = root / "Release"
    if setup != "missing_root":
        root.mkdir()
    if setup in ("missing_folder", "no_video", "ok"):
        (root / "other").write_text("x")
    if setup in ("no_video", "ok"):
        release.mkdir()
        (release / "readme.nfo").write_text("x")
    if setup == "ok":
        (release / "Movie.mkv").write_bytes(b"data")

    assert diag.diagnose(str(root), str(release)).kind == kind


def test_diagnose_file_reports_missing_video(tmp_path, monkeypatch):
    monkeypatch.setattr(diag, "_UNIT_DIRS", ())
    monkeypatch.setattr(diag, "_FSTAB", str(tmp_path / "nofstab"))
    (tmp_path / "Movie.mkv").write_bytes(b"data")

    assert diag.diagnose_file(str(tmp_path / "Gone.mkv")).kind == diag.FOLDER_MISSING
    assert diag.diagnose_file(str(tmp_path / "Movie.mkv")).kind == diag.OK
    assert diag.diagnose_file("http://127.0.0.1:1/stream") is None


def test_diagnose_remote_root_uses_kodi_vfs():
    with patch.object(diag.xbmcvfs, "exists", return_value=False):
        result = diag.diagnose("smb://user:pw@host/share", "smb://host/share/R")
    assert result.kind == diag.ROOT_MISSING


def test_remount_commands_for_systemd_and_fstab():
    automount = diag.MountConfig("/storage/nas", "systemd", "storage-nas", True)
    assert diag._remount_commands(automount) == [
        ["systemctl", "reset-failed", "storage-nas.mount"],
        ["systemctl", "restart", "storage-nas.automount"],
        ["systemctl", "restart", "storage-nas.mount"],
    ]
    fstab = diag.MountConfig("/mnt/nas", "fstab")
    assert diag._remount_commands(fstab) == [["mount", "/mnt/nas"]]


@pytest.fixture(name="progress")
def progress_fixture():
    """A remount progress dialog nobody cancels, with Kodi staying up."""
    dialog = MagicMock()
    dialog.iscanceled.return_value = False
    monitor = diag.xbmc.Monitor.return_value
    with patch.object(
        diag.xbmcgui, "DialogProgress", return_value=dialog
    ), patch.object(monitor, "abortRequested", return_value=False):
        yield dialog


def _python(code):
    return [sys.executable, "-c", code]


def test_remount_reports_command_error_when_still_down(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    fail = "import sys; print('mount.nfs: Connection refused'); sys.exit(32)"
    with patch.object(
        diag, "_remount_commands", return_value=[_python(fail)]
    ), patch.object(diag, "_mount_down", return_value=True):
        ok, detail = diag.remount(mount)

    assert (ok, detail) == (False, "mount.nfs: Connection refused")
    progress.create.assert_called_once()
    progress.close.assert_called_once()


def test_remount_says_still_down_when_commands_succeed(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    with patch.object(
        diag, "_remount_commands", return_value=[_python("pass")]
    ), patch.object(diag, "_mount_down", return_value=True):
        assert diag.remount(mount) == (False, diag._string(30636))
    progress.close.assert_called_once()


def test_remount_survives_missing_command_and_reports_success(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    with patch.object(
        diag, "_remount_commands", return_value=[["/nonexistent/mount"]]
    ), patch.object(diag, "_mount_down", return_value=False):
        assert diag.remount(mount) == (True, "")
    progress.close.assert_called_once()


def test_remount_kills_a_command_that_times_out(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    hang = _python("import time; time.sleep(30)")
    with patch.object(diag, "_remount_commands", return_value=[hang]), patch.object(
        diag, "_mount_down", return_value=True
    ), patch.object(diag, "_REMOUNT_TIMEOUT", 0.1), patch.object(
        diag, "_REMOUNT_POLL", 0.02
    ):
        started = time.monotonic()
        ok, detail = diag.remount(mount)
    assert ok is False
    assert "timed out after 0.1 seconds" in detail
    assert time.monotonic() - started < 10
    progress.close.assert_called_once()


def test_remount_timeout_is_not_held_up_by_a_helper_process(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    parent = _python(
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    with patch.object(diag, "_remount_commands", return_value=[parent]), patch.object(
        diag, "_mount_down", return_value=True
    ), patch.object(diag, "_REMOUNT_TIMEOUT", 0.5), patch.object(
        diag, "_REMOUNT_POLL", 0.02
    ):
        started = time.monotonic()
        ok, _detail = diag.remount(mount)
    assert ok is False
    assert time.monotonic() - started < 10


def test_remount_cancel_stops_the_running_command(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    progress.iscanceled.return_value = True
    hang = _python("import time; time.sleep(30)")
    second = _python("raise SystemExit('must not run')")
    with patch.object(
        diag, "_remount_commands", return_value=[hang, second]
    ), patch.object(diag, "_mount_down", return_value=True), patch.object(
        diag, "_REMOUNT_POLL", 0.02
    ):
        started = time.monotonic()
        assert diag.remount(mount) == (False, diag._string(30635))
    assert time.monotonic() - started < 10


def test_remount_stops_waiting_when_kodi_shuts_down(progress):
    mount = diag.MountConfig("/mnt/nas", "fstab")
    assert progress.iscanceled.return_value is False
    hang = _python("import time; time.sleep(30)")
    with patch.object(diag, "_remount_commands", return_value=[hang]), patch.object(
        diag, "_mount_down", return_value=True
    ), patch.object(diag, "_REMOUNT_POLL", 0.02), patch.object(
        diag.xbmc.Monitor.return_value, "abortRequested", return_value=True
    ):
        started = time.monotonic()
        ok, _detail = diag.remount(mount)
    assert ok is False
    assert time.monotonic() - started < 10


def test_recent_kodi_errors_filters_by_time_and_level(tmp_path):
    now = time.time()
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 600))
    new = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    log = tmp_path / "kodi.log"
    log.write_text(
        "{}.1 T:1    error <general>: too old\n".format(old)
        + "{}.2 T:2     info <general>: not an error\n".format(new)
        + "{}.3 T:3    error <general>: CFileCache::Open - failed\n".format(new)
        + "{}.4 T:3    error <general>: CFileCache::Open - failed\n".format(new)
        + "{}.5 T:4    error <general>: OpenFile apikey=secret123\n".format(new)
    )

    errors = diag.recent_kodi_errors(now - 5, log_path=str(log))

    assert errors[0] == "CFileCache::Open - failed"
    assert len(errors) == 2
    assert "secret123" not in errors[1]


def test_recent_kodi_errors_missing_log(tmp_path):
    assert not diag.recent_kodi_errors(0, log_path=str(tmp_path / "none.log"))


def test_offer_remount_shows_ok_dialog_when_not_remountable():
    result = diag.Diagnosis(diag.NO_VIDEO, "no video")
    dialog = MagicMock()
    with patch.object(diag.xbmcgui, "Dialog", return_value=dialog):
        assert diag.offer_remount("Addon", result) is False
    dialog.ok.assert_called_once_with("Addon", "no video")
    dialog.yesno.assert_not_called()


def test_offer_remount_remounts_on_yes():
    mount = diag.MountConfig("/storage/nas", "systemd", "storage-nas")
    result = diag.Diagnosis(diag.MOUNT_DOWN, "down", mount)
    dialog = MagicMock()
    dialog.yesno.return_value = True
    with patch.object(diag.xbmcgui, "Dialog", return_value=dialog), patch.object(
        diag, "_remount_supported", return_value=True
    ), patch.object(diag, "remount", return_value=(True, "")) as remount:
        assert diag.offer_remount("Addon", result) is True
    remount.assert_called_once_with(mount)


def test_recent_kodi_errors_prefers_player_lines(tmp_path):
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    log = tmp_path / "kodi.log"
    log.write_text(
        "{}.1 T:1    error <general>: Control 50 can't focus\n".format(now)
        + "{}.2 T:2    error <general>: CVideoPlayer::OpenInputStream - error\n".format(
            now
        )
    )

    errors = diag.recent_kodi_errors(time.time() - 5, log_path=str(log))

    assert errors == ["CVideoPlayer::OpenInputStream - error"]


def test_recent_kodi_errors_finds_real_errors_just_before_the_start_and_drops_own(
    tmp_path,
):
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 2))
    log = tmp_path / "kodi.log"
    log.write_text(
        "{0}.1 T:1 error <general>: OpenDemuxStream - Error creating demuxer\n"
        "{0}.2 T:2 error <general>: NeNeTeePee-Stream-Kodi: Playback never\n".format(
            stamp
        )
    )

    errors = diag.recent_kodi_errors(now, log_path=str(log))

    assert errors == ["OpenDemuxStream - Error creating demuxer"]


def test_mount_down_does_not_wait_out_a_hung_automount(fs):
    mount = diag.MountConfig("/mnt/nas", "systemd", "mnt-nas", True)
    release = threading.Event()
    with patch.object(diag.os, "listdir", side_effect=lambda _p: release.wait(30)):
        with patch.object(diag, "_AUTOMOUNT_WAIT", 0.05):
            started = time.monotonic()
            assert diag._mount_down(mount) is True
            assert time.monotonic() - started < 10
    release.set()
