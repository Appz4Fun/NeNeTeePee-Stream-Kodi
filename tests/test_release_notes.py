# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Tests for scripts/release_notes.py (GitHub release body from CHANGELOG.md)."""

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes  # noqa: E402  pylint: disable=wrong-import-position

SAMPLE = """# Changelog

## [Unreleased][] — main

## [2.0.0-beta.3][] — 2026-10-11

> Intro.

### Added

- Thing (#1)

## [2.0.0-beta.2][] — 2026-07-18

- Older

[Unreleased]: https://example.invalid/compare/v2.0.0-beta.3...main
[2.0.0-beta.3]: https://example.invalid/compare/v2.0.0-beta.2...v2.0.0-beta.3
"""


def _run(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_notes.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_section_returns_body_between_headings():
    body = release_notes.section(SAMPLE, "2.0.0-beta.3")
    assert body.startswith("> Intro.")
    assert "- Thing (#1)" in body
    assert "2.0.0-beta.2" not in body


def test_last_section_stops_at_link_references():
    assert release_notes.section(SAMPLE, "2.0.0-beta.2") == "- Older"


def test_missing_version_raises():
    with pytest.raises(LookupError):
        release_notes.section(SAMPLE, "9.9.9")


def test_empty_section_raises():
    with pytest.raises(LookupError):
        release_notes.section(SAMPLE, "Unreleased")


def test_version_with_v_prefix_is_accepted():
    assert release_notes.section(SAMPLE, "v2.0.0-beta.3").startswith("> Intro.")


def test_version_dots_are_literal():
    """``2.0.0`` must not match a ``2x0y0`` heading: dots are not wildcards."""
    with pytest.raises(LookupError):
        release_notes.section("## [2x0y0][] — d\n\n- x\n", "2.0.0")


def test_cli_prints_the_repo_changelog_section_for_the_addon_version():
    version = (
        ET.parse(ROOT / "repo" / "plugin.video.nzbdav" / "addon.xml")
        .getroot()
        .get("version")
    )
    out = _run(version)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip()


def test_cli_missing_version_exits_nonzero(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(SAMPLE, encoding="utf-8")
    out = _run("9.9.9", "--changelog", str(path))
    assert out.returncode == 1
    assert "9.9.9" in out.stderr
    assert out.stdout == ""
