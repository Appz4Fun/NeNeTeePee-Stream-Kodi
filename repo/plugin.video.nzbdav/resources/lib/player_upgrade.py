# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Atomic, silent upgrades of an already installed addon-owned player."""

import json
import os
import shutil
import tempfile
import uuid

import xbmc


def _lock_player(lock):
    """Acquire a process lock without waiting or leaving crash-stale ownership."""
    try:
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-error

            lock.seek(0)
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # pylint: disable=import-outside-toplevel

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        return False
    return True


def _read_player(path, definition):
    """Return an old, addon-owned player; never downgrade or guess ownership."""
    with open(path, "rb") as source:
        original = source.read()
    player = json.loads(original)
    if not isinstance(player, dict):
        raise ValueError("Player must be an object")
    if player.get("plugin") != definition["plugin"]:
        return original, None
    version = player.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError("Invalid player schema")
    if version >= definition["schema_version"]:
        return original, None
    return original, player


def _replace_player(path, original, player):
    """Back up, stage, validate and replace without truncating the live file."""
    backup = os.path.splitext(path)[0] + "." + uuid.uuid4().hex + ".bak"
    shutil.copy2(path, backup)
    with open(backup, "rb") as saved:
        if saved.read() != original:
            raise OSError("Player backup verification failed")
    fd, stage = tempfile.mkstemp(prefix=".nzbdav-player-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(player, output, indent=4)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        with open(stage, encoding="utf-8") as staged:
            if json.load(staged) != player:
                raise OSError("Player staging verification failed")
        with open(path, "rb") as current:
            if current.read() != original:
                raise OSError("Player changed during upgrade")
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)


def upgrade_player(folder, definition):
    """Upgrade only an existing player, retaining user fields and old routes."""
    path = os.path.join(folder, "nzbdav.json")
    if not os.path.isfile(path):
        return "absent"
    lock_path = os.path.splitext(path)[0] + ".upgrade.lock"
    if os.path.islink(path) or os.path.islink(lock_path):
        return "failed"
    try:
        with open(lock_path, "a+b") as lock:
            if not _lock_player(lock):
                return "deferred"
            original, player = _read_player(path, definition)
            if player is None:
                return "unchanged"
            for key in (
                "play_movie",
                "play_episode",
                "is_resolvable",
                "schema_version",
            ):
                player[key] = definition[key]
            _replace_player(path, original, player)
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Installed player automatically upgraded",
            xbmc.LOGINFO,
        )
        return "updated"
    except (OSError, ValueError, TypeError):
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Automatic player upgrade failed; "
            "original retained",
            xbmc.LOGWARNING,
        )
        return "failed"
