# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Custom full-screen dialog for NZB search results selection."""

import xbmcaddon
import xbmcgui

from resources.lib.http_util import format_size as _format_size
from resources.lib.i18n import fmt as _fmt
from resources.lib.i18n import string as _string
from resources.lib.results_groups import group_pick, release_groups
from resources.lib.results_input import OkHold

# Color constants matching the mockup
_RES_COLORS = {
    "4320p": "FFA78BFA",
    "2160p": "FFA78BFA",
    "1440p": "FF60A5FA",
    "1080p": "FF60A5FA",
    "720p": "FF4ADE80",
    "480p": "FFFBBF24",
}

_SRC_COLORS = {
    "BluRay REMUX": "FF60A5FA",
    "REMUX": "FF60A5FA",
    "BluRay": "FF4ADE80",
    "WEB-DL": "FFC084FC",
    "WEBRip": "FFF0ABFC",
    "HDTV": "FFFDE68A",
}

_SRC_SHORT = {
    "BluRay REMUX": "REMUX",
    "BluRay": "BluRay",
    "WEB-DL": "WEB-DL",
    "WEBRip": "WEBRip",
    "HDTV": "HDTV",
}


def _c(text, color):
    """Wrap text in Kodi [COLOR] tags."""
    if not text:
        return ""
    return "[COLOR {}]{}[/COLOR]".format(color, text)


# Row backgrounds for alternating stripes
_BG_A = "FF0C0C10"
_BG_B = "FF141417"
_AVAILABLE_LABEL = "DL"

ACTION_SELECT = 7
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_CONTEXT_MENU = 117

LIST_ID = 50
COMPACT_LIST_ID = 51

VIEW_LIST = "list"
VIEW_COMPACT = "compact"
# ``results_view`` setting values (settings.xml option order).
_VIEW_SETTING = {"0": VIEW_COMPACT, "1": VIEW_LIST}

# Copies listed in the List compacted side panel before "+N more".
_PANEL_MAX_COPIES = 14
# Characters per line when the panel breaks a release name at its dots.
_PANEL_NAME_WIDTH = 46
_COPY_LEAD_COLOR = "FFFFFFFF"
_COPY_COLOR = "FF4A9EFF"
_COPIES_CHIP_COLOR = "FFA8CCFF"


def _available_text():
    return _c(_AVAILABLE_LABEL, "FF22C55E")


def _build_result_item(result, row_index):
    """Build a styled ListItem for a single search result row."""
    label = result.get("_display_title") or result.get("title", "")
    li = xbmcgui.ListItem(label=label)
    _set_result_properties(li, result)
    # Alternating row background
    li.setProperty("row_bg", _BG_A if row_index % 2 == 0 else _BG_B)
    return li


def _set_result_properties(li, result):
    """Set a result row's colored metadata properties on ``li``."""
    meta = result.get("_meta", {})
    is_pack = bool(result.get("_season_pack"))

    res = meta.get("resolution", "")
    li.setProperty("resolution", _c(res, _RES_COLORS.get(res, "FFEEEEEE")))

    hdr_list = meta.get("hdr", [])
    if hdr_list:
        li.setProperty("hdr", _c(" ".join(hdr_list), "FFFBBF24"))
    elif not is_pack:
        li.setProperty("hdr", _c(_string(30400), "FF6B7280"))
    else:
        li.setProperty("hdr", "")

    li.setProperty("codec", _c(meta.get("codec", ""), "FF94A3B8"))

    audio_list = meta.get("audio", [])
    audio_str = " ".join(audio_list) if audio_list else ""
    li.setProperty("audio", _c(audio_str, "FFE879A8"))

    quality = meta.get("quality", "")
    src_display = _SRC_SHORT.get(quality, quality)
    li.setProperty("quality", _c(src_display, _SRC_COLORS.get(quality, "FFAAAAAA")))

    # Container (MKV, MP4, etc.)—default to MKV since most scene releases
    # are MKV and only MP4 releases tag the title.
    default_container = "" if is_pack else "MKV"
    container = (meta.get("container", "") or default_container).upper()
    # MKV green; everything else (incl. MP4, which is fully supported via the
    # stream proxy) gets neutral grey rather than error-red, so a supported
    # container is not flagged as a false negative.
    container_color = "FF34D399" if container == "MKV" else "FFA1A1AA"
    li.setProperty("container", _c(container, container_color))

    li.setProperty("size", _c(_format_size(result.get("size")), "FFA1A1AA"))
    li.setProperty("age", _c(result.get("age", ""), "FF6B7280"))
    li.setProperty("indexer", _c(result.get("indexer", ""), "FF4A9EFF"))
    li.setProperty("group", _c(meta.get("group", ""), "FF34D399"))

    if result.get("_available"):
        li.setProperty("available", _available_text())

    reject = result.get("_filter_reject")
    li.setProperty(
        "filter_reason", _c(_fmt(30371, reject), "FFFBBF24") if reject else ""
    )


def _wrap_release_name(name, width=_PANEL_NAME_WIDTH):
    """Break a dotted release name into ``[CR]``-joined lines for the panel.

    Kodi wraps text only at spaces, so a scene name (no spaces) would be cut
    off; break it after a dot (or space) instead, keeping the dots visible.
    A single piece longer than ``width`` gets a line of its own.
    """
    lines = []
    line = ""
    piece = ""
    for char in str(name or ""):
        piece += char
        if char in ". ":
            if line and len(line) + len(piece) > width:
                lines.append(line)
                line = ""
            line += piece
            piece = ""
    if piece:
        if line and len(line) + len(piece) > width:
            lines.append(line)
            line = ""
        line += piece
    if line:
        lines.append(line)
    return "[CR]".join(lines)


def _tech_summary(item_props):
    """The panel's one-line tech summary from a row's colored properties."""
    keys = ("resolution", "hdr", "codec", "audio", "quality", "container", "group")
    return "   ".join(item_props[key] for key in keys if item_props.get(key))


def _panel_copy_lines(group):
    """Panel columns (marks, indexers, sizes, ages) for one release group."""
    pick = group_pick(group)
    shown = group[:_PANEL_MAX_COPIES]
    if len(group) > _PANEL_MAX_COPIES:
        shown = group[: _PANEL_MAX_COPIES - 1]
        if not any(row is pick for row in shown):
            shown = shown[:-1] + [pick]
    marks, indexers, sizes, ages = [], [], [], []
    for row in shown:
        marks.append(_available_text() if row.get("_available") else " ")
        color = _COPY_LEAD_COLOR if row is pick else _COPY_COLOR
        indexers.append(_c(row.get("indexer", "") or " ", color))
        sizes.append(_c(_format_size(row.get("size")) or " ", "FFA1A1AA"))
        ages.append(_c(row.get("age", "") or " ", "FF6B7280"))
    if len(shown) < len(group):
        marks.append(" ")
        indexers.append(_c(_fmt(30489, len(group) - len(shown)), "FF9CA3AF"))
        sizes.append(" ")
        ages.append(" ")
    return ["[CR]".join(column) for column in (marks, indexers, sizes, ages)]


class _PropertyRecorder:  # pylint: disable=too-few-public-methods
    """Wrap a ListItem to remember the properties set on it."""

    def __init__(self, item):
        self.item = item
        self.props = {}

    def setProperty(self, key, value):  # pylint: disable=invalid-name
        self.props[key] = value
        self.item.setProperty(key, value)


def _build_group_item(group, row_index):
    """Build the List compacted row for one release group.

    The row is the group's lead (top-ranked) copy, built like a List row, plus
    the copy count, an any-copy DL mark, and the side-panel properties the skin
    reads from the focused item (``Container(51).ListItem.Property(...)``).
    """
    lead = group[0]
    label = lead.get("_display_title") or lead.get("title", "")
    item = xbmcgui.ListItem(label=label)
    recorder = _PropertyRecorder(item)
    _set_result_properties(recorder, lead)
    item.setProperty("row_bg", _BG_A if row_index % 2 == 0 else _BG_B)
    count = len(group)
    indexers = []
    for row in group:
        name = row.get("indexer", "")
        if name and name not in indexers:
            indexers.append(name)
    any_available = any(row.get("_available") for row in group)
    if any_available:
        item.setProperty("available", _available_text())
    if count > 1:
        item.setProperty("copies", _c(_fmt(30479, count), _COPIES_CHIP_COLOR))
        kicker = _fmt(30478, count, len(indexers))
    else:
        item.setProperty("copies", _c(lead.get("indexer", ""), "FF4A9EFF"))
        kicker = _fmt(30480, lead.get("indexer", "") or "?")
    item.setProperty("panel_kicker", kicker)
    item.setProperty("panel_title", _wrap_release_name(label))
    item.setProperty("panel_tech", _tech_summary(recorder.props))
    marks, names, sizes, ages = _panel_copy_lines(group)
    item.setProperty("panel_marks", marks)
    item.setProperty("panel_indexers", names)
    item.setProperty("panel_sizes", sizes)
    item.setProperty("panel_ages", ages)
    item.setProperty("panel_foot", _string(30487 if any_available else 30488))
    # The highlighted (white) panel line is the copy Enter plays.
    return item


def _provider_only(results):
    """``results`` with the synthetic local season-pack row excluded.

    ``_prepend_pack`` inserts that row into both the filtered and
    all-rows views, so it is always non-empty/truthy even when zero
    online provider results survived filtering. Callers deciding
    "did anything real pass the filters" must check this, not the raw
    list.
    """
    return [
        row
        for row in results
        if not (isinstance(row, dict) and row.get("_season_pack"))
    ]


class ResultsDialog(xbmcgui.WindowXMLDialog):
    """Full-screen NZB results selection dialog with a show-all toggle."""

    def __init__(self, *args, **kwargs):
        self.results = kwargs.get("results", [])
        self.all_results = kwargs.get("all_results") or []
        self.title = kwargs.get("title", "")
        self.year = kwargs.get("year", "")
        self.total_count = kwargs.get("total_count", 0)
        self.view = VIEW_COMPACT if kwargs.get("view") == VIEW_COMPACT else VIEW_LIST
        self._list_id = COMPACT_LIST_ID if self.view == VIEW_COMPACT else LIST_ID
        self._entries = []
        self.show_all = not _provider_only(self.results) and bool(self.all_results)
        self.selected_result = None
        self._closing = False
        self._ok_hold = OkHold(self._show_all, self._select_focused)
        super().__init__(*args)

    def _toggle_available(self):
        """Keep a zero-survivors picker pinned to its non-empty show-all view."""
        return bool(self.results) and bool(self.all_results)

    def _active_results(self):
        return self.all_results if self.show_all else self.results

    def _build_entries(self, active):
        """One entry (a list of rows, lead first) per list row of this view."""
        if self.view == VIEW_COMPACT:
            return release_groups(active)
        return [[row] for row in active]

    def _footer_hints(self):
        if not self._toggle_available():
            return _string(30370)
        if self.show_all:
            return _string(30369)
        return _fmt(30368, len(self.all_results) - len(self.results))

    def _populate(self):
        """(Re)build the list control and window properties for the active view."""
        active = self._active_results()
        title_display = self.title
        if self.year:
            title_display = "{} ({})".format(self.title, self.year)
        self._entries = self._build_entries(active)
        compact = self.view == VIEW_COMPACT
        releases = len(self._entries)
        self.setProperty("view", self.view)
        self.setProperty("title", title_display)
        if compact:
            self.setProperty("count", _fmt(30481, releases, len(active)))
            sort_info = "{} · {}".format(_string(30111), _string(30492))
            self.setProperty("sort_info", sort_info)
        else:
            self.setProperty("count", _fmt(30110, len(active)))
            self.setProperty("sort_info", _string(30111))
        if self.show_all and compact:
            self.setProperty("filter_info", _fmt(30483, len(active), releases))
        elif self.show_all:
            self.setProperty("filter_info", _fmt(30367, len(active)))
        elif compact:
            self.setProperty(
                "filter_info", _fmt(30482, releases, len(active), self.total_count)
            )
        else:
            self.setProperty("filter_info", _fmt(30112, len(active), self.total_count))
        hints = self._footer_hints()
        if self.all_results and self._ok_hold.available:
            hints += "     " + _string(30477)
        self.setProperty("footer_hints", hints)

        list_control = self.getControl(self._list_id)
        list_control.reset()
        if compact:
            items = [
                _build_group_item(group, i) for i, group in enumerate(self._entries)
            ]
        else:
            items = [_build_result_item(result, i) for i, result in enumerate(active)]
        list_control.addItems(items)

    def _focused_entry(self):
        """The focused entry (list of rows) of the active view, or None."""
        pos = self.getControl(self._list_id).getSelectedPosition()
        if isinstance(pos, int) and 0 <= pos < len(self._entries):
            return self._entries[pos]
        return None

    def onInit(self):
        """Populate the dialog with results data."""
        if self.all_results:
            self._ok_hold.start()
        self._populate()
        self.setFocusId(self._list_id)

    def _toggle_show_all(self):
        """Flip views, repopulate, and re-focus the same row by identity.

        In List compacted the groups are rebuilt for the new row set, so the
        focus lands on whichever new group holds the previously focused lead.
        """
        entry = self._focused_entry()
        focused = entry[0] if entry else None
        self.show_all = not self.show_all
        self._populate()
        index = 0
        if focused is not None:
            for i, rows in enumerate(self._entries):
                if any(row is focused for row in rows):
                    index = i
                    break
        self.getControl(self._list_id).selectItem(index)
        self.setFocusId(self._list_id)

    def _select_focused(self):
        """Record the focused row and close.

        For a List compacted group that is ``group_pick``: an already
        downloaded copy when the group has one, else the lead copy.
        """
        if self._closing:
            return
        entry = self._focused_entry()
        self.selected_result = group_pick(entry) if entry else None
        self.close()

    def _show_all(self):
        """A held OK always turns off filters, including when they are already off."""
        if not self._closing and self.all_results and not self.show_all:
            self._toggle_show_all()

    def close(self):
        """Stop hold detection before the picker goes away."""
        self._closing = True
        self._ok_hold.close()
        super().close()

    def _cancel(self):
        self.selected_result = None
        self.close()

    def onClick(self, controlId):
        """Handle item selection."""
        if controlId == self._list_id and not self._ok_hold.defer_selection():
            self._select_focused()

    def onAction(self, action):
        """Handle keyboard/remote actions."""
        action_id = action.getId()
        if action_id in (ACTION_SELECT,):
            if (
                self.getFocusId() == self._list_id
                and not self._ok_hold.defer_selection()
            ):
                self._select_focused()
        elif action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self._cancel()
        elif action_id in (ACTION_CONTEXT_MENU, 0xF043):
            # Kodi emits ContextMenu early for a native long OK press. Wait
            # for the physical release or five seconds on supported devices.
            if self._ok_hold.defer_selection():
                return
            if self._toggle_available():
                self._toggle_show_all()

    def get_selected_result(self):
        """Return the chosen result dict, or ``None`` when cancelled."""
        return self.selected_result


def _results_view(addon):
    """The ``results_view`` setting as a view name; List compacted by default.

    Read on the picker's own (Kodi add-on) thread, like the rest of
    ``show_results_dialog``. An unset or unknown value is the default.
    """
    try:
        value = str(addon.getSetting("results_view") or "").strip()
    except Exception:  # pylint: disable=broad-except
        value = ""
    return _VIEW_SETTING.get(value, VIEW_COMPACT)


def show_results_dialog(results, title="", year="", total_count=0, all_results=None):
    """Show the full-screen results dialog and wait for the user pick.

    Args:
        results: List of filtered result dicts (must include
            ``title``, ``size``, ``_meta`` per ``filter_results``).
        title: Movie or show title for the dialog heading.
        year: Release year (movies) or show year (episodes),
            displayed beside the title.
        total_count: Number of results before filtering, used to
            render "Showing N of M" in the dialog header.
        all_results: Optional unfiltered row list (same dict objects as
            ``results`` plus the filter-rejected ones). When provided with
            a non-empty filtered list, the context-menu button toggles
            the filter status even when both lists contain the same rows;
            when ``results`` is empty the dialog opens directly in show-all mode.

    Returns:
        The chosen result dict (same object reference from whichever view
        was active) when the user picks one, or ``None`` when the user
        cancels the dialog or no results are available. In List compacted
        (the ``results_view`` default) a picked group returns its first
        already-downloaded copy, else its lead, top-ranked copy; the other
        copies stay in ``results`` for the same-release backups.
    """
    addon = xbmcaddon.Addon("plugin.video.nzbdav")
    addon_path = addon.getAddonInfo("path")
    view = _results_view(addon)

    dialog = ResultsDialog(
        "results-dialog.xml",
        addon_path,
        "Default",
        "1080i",
        results=results,
        title=title,
        year=year,
        total_count=total_count,
        all_results=all_results,
        view=view,
    )
    try:
        dialog.doModal()
        selected = dialog.get_selected_result()
    finally:
        dialog.close()
    del dialog
    return selected


def _format_date(pubdate):
    """Extract YYYY-MM-DD from an RFC 2822 pubdate string."""
    if not pubdate:
        return ""
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(pubdate)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return pubdate[:10] if len(pubdate) >= 10 else pubdate


def _lang_short(lang):
    """Convert language name to short code."""
    _MAP = {
        "English": "EN",
        "Spanish": "ES",
        "French": "FR",
        "German": "DE",
        "Italian": "IT",
        "Portuguese": "PT",
        "Dutch": "NL",
        "Russian": "RU",
        "Japanese": "JA",
        "Korean": "KO",
        "Chinese": "ZH",
        "Arabic": "AR",
        "Hindi": "HI",
    }
    return _MAP.get(lang, lang[:2].upper() if lang else "")
