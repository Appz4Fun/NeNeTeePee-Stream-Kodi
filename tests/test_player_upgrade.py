# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Automatic existing-player upgrades are silent, atomic and customization-safe."""

import json
from unittest.mock import patch

import pytest
from resources.lib import player_installer


@pytest.fixture(name="installed_player")
def _installed_player(tmp_path, monkeypatch):
    root = tmp_path / "addon_data"
    folder = root / "plugin.video.themoviedb.helper" / "players"
    folder.mkdir(parents=True)
    monkeypatch.setattr(
        player_installer.xbmcvfs,
        "translatePath",
        lambda path: (
            str(root) if path == player_installer.ADDON_DATA_ROOT else str(folder)
        ),
    )
    return folder / "nzbdav.json"


def _old_player(schema=9):
    return {
        "plugin": "plugin.video.nzbdav",
        "schema_version": schema,
        "priority": 42,
        "name": "My player",
        "custom": {"keep": True},
        "play_movie": "old movie",
        "play_episode": "old episode",
        "is_resolvable": "false",
    }


@pytest.mark.parametrize("schema", [None, 8, 9, 10])
def test_automatic_upgrade_preserves_custom_fields_and_original_backup(
    installed_player, schema
):
    original = _old_player(schema)
    if schema is None:
        original.pop("schema_version")
    installed_player.write_text(json.dumps(original))
    with patch.object(player_installer, "_notify") as notify, patch.object(
        player_installer.xbmcaddon, "Addon"
    ) as addon:
        assert player_installer.upgrade_installed_tmdbhelper_player() == "updated"
        assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
        notify.assert_not_called()
        addon.assert_not_called()
    updated = json.loads(installed_player.read_text())
    assert updated["schema_version"] == 11
    for field in ("name", "priority", "custom"):
        assert updated[field] == original[field]
    for field in ("play_movie", "play_episode", "is_resolvable"):
        assert updated[field] == player_installer.PLAYER_JSON[field]
    backups = list(installed_player.parent.glob("nzbdav.*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == original


@pytest.mark.parametrize(
    "value",
    [
        _old_player(11),
        _old_player(12),
        dict(_old_player(), plugin="foreign"),
        [],
        "invalid",
    ],
)
def test_automatic_upgrade_leaves_current_foreign_or_invalid_files(
    installed_player, value
):
    original = "{" if value == "invalid" else json.dumps(value)
    installed_player.write_text(original)
    assert player_installer.upgrade_installed_tmdbhelper_player() in (
        "unchanged",
        "failed",
    )
    assert installed_player.read_text() == original
    assert not list(installed_player.parent.glob("*.bak"))


def test_automatic_upgrade_does_not_create_an_uninstalled_player(installed_player):
    assert player_installer.upgrade_installed_tmdbhelper_player() == "absent"
    assert not installed_player.exists()


@pytest.mark.parametrize("failure", ["backup", "stage", "replace"])
def test_automatic_upgrade_failure_keeps_original_bytes(installed_player, failure):
    from resources.lib import player_upgrade

    original = json.dumps(_old_player())
    installed_player.write_text(original)
    target = {
        "backup": "shutil.copy2",
        "stage": "tempfile.mkstemp",
        "replace": "os.replace",
    }[failure]
    with patch(
        "resources.lib.player_upgrade." + target, side_effect=OSError("write failure")
    ):
        assert player_installer.upgrade_installed_tmdbhelper_player() == "failed"
    assert installed_player.read_text() == original
    assert player_installer.upgrade_installed_tmdbhelper_player() == "updated"
    assert player_upgrade is not None


def test_automatic_upgrade_defers_while_another_process_holds_lock(installed_player):
    import fcntl

    installed_player.write_text(json.dumps(_old_player()))
    with open(installed_player.with_suffix(".upgrade.lock"), "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert player_installer.upgrade_installed_tmdbhelper_player() == "deferred"
    assert player_installer.upgrade_installed_tmdbhelper_player() == "updated"


def test_service_auto_upgrade_runs_before_proxy_failure(monkeypatch):
    import service

    events = []
    monkeypatch.setattr(
        player_installer,
        "upgrade_installed_tmdbhelper_player",
        lambda: events.append("upgrade"),
    )
    monkeypatch.setattr(service, "_clear_stale_ipc_properties", lambda: None)
    monkeypatch.setattr(
        service, "_start_proxy", lambda _monitor: events.append("proxy") or None
    )
    service.main()
    assert events == ["upgrade", "proxy"]


def test_fresh_addon_entry_upgrades_before_routing(monkeypatch):
    import runpy
    import sys
    from pathlib import Path

    from resources.lib import playback_backend, router

    events = []
    monkeypatch.setattr(
        player_installer,
        "upgrade_installed_tmdbhelper_player",
        lambda: events.append("upgrade"),
    )
    monkeypatch.setattr(playback_backend, "migrate_backend", lambda: None)
    monkeypatch.setattr(router, "route", lambda _args: events.append("route"))
    monkeypatch.setattr(sys, "argv", ["addon.py", "1", ""])
    runpy.run_path(str(Path(__file__).parents[1] / "repo/plugin.video.nzbdav/addon.py"))
    assert events == ["upgrade", "route"]


def test_partial_staged_json_write_keeps_original_and_removes_stage(installed_player):
    original = json.dumps(_old_player())
    installed_player.write_text(original)

    def partial_write(_data, stream, **_kwargs):
        stream.write("{")
        raise OSError("Disk full")

    with patch("resources.lib.player_upgrade.json.dump", side_effect=partial_write):
        assert player_installer.upgrade_installed_tmdbhelper_player() == "failed"
    assert installed_player.read_text() == original
    assert not list(installed_player.parent.glob(".nzbdav-player-*"))


def test_auto_upgrade_refuses_player_symlink(installed_player, tmp_path):
    target = tmp_path / "other.json"
    original = json.dumps(_old_player())
    target.write_text(original)
    installed_player.symlink_to(target)
    assert player_installer.upgrade_installed_tmdbhelper_player() == "failed"
    assert target.read_text() == original


def test_upgrade_error_does_not_escape_public_startup_hook(installed_player):
    with patch(
        "resources.lib.player_upgrade.upgrade_player",
        side_effect=RuntimeError("VFS failure"),
    ):
        assert player_installer.upgrade_installed_tmdbhelper_player() == "failed"


def test_migration_artifacts_are_not_tmdbhelper_player_candidates(installed_player):
    import ast
    from functools import cached_property
    from pathlib import Path
    from types import SimpleNamespace

    fixture = Path(__file__).parent / "fixtures/tmdbhelper_6_17_5_contract.txt"
    source = ast.parse(fixture.read_text())
    selected = [
        node
        for node in source.body
        if getattr(node, "name", "") in ("get_files_in_folder", "PlayerMeta")
    ]
    namespace = {
        "cached_property": cached_property,
        "loads": json.loads,
        "read_file": lambda filename: Path(filename).read_text(),
        "xbmcvfs": SimpleNamespace(
            listdir=lambda _folder: (
                [],
                [p.name for p in installed_player.parent.iterdir()],
            )
        ),
    }
    exec(  # pylint: disable=exec-used
        compile(ast.Module(body=selected, type_ignores=[]), str(fixture), "exec"),
        namespace,
    )
    installed_player.write_text(json.dumps(_old_player()))
    assert player_installer.upgrade_installed_tmdbhelper_player() == "updated"
    # Exercise the installed unanchored scan and JSON reader together.
    candidates = namespace["get_files_in_folder"](
        str(installed_player.parent), r".*\.json"
    )
    assert candidates == ["nzbdav.json"]
    reader = namespace["PlayerMeta"](str(installed_player.parent) + "/", candidates[0])
    assert reader.meta["schema_version"] == 11
    assert reader.meta["plugin"] == "plugin.video.nzbdav"


@pytest.mark.parametrize("failure", ["stage", "replace"])
def test_repeated_failed_upgrade_does_not_accumulate_backups(installed_player, failure):
    original = json.dumps(_old_player())
    installed_player.write_text(original)
    target = {"stage": "tempfile.mkstemp", "replace": "os.replace"}[failure]
    with patch(
        "resources.lib.player_upgrade." + target,
        side_effect=OSError("Persistent failure"),
    ):
        for _ in range(4):
            assert player_installer.upgrade_installed_tmdbhelper_player() == "failed"
    assert installed_player.read_text() == original
    assert not list(installed_player.parent.glob("*.bak"))
    assert not list(installed_player.parent.glob(".nzbdav-player-*"))
    assert player_installer.upgrade_installed_tmdbhelper_player() == "updated"
    assert len(list(installed_player.parent.glob("*.bak"))) == 1


# --- A fork of TMDBHelper installed beside it -------------------------------

BINGIE_HELPER_ID = "plugin.video.tmdb.bingie.helper"


@pytest.fixture(name="helpers")
def _helpers(tmp_path, monkeypatch):
    """Real folders for the profile and add-ons; returns (data, addons)."""
    home = tmp_path / "home"
    data = home / "userdata" / "addon_data"
    data.mkdir(parents=True)
    addons = home / "addons"
    addons.mkdir()
    monkeypatch.setattr(
        player_installer.xbmcvfs,
        "translatePath",
        lambda path: path.replace("special://profile", str(home / "userdata")).replace(
            "special://home", str(home)
        ),
    )
    return data, addons


def _install_addon(addons, addon_id):
    (addons / addon_id).mkdir()
    (addons / addon_id / "addon.xml").write_text("<addon/>")


def _player_file(data, addon_id):
    return data / addon_id / "players" / "nzbdav.json"


def _write_player(data, addon_id, player):
    path = _player_file(data, addon_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(player))
    return path


def test_installed_fork_without_the_player_gets_a_copy(helpers):
    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _write_player(data, "plugin.video.themoviedb.helper", _old_player(11))
    with patch.object(player_installer, "_notify") as notify, patch.object(
        player_installer.xbmcaddon, "Addon"
    ) as addon:
        assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
        notify.assert_not_called()
        addon.assert_not_called()
    copied = json.loads(_player_file(data, BINGIE_HELPER_ID).read_text())
    assert copied == player_installer.PLAYER_JSON
    assert not list(_player_file(data, BINGIE_HELPER_ID).parent.glob(".nzbdav-*"))


def test_player_installed_only_in_a_fork_is_copied_to_tmdbhelper(helpers):
    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _install_addon(addons, "plugin.video.themoviedb.helper")
    _write_player(data, BINGIE_HELPER_ID, _old_player(11))
    assert player_installer.upgrade_installed_tmdbhelper_player() == "absent"
    main = _player_file(data, "plugin.video.themoviedb.helper")
    assert json.loads(main.read_text()) == player_installer.PLAYER_JSON


def test_no_copy_into_a_fork_that_is_not_installed(helpers):
    data, _addons = helpers
    _write_player(data, "plugin.video.themoviedb.helper", _old_player(11))
    assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
    assert not (data / BINGIE_HELPER_ID).exists()


def test_no_copy_when_the_player_was_never_installed(helpers):
    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _install_addon(addons, "plugin.video.themoviedb.helper")
    assert player_installer.upgrade_installed_tmdbhelper_player() == "absent"
    assert not (data / BINGIE_HELPER_ID).exists()
    assert not (data / "plugin.video.themoviedb.helper").exists()


def test_no_copy_when_the_only_player_file_belongs_to_another_addon(helpers):
    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _write_player(
        data, "plugin.video.themoviedb.helper", dict(_old_player(), plugin="foreign")
    )
    assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
    assert not (data / BINGIE_HELPER_ID).exists()


def test_fork_player_is_upgraded_and_keeps_custom_fields(helpers):
    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _write_player(data, "plugin.video.themoviedb.helper", _old_player(11))
    fork = _write_player(data, BINGIE_HELPER_ID, _old_player(9))
    assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
    updated = json.loads(fork.read_text())
    assert updated["schema_version"] == 11
    assert updated["name"] == "My player"
    assert updated["play_episode"] == player_installer.PLAYER_JSON["play_episode"]


def test_copy_never_replaces_a_file_already_in_the_fork(helpers):
    from resources.lib import player_upgrade

    data, _addons = helpers
    fork = _write_player(data, BINGIE_HELPER_ID, {"plugin": "foreign"})
    original = fork.read_text()
    assert (
        player_upgrade.add_player(str(fork.parent), player_installer.PLAYER_JSON)
        == "unchanged"
    )
    assert fork.read_text() == original


def test_copy_failure_leaves_no_partial_player(helpers):
    from resources.lib import player_upgrade

    data, addons = helpers
    _install_addon(addons, BINGIE_HELPER_ID)
    _write_player(data, "plugin.video.themoviedb.helper", _old_player(11))
    with patch(
        "resources.lib.player_upgrade.os.replace", side_effect=OSError("disk full")
    ):
        assert player_installer.upgrade_installed_tmdbhelper_player() == "unchanged"
    folder = _player_file(data, BINGIE_HELPER_ID).parent
    assert not _player_file(data, BINGIE_HELPER_ID).exists()
    assert not list(folder.glob(".nzbdav-*"))
    assert player_upgrade.add_player(str(folder), player_installer.PLAYER_JSON) == (
        "added"
    )


def test_copy_defers_while_another_process_holds_the_lock(helpers):
    import fcntl

    from resources.lib import player_upgrade

    data, _addons = helpers
    folder = _player_file(data, BINGIE_HELPER_ID).parent
    folder.mkdir(parents=True)
    with open(folder / "nzbdav.upgrade.lock", "a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert (
            player_upgrade.add_player(str(folder), player_installer.PLAYER_JSON)
            == "deferred"
        )
    assert not (folder / "nzbdav.json").exists()
