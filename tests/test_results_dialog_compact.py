# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""List compacted: one row per release, docked indexer panel, view setting."""

import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from resources.lib.filter import parse_title_metadata
from resources.lib.results_dialog import (
    ACTION_CONTEXT_MENU,
    ACTION_SELECT,
    COMPACT_LIST_ID,
    LIST_ID,
    VIEW_COMPACT,
    VIEW_LIST,
    ResultsDialog,
    _build_group_item,
    _results_view,
    _wrap_release_name,
    show_results_dialog,
)

ALPHA = "Alpha.2024.1080p.WEB-DL.DDP5.1.H264-GRPA"
BRAVO = "Bravo.2024.1080p.WEB-DL.DDP5.1.H264-GRPB"

_DIALOG_XML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "skins",
    "Default",
    "1080i",
    "results-dialog.xml",
)


class _Action:  # pylint: disable=too-few-public-methods
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


def _row(title, link, indexer="idx", reject=None, **extra):
    row = {
        "title": title,
        "link": link,
        "size": "5000000000",
        "pubdate": "",
        "age": "3 days",
        "indexer": indexer,
        "_filter_reject": reject,
    }
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _dialog(results, all_results=None, view=VIEW_COMPACT):
    all_results = all_results if all_results is not None else list(results)
    return ResultsDialog(
        "results-dialog.xml",
        "",
        "Default",
        "1080i",
        results=results,
        title="Movie",
        year="2024",
        total_count=len(all_results),
        all_results=all_results,
        view=view,
    )


def _props(item):
    return {call.args[0]: call.args[1] for call in item.setProperty.call_args_list}


# ---------------------------------------------------------------------------
# Dialog behavior
# ---------------------------------------------------------------------------


def test_compact_view_shows_one_row_per_release_in_list_51():
    rows = [_row(ALPHA, "a1"), _row(ALPHA, "a2"), _row(BRAVO, "b1")]
    dialog = _dialog(rows)
    dialog.onInit()

    items = dialog.getControl(COMPACT_LIST_ID).addItems.call_args.args[0]
    assert len(items) == 2
    assert dialog.getFocusId() == COMPACT_LIST_ID
    assert dialog.getProperty("view") == VIEW_COMPACT
    assert dialog.getProperty("count") == "2 releases · 3 sources"
    assert dialog.getProperty("filter_info") == (
        "Showing 2 releases (3 of 3 sources) after filters"
    )
    dialog.getControl(LIST_ID).addItems.assert_not_called()


def test_compact_selection_returns_the_groups_lead_copy():
    lead = _row(ALPHA, "a1")
    rows = [lead, _row(ALPHA, "a2"), _row(BRAVO, "b1")]
    dialog = _dialog(rows)
    dialog.onInit()

    dialog.getControl(COMPACT_LIST_ID).getSelectedPosition.return_value = 0
    dialog.onAction(_Action(ACTION_SELECT))

    assert dialog.get_selected_result() is lead
    assert dialog._closed is True


def test_compact_selection_plays_the_downloaded_copy_when_only_it_is_dl():
    lead = _row(ALPHA, "a1")
    done = _row(ALPHA, "a2", _available=True)
    dialog = _dialog([lead, done])
    dialog.onInit()

    dialog.getControl(COMPACT_LIST_ID).getSelectedPosition.return_value = 0
    dialog.onAction(_Action(ACTION_SELECT))

    assert dialog.get_selected_result() is done


def test_compact_click_on_second_group_returns_its_lead():
    bravo = _row(BRAVO, "b1")
    dialog = _dialog([_row(ALPHA, "a1"), _row(ALPHA, "a2"), bravo])
    dialog.onInit()

    dialog.getControl(COMPACT_LIST_ID).getSelectedPosition.return_value = 1
    dialog.onClick(COMPACT_LIST_ID)

    assert dialog.get_selected_result() is bravo


def test_compact_ignores_clicks_from_the_hidden_list():
    dialog = _dialog([_row(ALPHA, "a1")])
    dialog.onInit()

    dialog.onClick(LIST_ID)

    assert dialog._closed is False


def test_compact_toggle_regroups_and_keeps_focus_on_the_same_release():
    alpha = _row(ALPHA, "a1")
    bravo = _row(BRAVO, "b1")
    hidden = _row("Hidden.2024.1080p.WEB-DL.H264-HID", "h1", reject="codec")
    dialog = _dialog([alpha, bravo], [hidden, alpha, bravo])
    dialog.onInit()

    control = dialog.getControl(COMPACT_LIST_ID)
    control.getSelectedPosition.return_value = 1  # Bravo
    dialog.onAction(_Action(ACTION_CONTEXT_MENU))

    assert len(control.addItems.call_args.args[0]) == 3
    control.selectItem.assert_called_with(2)
    assert dialog.getProperty("filter_info") == (
        "Showing all 3 sources in 3 releases (filters off)"
    )


def test_list_view_is_unchanged_and_uses_list_50():
    rows = [_row(ALPHA, "a1"), _row(ALPHA, "a2")]
    dialog = _dialog(rows, view=VIEW_LIST)
    dialog.onInit()

    assert len(dialog.getControl(LIST_ID).addItems.call_args.args[0]) == 2
    assert dialog.getFocusId() == LIST_ID
    assert dialog.getProperty("view") == VIEW_LIST
    assert dialog.getProperty("count") == "2 sources found"


# ---------------------------------------------------------------------------
# Row and panel properties
# ---------------------------------------------------------------------------


def _group_props(group):
    item = MagicMock()
    with patch("resources.lib.results_dialog.xbmcgui.ListItem", return_value=item):
        _build_group_item(group, 0)
    return _props(item)


def test_group_row_shows_copy_count_and_panel_lists_every_indexer_once():
    group = [
        _row(ALPHA, "a1", indexer="SceneNZBs"),
        _row(ALPHA, "a2", indexer="NZBGeek"),
        _row(ALPHA, "a3", indexer="NZBGeek"),
    ]
    props = _group_props(group)

    assert "3 copies" in props["copies"]
    assert props["panel_kicker"] == "3 copies on 2 indexers"
    indexers = props["panel_indexers"].split("[CR]")
    assert len(indexers) == 2
    assert "SceneNZBs" in indexers[0] and "FFFFFFFF" in indexers[0]
    assert "NZBGeek x2" in indexers[1] and "FFFFFFFF" not in indexers[1]
    assert len(props["panel_sizes"].split("[CR]")) == 2
    assert props["panel_foot"] == "Enter plays the top copy."


def test_panel_shows_every_indexer_when_one_lists_the_release_many_times():
    # Three NZB Finder copies used to fill three lines and push the other
    # indexers into "+N more".
    names = ["NZB Finder"] * 3 + ["nzb.life"] * 2
    names += ["Indexer {}".format(n) for n in range(1, 12)]
    group = [_row(ALPHA, "a{}".format(i), indexer=name) for i, name in enumerate(names)]
    props = _group_props(group)

    assert props["panel_kicker"] == "16 copies on 13 indexers"
    indexers = props["panel_indexers"].split("[CR]")
    assert len(indexers) == 13
    assert "NZB Finder x3" in indexers[0]
    assert "nzb.life x2" in indexers[1]
    assert "Indexer 11" in indexers[-1]
    assert not any("more" in line for line in indexers)


def test_an_indexer_line_shows_the_copy_enter_plays():
    group = [
        _row(ALPHA, "a1", indexer="Geek", size=str(41 * 1024**3), age="2d"),
        _row(ALPHA, "a2", indexer="Slug", size=str(40 * 1024**3), age="3d"),
        _row(
            ALPHA,
            "a3",
            indexer="Slug",
            size=str(39 * 1024**3),
            age="9d",
            _available=True,
        ),
    ]
    props = _group_props(group)

    indexers = props["panel_indexers"].split("[CR]")
    sizes = props["panel_sizes"].split("[CR]")
    ages = props["panel_ages"].split("[CR]")
    marks = props["panel_marks"].split("[CR]")
    # Slug holds the downloaded copy: highlighted, with that copy's size and age.
    assert "Slug x2" in indexers[1] and "FFFFFFFF" in indexers[1]
    assert "FFFFFFFF" not in indexers[0]
    assert "39" in sizes[1] and "9d" in ages[1]
    assert "DL" in marks[1] and "DL" not in marks[0]


def test_single_copy_row_shows_its_indexer_instead_of_a_count():
    props = _group_props([_row(ALPHA, "a1", indexer="altHUB")])

    assert "altHUB" in props["copies"]
    assert props["panel_kicker"] == "1 copy on altHUB"


def test_dl_on_any_copy_marks_the_group_row_and_that_panel_line():
    group = [
        _row(ALPHA, "a1", indexer="Geek"),
        _row(ALPHA, "a2", indexer="Slug", _available=True),
    ]
    props = _group_props(group)

    assert props["available"] == "[COLOR FF22C55E]DL[/COLOR]"
    marks = props["panel_marks"].split("[CR]")
    assert "DL" not in marks[0] and "DL" in marks[1]
    indexers = props["panel_indexers"].split("[CR]")
    assert "FFFFFFFF" not in indexers[0] and "FFFFFFFF" in indexers[1]
    assert props["panel_foot"].startswith("Already downloaded")


GB = 1024**3


def test_an_indexer_with_copies_of_different_size_and_age_shows_ranges():
    group = [
        _row(ALPHA, "a1", indexer="Geek", size=str(42 * GB), age="today"),
        _row(ALPHA, "a2", indexer="NZB Finder", size=str(40 * GB), age="2 months"),
        _row(ALPHA, "a3", indexer="NZB Finder", size=str(38 * GB), age="20 days"),
        _row(ALPHA, "a4", indexer="NZB Finder", size=str(42 * GB), age="1 month"),
        _row(ALPHA, "a5", indexer="Slug", size=str(40 * GB), age="3 days"),
        _row(ALPHA, "a6", indexer="Slug", size=str(40 * GB + 1), age="3 days"),
    ]
    props = _group_props(group)

    indexers = props["panel_indexers"].split("[CR]")
    sizes = props["panel_sizes"].split("[CR]")
    ages = props["panel_ages"].split("[CR]")
    assert "NZB Finder x3" in indexers[1]
    assert "38-42 GB" in sizes[1]
    assert "20d-2mo" in ages[1]
    # One copy, or copies that read the same, keep the plain size and age.
    assert "42.0 GB" in sizes[0] and "today" in ages[0]
    assert "40.0 GB" in sizes[2] and "3 days" in ages[2]


def test_ranges_cover_small_sizes_and_young_copies():
    from resources.lib.results_dialog import _age_range, _size_range

    mb = 1024**2
    small = [{"size": str(300 * mb)}, {"size": str(700 * mb)}]
    assert _size_range(small, small[0]) == "300-700 MB"
    mixed = [{"size": str(900 * mb)}, {"size": str(3 * GB)}]
    assert _size_range(mixed, mixed[0]) == "900-3072 MB"
    unsized = [{"size": ""}, {"size": "junk"}]
    assert _size_range(unsized, unsized[0]) == ""
    young = [{"age": "today"}, {"age": "1 day"}, {"age": "5 days"}]
    assert _age_range(young, young[0]) == "0d-5d"
    old = [{"age": "11 months"}, {"age": "2 years"}]
    assert _age_range(old, old[0]) == "11mo-2y"
    unknown = [{"age": "3 days"}, {"age": ""}, {"age": "soon"}]
    assert _age_range(unknown, unknown[0]) == "3 days"


def test_a_long_indexer_name_is_shortened_so_the_panel_rows_stay_aligned():
    from resources.lib.results_dialog import _panel_indexer_name

    assert _panel_indexer_name("NZB Finder", 3) == "NZB Finder x3"
    assert _panel_indexer_name("SceneNZBs (TreasureMaps)", 1) == (
        "SceneNZBs (Treasure\u2026"
    )
    long_name = _panel_indexer_name("scenenzbs.org-free-custom", 12)
    assert long_name.endswith(" x12") and len(long_name) <= 20
    assert _panel_indexer_name("", 1) == " "


def test_panel_caps_many_indexers_with_a_more_line():
    group = [
        _row(ALPHA, "a{}".format(i), indexer="Indexer {:02d}".format(i))
        for i in range(20)
    ]
    props = _group_props(group)

    indexers = props["panel_indexers"].split("[CR]")
    assert len(indexers) == 14
    assert "+7 more" in indexers[-1]


def test_capped_panel_still_lists_the_indexer_of_the_copy_enter_plays():
    group = [
        _row(ALPHA, "a{}".format(i), indexer="Indexer {:02d}".format(i))
        for i in range(19)
    ]
    group.append(_row(ALPHA, "done", indexer="DLIDX", _available=True))
    props = _group_props(group)

    indexers = props["panel_indexers"].split("[CR]")
    assert len(indexers) == 14
    assert "DLIDX" in indexers[-2] and "FFFFFFFF" in indexers[-2]


def test_group_row_carries_the_leads_filter_reason():
    props = _group_props([_row(ALPHA, "a1", reject="codec")])

    assert "FILTERED: codec" in props["filter_reason"]


def test_release_name_breaks_after_dots_and_keeps_every_character():
    name = "Metropolis.1927.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-EPSiLON"
    wrapped = _wrap_release_name(name, width=24)

    lines = wrapped.split("[CR]")
    assert "".join(lines) == name
    assert all(len(line) <= 24 for line in lines)
    assert len(lines) > 1


def test_release_name_keeps_an_overlong_piece_whole():
    assert _wrap_release_name("x" * 60, width=10) == "x" * 60


# ---------------------------------------------------------------------------
# results_view setting
# ---------------------------------------------------------------------------


def _addon(value):
    addon = MagicMock()
    addon.getSetting.return_value = value
    return addon


def test_results_view_defaults_to_compact():
    assert _results_view(_addon("")) == VIEW_COMPACT
    assert _results_view(_addon("0")) == VIEW_COMPACT
    assert _results_view(_addon("junk")) == VIEW_COMPACT


def test_results_view_list_option():
    assert _results_view(_addon("1")) == VIEW_LIST


def test_show_results_dialog_passes_the_view_setting():
    with patch("resources.lib.results_dialog.xbmcaddon.Addon") as addon_cls, patch(
        "resources.lib.results_dialog.ResultsDialog"
    ) as dialog_cls:
        addon_cls.return_value.getSetting.return_value = "1"
        dialog_cls.return_value.get_selected_result.return_value = None
        show_results_dialog([_row(ALPHA, "a1")])

    assert dialog_cls.call_args.kwargs["view"] == VIEW_LIST


def test_settings_xml_defines_results_view_with_compact_default():
    settings = os.path.join(
        os.path.dirname(_DIALOG_XML_PATH), "..", "..", "..", "settings.xml"
    )
    root = ET.parse(os.path.normpath(settings)).getroot()
    setting = root.find(".//category[@id='sorting']//setting[@id='results_view']")

    assert setting is not None
    assert setting.findtext("default") == "0"
    labels = [o.get("label") for o in setting.iter("option")]
    assert labels == ["30492", "30493"]


# ---------------------------------------------------------------------------
# Skin structure
# ---------------------------------------------------------------------------


def _top_controls():
    return ET.parse(_DIALOG_XML_PATH).getroot().findall("./controls/control")


def _find(control_type, control_id):
    for control in _top_controls():
        if control.get("type") == control_type and control.get("id") == control_id:
            return control
    raise AssertionError("missing {} {}".format(control_type, control_id))


def test_skin_shows_exactly_one_list_per_view():
    compact = "String.IsEqual(Window.Property(view),compact)"

    assert _find("list", "50").findtext("visible") == "!" + compact
    assert _find("scrollbar", "60").findtext("visible") == "!" + compact
    assert _find("list", "51").findtext("visible") == compact
    assert _find("scrollbar", "61").findtext("visible") == compact
    assert _find("list", "51").findtext("pagecontrol") == "61"


def test_compact_rows_keep_dl_copies_and_focus_bar():
    compact_list = _find("list", "51")
    for tag in ("itemlayout", "focusedlayout"):
        layout = compact_list.find(tag)
        labels = [c.findtext("label") for c in layout.findall("control")]
        assert "$INFO[ListItem.Property(available)]" in labels
        assert "$INFO[ListItem.Property(copies)]" in labels
        assert "$INFO[ListItem.Property(filter_reason)]" in labels
    focused = compact_list.find("focusedlayout")
    bars = [
        c
        for c in focused.findall("./control[@type='image']")
        if c.findtext("colordiffuse") == "FF4A9EFF" and c.findtext("width") == "4"
    ]
    assert len(bars) == 1


def test_panel_reads_the_focused_compact_item_and_is_not_focusable():
    panel = [
        c
        for c in _top_controls()
        if c.get("type") == "group"
        and c.findtext("visible") == "String.IsEqual(Window.Property(view),compact)"
    ]
    assert len(panel) == 1
    labels = [c.findtext("label") or "" for c in panel[0].iter("control")]
    for prop in (
        "panel_kicker",
        "panel_title",
        "panel_tech",
        "panel_marks",
        "panel_indexers",
        "panel_sizes",
        "panel_ages",
        "panel_foot",
    ):
        assert "$INFO[Container(51).ListItem.Property({})]".format(prop) in labels
    types = {c.get("type") for c in panel[0].iter("control")}
    assert not types & {"button", "list", "panel", "scrollbar"}
