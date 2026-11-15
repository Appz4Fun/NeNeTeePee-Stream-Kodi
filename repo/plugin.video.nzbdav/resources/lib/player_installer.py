# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Player JSON installer for TMDBHelper and compatible player folders."""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.http_util import notify as _notify
from resources.lib.i18n import addon_name as _addon_name
from resources.lib.i18n import fmt as _fmt
from resources.lib.i18n import string as _string

ADDON_DATA_ROOT = "special://profile/addon_data/"
ADDONS_ROOT = "special://home/addons/"
NZBDAV_ADDON_ID = "plugin.video.nzbdav"
PLAYER_FILENAME = "nzbdav.json"
TMDBHELPER_ADDON_ID = "plugin.video.themoviedb.helper"
TMDBHELPER_LABEL = "TMDBHelper"

# Forks of TMDBHelper that a skin installs beside it. A fork reads its own
# players folder and its own default-player settings, so a title opened through
# the fork never sees a player that was installed only into TMDBHelper.
TMDBHELPER_FORKS = (("plugin.video.tmdb.bingie.helper", "TMDb Bingie Helper"),)

# TMDBHelper and its forks store a default player as "<file> <mode>".
DEFAULT_PLAYER_SETTINGS = (
    ("default_player_movies", PLAYER_FILENAME + " play_movie"),
    ("default_player_episodes", PLAYER_FILENAME + " play_episode"),
)

# The file a helper's "create player" template writes for this add-on. It
# opens the add-on as a folder with only a title, so it can never play.
GENERATED_PLAYER_FILENAME = "autogen." + NZBDAV_ADDON_ID + ".json"


def _player_path_for(addon_id):
    return ADDON_DATA_ROOT + addon_id + "/players/"


TMDBHELPER_PLAYER_PATH = _player_path_for(TMDBHELPER_ADDON_ID)

# Bump this when PLAYER_JSON's shape changes in a way that requires the
# installer to overwrite an older generation. The installer ignores the user's
# manual edits only when the stored schema_version differs from this value.
_PLAYER_SCHEMA_VERSION = 11

PLAYER_JSON = {
    "name": "NeNeTeePee-Stream-Kodi",
    "plugin": "plugin.video.nzbdav",
    "priority": 100,
    "is_resolvable": "false",
    "schema_version": _PLAYER_SCHEMA_VERSION,
    "play_movie": (
        "executebuiltin://RunScript("
        "special://home/addons/plugin.video.nzbdav/addon.py,tmdb_play,"
        "type=movie,title={title_url},year={year},imdb={imdb},tmdb_id={tmdb})"
    ),
    "play_episode": (
        "executebuiltin://RunScript("
        "special://home/addons/plugin.video.nzbdav/addon.py,tmdb_play,"
        "type=episode,title={showname_url},year={showyear},season={season},"
        "episode={episode},imdb={imdb},tmdb_id={tmdb},tvdb={tvdb},"
        "ep_season={ep_showseason},ep_episode={ep_showepisode},"
        "tvshow_tmdb_id={tmdb},episode_tmdb_id={eptmdb},episode_title={title_url},"
        "premiered={premiered})"
    ),
}


def _addon_label(addon_id):
    try:
        name = xbmcaddon.Addon(addon_id).getAddonInfo("name")
    except Exception as e:  # pylint: disable=broad-except
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: Could not read addon name for {}: {}").format(
                addon_id, e
            ),
            xbmc.LOGDEBUG,
        )
        name = ""

    if isinstance(name, str) and name and name != addon_id:
        return "{} ({})".format(name, addon_id)
    return addon_id


def discover_other_player_targets():
    """Return non-TMDBHelper addon_data player folders that already exist."""
    try:
        addon_dirs, _files = xbmcvfs.listdir(ADDON_DATA_ROOT)
    except Exception as e:  # pylint: disable=broad-except
        xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: Failed to list addon_data for "
                "player targets: {}"
            ).format(e),
            xbmc.LOGWARNING,
        )
        return []

    targets = []
    for addon_id in sorted(addon_dirs):
        if addon_id in (TMDBHELPER_ADDON_ID, NZBDAV_ADDON_ID):
            continue

        player_path = _player_path_for(addon_id)
        real_path = xbmcvfs.translatePath(player_path)
        if not xbmcvfs.exists(real_path):
            continue

        targets.append(
            {
                "addon_id": addon_id,
                "label": _addon_label(addon_id),
                "path": player_path,
            }
        )

    return targets


def _player_path_inside_profile(real_path):
    """Return True iff ``real_path`` resolves inside the addon_data profile.

    Defensive check: if special:// resolution is ever hijacked (symlink,
    environment override, Kodi mis-config) the installer would otherwise happily write
    nzbdav.json anywhere on disk.
    """
    profile_root = xbmcvfs.translatePath(ADDON_DATA_ROOT)
    # Use os.path.commonpath so a sibling like `<root>/addon_data_evil/<path>`
    # doesn't pass the prefix check just because its name happens to start
    # with `addon_data`. Closes TODO.md §H.3.
    real_resolved = os.path.realpath(real_path)
    profile_resolved = os.path.realpath(profile_root)
    try:
        common = os.path.commonpath([real_resolved, profile_resolved])
    except ValueError:
        # Different drive on Windows—definitely not inside profile_root.
        common = ""
    return common == profile_resolved


def _existing_player_is_current(file_path, target_name):
    """Return True iff an existing player file matches the current schema.

    When the schema differs, back up the old file before the caller
    overwrites. Unreadable/malformed files report False (overwrite).
    """
    try:
        existing_f = xbmcvfs.File(file_path, "r")
        try:
            existing_text = existing_f.read()
        finally:
            existing_f.close()
        existing = json.loads(existing_text)
    except (OSError, ValueError, TypeError):
        # Unreadable or malformed existing file (including the
        # MagicMock-returns-MagicMock case in tests)—just overwrite.
        return False

    if existing.get("schema_version") == _PLAYER_SCHEMA_VERSION:
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Player already installed at schema v{}; "
            "preserving existing file".format(_PLAYER_SCHEMA_VERSION),
            xbmc.LOGINFO,
        )
        _notify(_addon_name(), _fmt(30094, target_name))
        return True

    # Schema change—back up the old file before overwriting. If the backup
    # cannot be written, re-raise so the caller's handler aborts the install
    # (LOGERROR + "Failed" toast) and the user's existing file is preserved
    # rather than silently overwritten without a backup.
    backup_path = os.path.splitext(file_path)[0] + ".bak"
    try:
        xbmcvfs.copy(file_path, backup_path)
    except Exception as e:  # pylint: disable=broad-except
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: Could not back up {} to {}: {}").format(
                file_path, backup_path, e
            ),
            xbmc.LOGWARNING,
        )
        raise
    return False


def _install_player_to_path(target_name, target_path):
    """Install player JSON to the requested Kodi player directory.

    Returns True when the folder holds a current player afterwards, whether
    this call wrote it or kept the one already there.
    """
    player_content = json.dumps(PLAYER_JSON, indent=4)

    xbmc.log(
        ("NeNeTeePee-Stream-Kodi: Installing player to {} at {}").format(
            target_name, target_path
        ),
        xbmc.LOGINFO,
    )
    try:
        real_path = xbmcvfs.translatePath(target_path)

        if not _player_path_inside_profile(real_path):
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Refusing to install player outside addon_data "
                "(resolved {} from {})".format(real_path, target_path),
                xbmc.LOGERROR,
            )
            _notify(_addon_name(), _fmt(30095, target_name))
            return False

        if not xbmcvfs.exists(real_path):
            if not xbmcvfs.mkdirs(real_path):
                xbmc.log(
                    (
                        "NeNeTeePee-Stream-Kodi: Failed to create player "
                        "directory {}"
                    ).format(real_path),
                    xbmc.LOGERROR,
                )
                _notify(_addon_name(), _fmt(30095, target_name))
                return False

        file_path = os.path.join(real_path, PLAYER_FILENAME)

        # If an existing nzbdav.json is present with the SAME schema_version,
        # skip the overwrite so a user who edited the file (for example, customized
        # priority, added extra fields) doesn't lose those edits on every
        # addon upgrade. Different schema_version → overwrite with a backup.
        if xbmcvfs.exists(file_path) and _existing_player_is_current(
            file_path, target_name
        ):
            return True

        _write_player_file(file_path, player_content, target_name)
        return True
    except Exception as e:
        xbmc.log(
            ("NeNeTeePee-Stream-Kodi: Failed to install player: {}").format(e),
            xbmc.LOGERROR,
        )
        _notify(_addon_name(), _fmt(30095, target_name))
        return False


def _write_player_file(file_path, player_content, target_name):
    """Write the player JSON and notify on success.

    Raises ``OSError`` on a partial/failed write so the caller's handler
    reports the failure instead of a false success.
    """
    f = xbmcvfs.File(file_path, "w")
    try:
        # xbmcvfs.File.write returns False on disk-full / permission
        # failure rather than raising; without this check the install
        # path used to log "successfully" and toast a success
        # notification on a partial write. TODO.md §H.2-L23.
        wrote = f.write(player_content)
        if wrote is False:
            raise OSError(
                "xbmcvfs.File.write returned False (disk-full or permission failure)"
            )
        xbmc.log("NeNeTeePee-Stream-Kodi: Player installed successfully", xbmc.LOGINFO)
        _notify(_addon_name(), _fmt(30094, target_name))
    finally:
        f.close()


def _addon_is_installed(addon_id):
    """Return True when the add-on's folder is on disk.

    Reads the file system only, so the service can ask at start-up without
    building a Kodi add-on object.
    """
    try:
        folder = xbmcvfs.translatePath(ADDONS_ROOT + addon_id + "/")
        return os.path.isfile(os.path.join(folder, "addon.xml"))
    except Exception:  # pylint: disable=broad-except
        return False


def _installed_helpers():
    """Return (addon_id, label) for TMDBHelper and each fork that is installed.

    TMDBHelper is always listed, as it was before forks were known: the
    install button names it and has always written its player folder.
    """
    helpers = [(TMDBHELPER_ADDON_ID, TMDBHELPER_LABEL)]
    helpers.extend(fork for fork in TMDBHELPER_FORKS if _addon_is_installed(fork[0]))
    return helpers


def _enable_tmdbhelper_action_player_mode(addon_id=TMDBHELPER_ADDON_ID):
    """Make TMDBHelper execute non-resolvable player actions directly."""
    try:
        xbmcaddon.Addon(addon_id).setSetting("only_resolve_strm", "true")
    except Exception as e:  # pylint: disable=broad-except
        xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: Could not enable STRM-only resolver "
                "mode in {}: {}"
            ).format(addon_id, e),
            xbmc.LOGWARNING,
        )


def _default_player_is_open(current):
    """True when the stored default is empty or the generated player."""
    if not isinstance(current, str):
        return False
    parts = current.split()
    return not parts or parts[0] == GENERATED_PLAYER_FILENAME


def _make_default_player(addon_id):
    """Choose this player where the helper has no working default yet.

    A default the user already chose, for this or any other player, is left
    alone. The one exception is the player a helper generates for this add-on
    from its template: that one cannot play, so this player replaces it.
    """
    try:
        addon = xbmcaddon.Addon(addon_id)
        for setting_id, value in DEFAULT_PLAYER_SETTINGS:
            current = addon.getSetting(setting_id)
            if _default_player_is_open(current):
                addon.setSetting(setting_id, value)
                xbmc.log(
                    "NeNeTeePee-Stream-Kodi: Set {} in {} to '{}'".format(
                        setting_id, addon_id, value
                    ),
                    xbmc.LOGINFO,
                )
    except Exception as e:  # pylint: disable=broad-except
        xbmc.log(
            (
                "NeNeTeePee-Stream-Kodi: Could not set the default player in " "{}: {}"
            ).format(addon_id, e),
            xbmc.LOGWARNING,
        )


def install_player():
    """Install the player into TMDBHelper and every installed fork of it."""
    for addon_id, label in _installed_helpers():
        _enable_tmdbhelper_action_player_mode(addon_id)
        if _install_player_to_path(label, _player_path_for(addon_id)):
            _make_default_player(addon_id)


def install_player_other():
    """Prompt for another compatible player directory and install there."""
    targets = discover_other_player_targets()
    if not targets:
        _notify(_addon_name(), _string(30162), 5000)
        return

    labels = [target["label"] for target in targets]
    selected = xbmcgui.Dialog().select(_string(30161), labels)
    if selected < 0:
        return

    target = targets[selected]
    _install_player_to_path(target["label"], target["path"])


def _player_folder(addon_id):
    """Return the helper's real players folder, or None when it is unsafe."""
    folder = xbmcvfs.translatePath(_player_path_for(addon_id))
    if not _player_path_inside_profile(folder):
        return None
    return folder


def _copy_player_to_other_helpers(states, folders):
    """Give the player to an installed helper that has none.

    Runs only when the player is already installed in one helper, which is
    the user's choice to use it. Writes the file and nothing else: no setting
    of the other add-on is read or changed here. A deleted copy comes back on
    the next start; to hide the player in one helper, disable it there.
    """
    from resources.lib.player_upgrade import add_player, player_is_installed

    missing = [
        addon_id
        for addon_id, state in states.items()
        if state == "absent" and _addon_is_installed(addon_id)
    ]
    if not missing:
        return
    # A helper that was uninstalled can leave its player file behind in
    # addon_data. Only a helper that is still installed counts as a source,
    # or that leftover would keep bringing back a player the user removed.
    if not any(
        player_is_installed(folders[addon_id], PLAYER_JSON)
        for addon_id, state in states.items()
        if state != "absent" and folders.get(addon_id) and _addon_is_installed(addon_id)
    ):
        return
    for addon_id in missing:
        add_player(folders[addon_id], PLAYER_JSON)


def upgrade_installed_tmdbhelper_player():
    """Silently keep an installed player current in TMDBHelper and its forks.

    An existing player is migrated to the current schema. When the player is
    installed in one helper, an installed fork without it gets a copy, so a
    title opened through the fork can use it too. No add-on setting changes.
    Returns the state of TMDBHelper's own player.
    """
    from resources.lib.player_upgrade import upgrade_player

    states = {}
    folders = {}
    for addon_id in [TMDBHELPER_ADDON_ID] + [fork[0] for fork in TMDBHELPER_FORKS]:
        try:
            folder = _player_folder(addon_id)
            if folder is None:
                states[addon_id] = "failed"
                continue
            folders[addon_id] = folder
            states[addon_id] = upgrade_player(folder, PLAYER_JSON)
        except Exception:  # pylint: disable=broad-except
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: Automatic player upgrade failed",
                xbmc.LOGWARNING,
            )
            states[addon_id] = "failed"
    try:
        _copy_player_to_other_helpers(states, folders)
    except Exception:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: Automatic player copy failed", xbmc.LOGWARNING
        )
    return states[TMDBHELPER_ADDON_ID]
