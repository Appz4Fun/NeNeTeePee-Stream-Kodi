# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Tests for building the installable Kodi addon zip."""

import importlib.util
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_build_zip_module():
    script_path = REPO_ROOT / "scripts" / "build_zip.py"
    spec = importlib.util.spec_from_file_location("build_zip_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_zip_keeps_addon_id_as_archive_root(tmp_path, monkeypatch):
    module = _load_build_zip_module()
    monkeypatch.chdir(REPO_ROOT)

    zip_path = module.build_zip(output_dir=str(tmp_path))

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())

    assert "plugin.video.nzbdav/addon.xml" in names
    assert "plugin.video.nzbdav/addon.py" in names
    assert "repo/plugin.video.nzbdav/addon.xml" not in names


def test_real_package_contains_scrobbling_helper_and_matching_boundary_sources(
    tmp_path, monkeypatch
):
    import json

    from resources.lib.player_installer import PLAYER_JSON

    module = _load_build_zip_module()
    monkeypatch.chdir(REPO_ROOT)
    zip_path = module.build_zip(output_dir=str(tmp_path))
    with zipfile.ZipFile(zip_path) as archive:
        addon = REPO_ROOT / "repo/plugin.video.nzbdav"
        for path in (
            "resources/lib/playback_context.py",
            "resources/lib/playback_handoff.py",
            "resources/lib/player_upgrade.py",
            "resources/lib/nzbget_resolver.py",
            "resources/lib/streamnzb_player.py",
            "resources/lib/resolver_resume.py",
            "addon.py",
            "service.py",
        ):
            assert (
                archive.read("plugin.video.nzbdav/" + path)
                == (addon / path).read_bytes()
            )
        assert (
            json.loads(
                archive.read("plugin.video.nzbdav/resources/players/nzbdav.json")
            )
            == PLAYER_JSON
        )
        assert PLAYER_JSON["schema_version"] == 10
        assert not any(
            "__pycache__" in name or name.endswith(".pyc")
            for name in archive.namelist()
        )
