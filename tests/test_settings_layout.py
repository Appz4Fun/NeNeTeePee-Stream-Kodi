# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Structural assertions over the bundled settings.xml.

The settings.xml is hand-edited and Kodi has no schema validation —
silent breakage (a default flipped from ``true`` to garbage, a
required-creds block reordered behind an optional one) only surfaces
on a fresh install. These tests pin the load-bearing invariants:

    * Required defaults that the first-run UX depends on.
    * Playback backend fields stay together; search providers belong on
      Indexers, with NZBHydra2 first.
    * That ``*_enabled`` flags whose paired credentials default empty
      also default ``false`` (Fix #2 — opt-in after credentials).
    * That ``prowlarr_indexer_ids`` precedes its associated
      "Test Prowlarr" action (the action depends on the IDs).
"""

import os
import xml.etree.ElementTree as ET

import pytest

# pylint: disable=redefined-outer-name

_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "settings.xml",
)


@pytest.fixture(scope="module")
def settings_root():
    tree = ET.parse(_SETTINGS_PATH)
    return tree.getroot()


def _setting_by_id(category, setting_id):
    for child in category.iter("setting"):
        if child.get("id") == setting_id:
            return child
    return None


def _index_of_setting(category, predicate):
    """Return the position (within the category, across all its groups) of
    the first setting matching ``predicate``. -1 if absent. Used to assert
    relative ordering. ``category.iter("setting")`` walks groups in document
    order, so positions still reflect on-screen order."""
    for idx, child in enumerate(category.iter("setting")):
        if predicate(child):
            return idx
    return -1


def _categories(root):
    section = root.find("section")
    assert section is not None, "new-format settings.xml missing section"
    assert section.get("id") == "plugin.video.nzbdav"
    return section.findall("category")


def _category_by_label(root, label):
    for category in _categories(root):
        if category.get("label") == label:
            return category
    raise AssertionError("category label={} not found".format(label))


def _dependency_for(setting, dep_type, dep_setting):
    for dependency in setting.findall("./dependencies/dependency"):
        if (
            dependency.get("type") == dep_type
            and dependency.get("setting") == dep_setting
        ):
            return dependency
    return None


def test_settings_xml_uses_new_format_with_category_help(settings_root):
    """Kodi's old add-on settings format ignores category help; keep the
    Matrix/Omega settings format so each tab can show a help blurb."""
    assert settings_root.get("version") == "1"
    categories = _categories(settings_root)
    assert categories, "settings.xml has no categories"
    for category in categories:
        assert category.get("id"), "category missing stable id"
        assert category.get("help"), "category {} missing help id".format(
            category.get("label")
        )


# --- Fix #1: webdav_url default is no longer empty -------------------------


def test_webdav_url_default_is_localhost_8080(settings_root):
    """First install must have a discoverable webdav starting point."""
    cat = _category_by_label(settings_root, "30600")
    webdav_url = _setting_by_id(cat, "webdav_url")
    assert webdav_url is not None, "webdav_url setting missing"
    assert webdav_url.findtext("default") == "http://localhost:8080"


# --- Fix #2: nzbhydra_enabled defaults false (paired creds default empty) --


def test_nzbhydra_enabled_defaults_false(settings_root):
    """nzbhydra_enabled with empty hydra_api_key would always fail
    test_hydra on first launch — flipped to opt-in after creds."""
    cat = _category_by_label(settings_root, "30163")
    hydra = _setting_by_id(cat, "nzbhydra_enabled")
    assert hydra is not None
    assert hydra.findtext("default") == "false"
    # Paired credentials still default empty (the user has to enter them).
    api_key = _setting_by_id(cat, "hydra_api_key")
    assert api_key.findtext("default") == ""


def test_prowlarr_enabled_remains_false(settings_root):
    """Confirm the Fix #2 audit conclusion: prowlarr_enabled was already
    correct; this test pins it so a future edit doesn't regress it."""
    cat = _category_by_label(settings_root, "30163")
    prowlarr = _setting_by_id(cat, "prowlarr_enabled")
    assert prowlarr.findtext("default") == "false"


def test_backend_and_indexer_panels(settings_root):
    """Keep the backend credentials together and Hydra first among indexers."""
    categories = _categories(settings_root)
    assert [cat.get("id") for cat in categories[:2]] == ["backend", "indexers"]
    assert not {"connection", "nzbget"}.intersection(
        cat.get("id") for cat in categories
    )
    backend = _category_by_label(settings_root, "30600")
    indexers = _category_by_label(settings_root, "30163")
    for setting_id, value in (
        ("nzbdav_url", "0"),
        ("webdav_url", "0"),
        ("nzbget_url", "1"),
        ("nzbget_smb_root", "1"),
        ("streamnzb_url", "2"),
        ("streamnzb_token", "2"),
    ):
        setting = _setting_by_id(backend, setting_id)
        assert setting is not None
        assert _dependency_for(setting, "visible", "playback_backend").text == value
        assert _setting_by_id(indexers, setting_id) is None
    assert _index_of_setting(backend, lambda s: s.get("id") == "nzbdav_url") < (
        _index_of_setting(backend, lambda s: s.get("id") == "webdav_url")
    )
    assert next(indexers.iter("setting")).get("id") == "nzbhydra_enabled"
    for setting_id in ("hydra_url", "prowlarr_host", "direct_indexers_enabled"):
        assert _setting_by_id(backend, setting_id) is None
        assert _setting_by_id(indexers, setting_id) is not None


def test_prowlarr_indexer_ids_precedes_test_action(settings_root):
    """``prowlarr_indexer_ids`` is consumed by the Test Prowlarr action;
    it must appear BEFORE that action in the panel so users finish
    selecting indexers before validating the connection."""
    cat = _category_by_label(settings_root, "30163")
    settings_list = list(cat.iter("setting"))

    indexer_ids_idx = next(
        (
            i
            for i, s in enumerate(settings_list)
            if s.get("id") == "prowlarr_indexer_ids"
        ),
        -1,
    )
    test_action_idx = next(
        (
            i
            for i, s in enumerate(settings_list)
            if s.findtext("data", "").endswith("test_prowlarr)")
        ),
        -1,
    )
    assert indexer_ids_idx >= 0
    assert test_action_idx >= 0
    assert indexer_ids_idx < test_action_idx


def test_direct_indexer_options_depend_on_master_toggle(settings_root):
    """All direct-indexer options must be tied to the in-dialog master toggle
    via an `enable` dependency, not `visible`.

    Kodi's CGUIDialogSettingsBase only creates GUI controls for a group at
    dialog-build time if the group contains at least one currently-visible
    setting (CSettingCategory::GetGroups -> ContainsVisibleSettings). The
    "Popular Indexers" / "Custom Newznab Indexers" groups have no setting
    other than these dependents, so when direct_indexers_enabled is off,
    those groups are entirely hidden and get NO controls built at all —
    meaning there is nothing for the live dependency-update mechanism to
    later reveal when the toggle flips (confirmed live on a Kodi 21.3-Omega
    device: toggling stayed stuck until switching category tabs away and
    back forced a rebuild). `enable` sidesteps this because it never hides
    the setting from ContainsVisibleSettings — the row always renders, just
    greyed out — and enabled/disabled state DOES update live through the
    same UpdateSettingControl path regardless of group composition.
    """
    cat = _category_by_label(settings_root, "30163")
    direct_groups = [
        group
        for group in cat.findall("group")
        if group.get("label") in {"30166", "30167"}
    ]
    assert len(direct_groups) == 2

    for setting in (s for group in direct_groups for s in group.iter("setting")):
        assert (
            _dependency_for(setting, "enable", "direct_indexers_enabled") is not None
        ), "setting {} is not tied to direct_indexers_enabled via enable=".format(
            setting.get("id") or setting.get("label")
        )


# --- Sanity: every setting has a unique id (when id is present) -----------


def _setting_anywhere(root, setting_id):
    for setting in root.iter("setting"):
        if setting.get("id") == setting_id:
            return setting
    return None


def test_readahead_buffer_mb_setting_present(settings_root):
    """The read-ahead prefetch cache is gated by readahead_buffer_mb; pin its
    layout (type=integer, default=256) like the sibling tuning settings."""
    setting = _setting_anywhere(settings_root, "readahead_buffer_mb")
    assert setting is not None, "readahead_buffer_mb setting missing"
    assert setting.get("type") == "integer"
    assert setting.findtext("default") == "256"
    assert setting.get("label") == "30207"


def test_passthrough_stall_wait_setting_present(settings_root):
    """Pin the passthrough_stall_wait layout (was previously unasserted)."""
    setting = _setting_anywhere(settings_root, "passthrough_stall_wait")
    assert setting is not None
    assert setting.get("type") == "integer"
    assert setting.findtext("default") == "120"


def test_no_duplicate_setting_ids(settings_root):
    """Settings with an id attribute should be unique across the file —
    Kodi keys by id, and a dup silently shadows."""
    seen = []
    for setting in settings_root.iter("setting"):
        sid = setting.get("id")
        if sid:
            seen.append(sid)
    duplicates = {s for s in seen if seen.count(s) > 1}
    assert not duplicates, "duplicate setting ids: {}".format(sorted(duplicates))
