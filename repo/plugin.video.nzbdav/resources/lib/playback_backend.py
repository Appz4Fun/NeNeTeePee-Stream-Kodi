# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Stable backend values and migration from the former NZBGet toggle."""

import xbmcaddon

_BACKENDS = {"0": "nzbdav", "1": "nzbget", "2": "streamnzb"}
_BACKUP_SWITCH_MIGRATED = "nzbget_backup_switch_migrated"


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


def migrate_nzbget_backup_switch():
    """Keep NZBGet backups off for anyone who had turned them off before.

    NZBGet duplicate backups used to need **Enable fallback streams** as well as
    **Maximum duplicate backups**. That switch is now nzbdav-only, so an NZBGet
    user who had it off would suddenly get backups again. Once, that choice is
    carried over as ``nzbget_max_backups = 0``. Reads the saved XML (like
    ``migrate_backend``); a refused write leaves the flag unset for a retry.
    """
    from resources.lib.router import _get_script_setting

    if _get_script_setting(_BACKUP_SWITCH_MIGRATED, "false") == "true":
        return
    fallback_off = (
        _get_script_setting("fallback_streams_enabled", "true") or ""
    ).strip().lower() == "false"
    try:
        addon = xbmcaddon.Addon("plugin.video.nzbdav")
        if fallback_off and get_backend(_get_script_setting) == "nzbget":
            addon.setSetting("nzbget_max_backups", "0")
        addon.setSetting(_BACKUP_SWITCH_MIGRATED, "true")
    except (RuntimeError, AttributeError, TypeError):
        # Kodi can refuse settings writes during startup or shutdown.
        pass


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
