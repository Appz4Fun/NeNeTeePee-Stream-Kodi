# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Stable backend values and migration from the former NZBGet toggle."""

import xbmcaddon

_BACKENDS = {"0": "nzbdav", "1": "nzbget", "2": "streamnzb"}


def get_setting(key, default=""):
    """Adapt the shared addon-first reader to the backend's key-first API."""
    from resources.lib.router_settings import _get_addon_setting

    return _get_addon_setting(xbmcaddon.Addon("plugin.video.nzbdav"), key, default)


def get_backend(settings_getter=None):
    """Read the dropdown, with a legacy fallback for unmigrated settings."""
    try:
        if settings_getter is None:
            settings_getter = get_setting
        value = settings_getter("playback_backend", "")
        if value in _BACKENDS:
            return _BACKENDS[value]
        legacy = settings_getter("nzbget_enabled", "false")
        return "nzbget" if (legacy or "").strip().lower() == "true" else "nzbdav"
    except (RuntimeError, AttributeError, TypeError):
        return "nzbdav"


def migrate_backend():
    """Persist the old choice once, before Kodi's dropdown default masks it.

    Read the saved XML through the existing RunScript reader: getSetting may
    return the new default even when the user has never selected a backend,
    and the removed toggle may no longer be exposed through Kodi's API.
    """
    from resources.lib.router import _get_script_setting

    if _get_script_setting("playback_backend", "") in _BACKENDS:
        return
    value = "1" if get_backend(_get_script_setting) == "nzbget" else "0"
    try:
        xbmcaddon.Addon("plugin.video.nzbdav").setSetting("playback_backend", value)
    except (RuntimeError, AttributeError, TypeError):
        # Kodi can refuse settings writes during startup or shutdown. Leave
        # the stored legacy value intact so the next invocation can retry.
        pass
