# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Serialize identity publication and native playback across addon processes."""

import os
import time
import uuid
from contextlib import contextmanager

import xbmc
import xbmcgui
import xbmcvfs

from resources.lib.player_upgrade import _lock_player

SESSION_PROPERTY = "nzbdav.playback_session"
PENDING_PROPERTY = "nzbdav.pending_playback_session"
MONITOR_PROPERTIES = (
    "nzbdav.active",
    "nzbdav.playing",
    "nzbdav.stream_url",
    "nzbdav.resume_key",
    "nzbdav.resume_offset",
    "nzbdav.stream_title",
    "nzbdav.playback_metadata",
)


def _lock_path():
    folder = xbmcvfs.translatePath("special://profile/addon_data/plugin.video.nzbdav/")
    if not isinstance(folder, str) or not os.path.isabs(folder):
        raise OSError("Playback handoff profile is unavailable")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "playback-handoff.lock")


@contextmanager
def snapshot_guard(wait=False):
    """Take a process lock; service reads/retries never wait on GUI writers."""
    try:
        lock = open(_lock_path(), "a+b")
    except OSError:
        yield None
        return
    with lock:
        deadline = time.monotonic() + 5.0
        monitor = xbmc.Monitor()
        while not _lock_player(lock):
            if not wait:
                yield False
                return
            if time.monotonic() >= deadline or monitor.waitForAbort(0.01):
                raise RuntimeError("Playback handoff interrupted")
        yield True


def _read_token(home, key):
    value = home.getProperty(key)
    return value if isinstance(value, str) else ""


def session_token(home):
    """Read the committed token as a string, independent of item metadata."""
    return _read_token(home, SESSION_PROPERTY)


def session_matches(home, token):
    """A pending reservation invalidates old retries before context is changed."""
    try:
        return (
            _read_token(home, SESSION_PROPERTY) == token
            and _read_token(home, PENDING_PROPERTY) == token
        )
    except RuntimeError:
        return False


def _clear_properties(home, keys):
    for key in keys:
        try:
            home.clearProperty(key)
        except RuntimeError:
            # GUI teardown must not conceal the original playback failure.
            pass


def _rollback_context(home, token):
    if home is None:
        return
    try:
        if session_token(home) != token:
            return
        _clear_properties(
            home, MONITOR_PROPERTIES + ("TMDbHelper.PlayerInfoString", SESSION_PROPERTY)
        )
        if _read_token(home, PENDING_PROPERTY) == token:
            _clear_properties(home, (PENDING_PROPERTY,))
    except RuntimeError:
        # No ownership can be proven while the GUI is unavailable.
        pass


@contextmanager
def handoff(home=None, expected_session=None, monitored=True, play_url=""):
    """Reserve a fresh start, or reject a retry superseded by another process.

    Hold this guard through prepare_playback, monitor properties and native
    Player.play/setResolvedUrl. A new context cannot slip between an old
    retry's publication and its native playback call.
    """
    token = uuid.uuid4().hex if expected_session is None else expected_session
    previous_pending = ""
    entered = False
    try:
        home = home if home is not None else xbmcgui.Window(10000)
        if expected_session is None:
            previous_pending = _read_token(home, PENDING_PROPERTY)
            home.setProperty(PENDING_PROPERTY, token)
    except RuntimeError:
        home = None
    try:
        with snapshot_guard(wait=expected_session is None) as locked:
            if expected_session is not None and (
                not locked
                or home is None
                or not session_matches(home, expected_session)
            ):
                yield None
                return
            if expected_session is None and home is not None:
                try:
                    pending = _read_token(home, PENDING_PROPERTY)
                except RuntimeError:
                    pending = token
                if pending != token:
                    raise RuntimeError("Playback handoff superseded")
                keys = (
                    MONITOR_PROPERTIES
                    if not monitored
                    else ("nzbdav.active", "nzbdav.playing")
                )
                _clear_properties(home, keys)
                try:
                    # Keep the new proxy identifiable to an obsolete stop callback,
                    # even before a monitored route writes ACTIVE last.
                    home.setProperty("nzbdav.stream_url", play_url)
                except RuntimeError:
                    # Playback metadata and native playback still get their chance.
                    pass
            # Fresh streams still work if optional profile/GUI coordination is
            # unavailable. Retries fail closed when their session cannot be proven.
            entered = True
            try:
                yield token
            except Exception:
                _rollback_context(home, token)
                raise
    finally:
        if not entered and expected_session is None and home is not None:
            try:
                if _read_token(home, PENDING_PROPERTY) == token:
                    home.setProperty(PENDING_PROPERTY, previous_pending)
            except RuntimeError:
                # An unavailable GUI cannot be repaired by blocking shutdown.
                pass
