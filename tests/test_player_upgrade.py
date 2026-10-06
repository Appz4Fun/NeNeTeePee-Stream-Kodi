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


@pytest.mark.parametrize("schema", [None, 8, 9])
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
    assert updated["schema_version"] == 10
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
        _old_player(10),
        _old_player(11),
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
    assert reader.meta["schema_version"] == 10
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
