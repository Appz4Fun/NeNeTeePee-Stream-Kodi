# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from resources.lib.player_installer import (
    PLAYER_JSON,
    TMDBHELPER_PLAYER_PATH,
    discover_other_player_targets,
    install_player,
    install_player_other,
)


def test_tmdbhelper_path_defined():
    assert "themoviedb.helper" in TMDBHELPER_PLAYER_PATH


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_writes_file(mock_vfs, mock_notify):
    """Player file gets written to TMDBHelper directory."""
    mock_vfs.exists.return_value = True
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_file = MagicMock()
    mock_vfs.File.return_value = mock_file

    install_player()

    # Installer may read the existing file first (schema-version check) and
    # then write. Assert the write happened and that each File call was
    # against nzbdav.json.
    assert mock_vfs.File.call_count >= 1
    write_calls = [
        c for c in mock_vfs.File.call_args_list if len(c[0]) >= 2 and c[0][1] == "w"
    ]
    assert len(write_calls) == 1
    assert "nzbdav.json" in write_calls[0][0][0]
    mock_file.write.assert_called_once()
    mock_notify.assert_called()


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_creates_directory_when_missing(mock_vfs, mock_notify):
    """Should call mkdirs when target dir doesn't exist."""
    mock_vfs.exists.return_value = False
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_file = MagicMock()
    mock_vfs.File.return_value = mock_file

    install_player()

    mock_vfs.mkdirs.assert_called_once()


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_handles_write_failure(mock_vfs, mock_notify):
    """Should catch write exceptions and notify about failure."""
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = True
    mock_vfs.File.side_effect = OSError("Disk full")

    install_player()

    notify_calls = [str(c) for c in mock_notify.call_args_list]
    assert any("Failed" in s or "failed" in s for s in notify_calls)


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_aborts_without_overwrite_when_backup_fails(
    mock_vfs, mock_notify
):
    """A schema-mismatch backup failure must NOT overwrite the user's file:
    the install aborts (Failed toast) so existing customizations are preserved
    instead of being silently replaced with no backup."""
    from resources.lib.player_installer import _PLAYER_SCHEMA_VERSION

    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = True
    mock_read_file = MagicMock()
    mock_read_file.read.return_value = json.dumps(
        {"name": "stale", "schema_version": _PLAYER_SCHEMA_VERSION - 1}
    )
    mock_write_file = MagicMock()

    def _file_factory(_path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        return mock_read_file if mode == "r" else mock_write_file

    mock_vfs.File.side_effect = _file_factory
    mock_vfs.copy.side_effect = OSError("backup target read-only")

    install_player()  # must not raise out of the public entrypoint

    assert mock_vfs.copy.called  # backup was attempted
    mock_write_file.write.assert_not_called()  # the overwrite never happened
    assert any(
        "Failed" in str(c) or "failed" in str(c) for c in mock_notify.call_args_list
    )


def test_player_json_uses_script_handoff_instead_of_plugin_media_url():
    assert PLAYER_JSON["is_resolvable"] == "false"
    assert PLAYER_JSON["play_movie"].startswith(
        "executebuiltin://RunScript("
        "special://home/addons/plugin.video.nzbdav/addon.py,tmdb_play,"
    )
    assert "plugin://plugin.video.nzbdav" not in PLAYER_JSON["play_movie"]
    assert "plugin.video.nzbdav" in PLAYER_JSON["play_movie"]
    assert "type=movie" in PLAYER_JSON["play_movie"]
    assert "title={title_url}" in PLAYER_JSON["play_movie"]
    # tmdb_id must be forwarded so resolver can clear TMDBHelper bookmarks
    assert "tmdb_id={tmdb}" in PLAYER_JSON["play_movie"]
    assert PLAYER_JSON["play_episode"].startswith(
        "executebuiltin://RunScript("
        "special://home/addons/plugin.video.nzbdav/addon.py,tmdb_play,"
    )
    assert "plugin://plugin.video.nzbdav" not in PLAYER_JSON["play_episode"]
    assert "plugin.video.nzbdav" in PLAYER_JSON["play_movie"]
    assert "plugin.video.nzbdav" in PLAYER_JSON["play_episode"]
    assert "type=episode" in PLAYER_JSON["play_episode"]
    assert "title={showname_url}" in PLAYER_JSON["play_episode"]
    assert "tmdb_id={tmdb}" in PLAYER_JSON["play_episode"]
    roundtripped = json.loads(json.dumps(PLAYER_JSON))
    assert roundtripped["name"] == PLAYER_JSON["name"]


def test_play_episode_forwards_tvdb_token():
    """The episode action requests TMDBHelper's {tvdb} token—in episode
    context it resolves to the show's TheTVDB id, letting providers search
    by tvdbid (issue #318)."""
    assert "tvdb={tvdb}" in PLAYER_JSON["play_episode"]
    # Movies have no series tvdb id; the movie action must not carry it.
    assert "tvdb=" not in PLAYER_JSON["play_movie"]


def test_bundled_player_matches_generated_definition():
    bundled = (
        Path(__file__).resolve().parents[1]
        / "repo/plugin.video.nzbdav/resources/players/nzbdav.json"
    )
    assert json.loads(bundled.read_text(encoding="utf-8")) == PLAYER_JSON


def test_player_schema_version_bumped_for_tvdb_token():
    """Adding the {tvdb} token changes the shipped action string, so the
    schema version must advance to force a re-install over older files."""
    from resources.lib.player_installer import _PLAYER_SCHEMA_VERSION

    assert _PLAYER_SCHEMA_VERSION >= 7
    assert PLAYER_JSON["schema_version"] == _PLAYER_SCHEMA_VERSION


@patch("resources.lib.player_installer.xbmcaddon")
@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_enables_tmdbhelper_strm_only_for_script_handoff(
    mock_vfs, mock_notify, mock_addon
):
    """TMDBHelper action players should skip the dummy resolver probe.

    The CoreELEC crash reproduces before NeNeTeePee-Stream-Kodi starts when TMDBHelper
    asks
    Kodi to open plugin://plugin.video.nzbdav/... as media. Script handoff
    avoids that path, but TMDBHelper only executes non-resolvable actions
    directly when only_resolve_strm is true.
    """
    mock_vfs.exists.return_value = True
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.File.return_value = MagicMock()
    tmdb_addon = MagicMock()
    mock_addon.Addon.return_value = tmdb_addon

    install_player()

    mock_addon.Addon.assert_any_call("plugin.video.themoviedb.helper")
    tmdb_addon.setSetting.assert_called_once_with("only_resolve_strm", "true")


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_refuses_to_write_outside_addon_data(mock_vfs, mock_notify):
    """Defensive check: if special:// resolution maps TMDBHelper's player
    directory outside the Kodi profile's addon_data root, the installer
    must refuse to write and notify the user—no arbitrary-filesystem
    write."""

    # Force translatePath to return a path that doesn't live under
    # addon_data. realpath of both is stable since the test picks real-ish
    # temp-ish paths the test host resolves identically.
    def _translate(path):
        if "profile/addon_data/" in path and path.endswith("addon_data/"):
            return "/home/kodi/.kodi/userdata/addon_data/"
        return "/tmp/hostile-elsewhere/plugin.video.themoviedb.helper/players/"

    mock_vfs.translatePath.side_effect = _translate
    mock_vfs.exists.return_value = True
    mock_vfs.File.return_value = MagicMock()

    install_player()

    # No write was attempted.
    write_calls = [
        c for c in mock_vfs.File.call_args_list if len(c[0]) >= 2 and c[0][1] == "w"
    ]
    assert len(write_calls) == 0, "Installer wrote despite escape-prevention check"
    # Notified the user of the failure.
    notify_msgs = [str(c) for c in mock_notify.call_args_list]
    assert any("TMDBHelper" in m for m in notify_msgs)


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_bails_when_mkdirs_fails(mock_vfs, mock_notify):
    """If the addon_data sub-directory doesn't exist and mkdirs() fails
    (filesystem read-only, permissions, etc.), the installer must bail
    out cleanly instead of trying to write into a non-existent dir."""
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = False
    mock_vfs.mkdirs.return_value = False  # creation fails
    mock_vfs.File.return_value = MagicMock()

    install_player()

    write_calls = [
        c for c in mock_vfs.File.call_args_list if len(c[0]) >= 2 and c[0][1] == "w"
    ]
    assert len(write_calls) == 0
    notify_msgs = [str(c) for c in mock_notify.call_args_list]
    assert any("TMDBHelper" in m for m in notify_msgs)


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_preserves_existing_file_on_matching_schema(
    mock_vfs, mock_notify
):
    """If a user has a hand-edited nzbdav.json with the current
    schema_version, do NOT overwrite—preserves their customizations."""
    from resources.lib.player_installer import _PLAYER_SCHEMA_VERSION

    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = True
    # The existing file reports the same schema_version as shipped code.
    existing_contents = (
        '{"name": "user-customized", "schema_version": '
        + str(_PLAYER_SCHEMA_VERSION)
        + "}"
    )
    mock_read_file = MagicMock()
    mock_read_file.read.return_value = existing_contents
    mock_write_file = MagicMock()

    # pylint: disable=keyword-arg-before-vararg
    # `open(path, mode, *)` is the builtin signature this factory mirrors.
    def _file_factory(path, mode="r", *a, **kw):
        return mock_read_file if mode == "r" else mock_write_file

    mock_vfs.File.side_effect = _file_factory

    install_player()

    mock_write_file.write.assert_not_called()


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_backs_up_and_overwrites_on_schema_mismatch(
    mock_vfs, mock_notify
):
    """Existing stale player JSON must be backed up outside TMDBHelper's scan."""
    from resources.lib.player_installer import _PLAYER_SCHEMA_VERSION

    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = True
    existing_contents = (
        '{"name": "stale", "schema_version": ' + str(_PLAYER_SCHEMA_VERSION - 1) + "}"
    )
    mock_read_file = MagicMock()
    mock_read_file.read.return_value = existing_contents
    mock_write_file = MagicMock()

    # pylint: disable=keyword-arg-before-vararg
    # `open(path, mode, *)` is the builtin signature this factory mirrors.
    def _file_factory(path, mode="r", *a, **kw):
        return mock_read_file if mode == "r" else mock_write_file

    mock_vfs.File.side_effect = _file_factory

    install_player()

    # copy() was called before the overwrite fired. The backup filename must
    # not contain `.json` because TMDBHelper scans `.*\.json` with re.match
    # and otherwise treats nzbdav.json.bak as an active player.
    assert mock_vfs.copy.called
    backup_args = mock_vfs.copy.call_args[0]
    assert backup_args[1].endswith("/nzbdav.bak")
    # And the write did land after the backup.
    mock_write_file.write.assert_called_once()


@patch("resources.lib.player_installer.xbmcaddon")
@patch("resources.lib.player_installer.xbmcvfs")
def test_discover_other_player_targets_lists_existing_non_tmdbhelper_player_dirs(
    mock_vfs, mock_addon
):
    """Other-player discovery should offer existing player folders and skip
    TMDBHelper, since TMDBHelper has its own direct install action."""

    mock_vfs.listdir.return_value = (
        [
            "plugin.video.themoviedb.helper",
            "plugin.video.seren",
            "plugin.video.empty",
            "script.module.not-a-player",
        ],
        [],
    )
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )

    def _exists(path):
        return path.endswith("/plugin.video.seren/players/")

    mock_vfs.exists.side_effect = _exists
    addon_names = {
        "plugin.video.seren": "Seren",
        "plugin.video.empty": "Empty Addon",
        "script.module.not-a-player": "Not A Player",
    }

    def _addon(addon_id=None):
        addon = MagicMock()
        addon.getAddonInfo.side_effect = lambda key: (
            addon_names.get(addon_id, addon_id or "") if key == "name" else ""
        )
        return addon

    mock_addon.Addon.side_effect = _addon

    targets = discover_other_player_targets()

    assert targets == [
        {
            "addon_id": "plugin.video.seren",
            "label": "Seren (plugin.video.seren)",
            "path": "special://profile/addon_data/plugin.video.seren/players/",
        }
    ]


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcgui")
@patch("resources.lib.player_installer.xbmcaddon")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_other_prompts_and_writes_selected_target(
    mock_vfs, mock_addon, mock_gui, mock_notify
):
    """The alternate installer should prompt for discovered player folders
    and write nzbdav.json into the selected addon's players directory."""

    mock_vfs.listdir.return_value = (
        ["plugin.video.seren", "plugin.video.umbrella"],
        [],
    )
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )

    def _exists(path):
        if path.endswith("/plugin.video.seren/players/"):
            return True
        if path.endswith("/plugin.video.umbrella/players/"):
            return True
        # Force installer to skip existing-file preservation and write fresh.
        if path.endswith("/nzbdav.json"):
            return False
        return True

    mock_vfs.exists.side_effect = _exists
    addon_names = {
        "plugin.video.seren": "Seren",
        "plugin.video.umbrella": "Umbrella",
    }

    def _addon(addon_id=None):
        addon = MagicMock()
        addon.getAddonInfo.side_effect = lambda key: (
            addon_names.get(addon_id, addon_id or "") if key == "name" else ""
        )
        return addon

    mock_addon.Addon.side_effect = _addon
    mock_gui.Dialog.return_value.select.return_value = 1
    mock_file = MagicMock()
    mock_vfs.File.return_value = mock_file

    install_player_other()

    mock_gui.Dialog.return_value.select.assert_called_once()
    write_calls = [
        c for c in mock_vfs.File.call_args_list if len(c[0]) >= 2 and c[0][1] == "w"
    ]
    assert len(write_calls) == 1
    assert "/plugin.video.umbrella/players/nzbdav.json" in write_calls[0][0][0]
    mock_file.write.assert_called_once()
    notify_msgs = [str(c) for c in mock_notify.call_args_list]
    assert any("Umbrella" in msg for msg in notify_msgs)


@patch("resources.lib.player_installer._notify")
@patch("resources.lib.player_installer.xbmcgui")
@patch("resources.lib.player_installer.xbmcvfs")
def test_install_player_other_notifies_when_no_other_targets(
    mock_vfs, mock_gui, mock_notify
):
    mock_vfs.listdir.return_value = (["plugin.video.themoviedb.helper"], [])
    mock_vfs.translatePath.side_effect = lambda p: p.replace(
        "special://profile", "/home/kodi/.kodi/userdata"
    )
    mock_vfs.exists.return_value = False

    install_player_other()

    mock_gui.Dialog.return_value.select.assert_not_called()
    mock_vfs.File.assert_not_called()
    mock_notify.assert_called()


# --- TMDBHelper forks (a skin's own helper) ---------------------------------

BINGIE_HELPER_ID = "plugin.video.tmdb.bingie.helper"


def _profile(tmp_path, monkeypatch, forks_installed=(BINGIE_HELPER_ID,)):
    """Map Kodi's special:// paths onto a real folder tree under tmp_path."""
    from resources.lib import player_installer

    home = tmp_path / "home"
    data = home / "userdata" / "addon_data"
    data.mkdir(parents=True)
    for addon_id in forks_installed:
        addon_dir = home / "addons" / addon_id
        addon_dir.mkdir(parents=True)
        (addon_dir / "addon.xml").write_text("<addon/>")

    def _translate(path):
        return path.replace("special://profile", str(home / "userdata")).replace(
            "special://home", str(home)
        )

    def _mkdirs(path):
        Path(path).mkdir(parents=True, exist_ok=True)
        return True

    def _copy(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        return True

    class _File:
        def __init__(self, path, mode="r"):
            self._handle = open(path, mode, encoding="utf-8")

        def read(self):
            return self._handle.read()

        def write(self, text):
            self._handle.write(text)
            return True

        def close(self):
            self._handle.close()

    vfs = MagicMock()
    vfs.translatePath.side_effect = _translate
    vfs.exists.side_effect = lambda path: Path(path).exists()
    vfs.mkdirs.side_effect = _mkdirs
    vfs.copy.side_effect = _copy
    vfs.File.side_effect = _File
    monkeypatch.setattr(player_installer, "xbmcvfs", vfs)
    return data


def _helper_addons(monkeypatch, settings):
    """Give each helper add-on its own settings dict; return the Addon mocks."""
    from resources.lib import player_installer

    addons = {}

    def _addon(addon_id=None):
        if addon_id not in settings:
            raise RuntimeError("Unknown addon id '{}'".format(addon_id))
        if addon_id not in addons:
            addon = MagicMock()
            values = settings[addon_id]
            addon.getSetting.side_effect = lambda key: values.get(key, "")
            addon.setSetting.side_effect = values.__setitem__
            addons[addon_id] = addon
        return addons[addon_id]

    xbmcaddon = MagicMock()
    xbmcaddon.Addon.side_effect = _addon
    monkeypatch.setattr(player_installer, "xbmcaddon", xbmcaddon)
    return addons


@patch("resources.lib.player_installer._notify")
def test_install_player_also_installs_into_an_installed_fork(
    mock_notify, tmp_path, monkeypatch
):
    """A skin's own helper reads its own players folder, which may not exist
    yet. The one install button must register the player there too, or a
    title opened through the fork has no player that passes season/episode."""
    data = _profile(tmp_path, monkeypatch)
    settings = {"plugin.video.themoviedb.helper": {}, BINGIE_HELPER_ID: {}}
    _helper_addons(monkeypatch, settings)

    install_player()

    for addon_id, helper_settings in settings.items():
        player = json.loads((data / addon_id / "players" / "nzbdav.json").read_text())
        assert player == PLAYER_JSON
        assert helper_settings == {
            "only_resolve_strm": "true",
            "default_player_movies": "nzbdav.json play_movie",
            "default_player_episodes": "nzbdav.json play_episode",
        }
    labels = [call.args[1] for call in mock_notify.call_args_list]
    assert any("TMDb Bingie Helper" in label for label in labels)


@patch("resources.lib.player_installer._notify")
def test_install_player_skips_a_fork_that_is_not_installed(
    mock_notify, tmp_path, monkeypatch
):
    data = _profile(tmp_path, monkeypatch, forks_installed=())
    addons = _helper_addons(monkeypatch, {"plugin.video.themoviedb.helper": {}})

    install_player()

    assert (data / "plugin.video.themoviedb.helper" / "players").is_dir()
    assert not (data / BINGIE_HELPER_ID).exists()
    assert list(addons) == ["plugin.video.themoviedb.helper"]


@patch("resources.lib.player_installer._notify")
def test_install_player_keeps_a_default_player_the_user_chose(
    mock_notify, tmp_path, monkeypatch
):
    _profile(tmp_path, monkeypatch)
    settings = {
        "plugin.video.themoviedb.helper": {
            "default_player_movies": "other.json play_movie"
        },
        BINGIE_HELPER_ID: {"default_player_episodes": "other.json play_episode"},
    }
    _helper_addons(monkeypatch, settings)

    install_player()

    main = settings["plugin.video.themoviedb.helper"]
    assert main["default_player_movies"] == "other.json play_movie"
    assert main["default_player_episodes"] == "nzbdav.json play_episode"
    fork = settings[BINGIE_HELPER_ID]
    assert fork["default_player_movies"] == "nzbdav.json play_movie"
    assert fork["default_player_episodes"] == "other.json play_episode"


@patch("resources.lib.player_installer._notify")
def test_install_player_sets_no_default_when_the_install_fails(
    mock_notify, tmp_path, monkeypatch
):
    from resources.lib import player_installer

    _profile(tmp_path, monkeypatch, forks_installed=())
    settings = {"plugin.video.themoviedb.helper": {}}
    _helper_addons(monkeypatch, settings)
    player_installer.xbmcvfs.mkdirs.side_effect = lambda _path: False

    install_player()

    assert "default_player_movies" not in settings["plugin.video.themoviedb.helper"]


def test_fork_player_template_passes_season_and_episode():
    """The fork formats the player string from a dict that yields "_" for a
    field it does not know and "None" for a known field with no value. The
    add-on must still get the requested season and episode, and no fake id."""
    from collections import defaultdict
    from urllib.parse import quote

    from resources.lib.router import _clean_params
    from resources.lib.script_player import parse_script_args

    item = defaultdict(lambda: "_")
    item.update(
        showname_url=quote("Six Feet Under"),
        title_url=quote("The Eye Inside"),
        showyear=None,
        season=3,
        episode=3,
        imdb="tt0248654",
        tmdb=1274,
        tvdb=None,
        eptmdb=91031,
        premiered="2003-03-16",
    )
    action = PLAYER_JSON["play_episode"].format_map(item)
    prefix = "executebuiltin://RunScript("
    assert action.startswith(prefix) and action.endswith(")")
    args = action[len(prefix) : -1].split(",")
    assert args[1] == "tmdb_play"

    params = _clean_params(parse_script_args(args[2:]))

    assert params["type"] == "episode"
    assert params["title"] == "Six Feet Under"
    assert params["season"] == "3"
    assert params["episode"] == "3"
    assert params["imdb"] == "tt0248654"
    assert params["premiered"] == "2003-03-16"
    for cleared in ("year", "tvdb", "ep_season", "ep_episode"):
        assert params[cleared] == ""
