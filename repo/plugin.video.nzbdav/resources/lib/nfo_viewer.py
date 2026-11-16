# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The picker's NFO and MediaInfo viewer.

Left or Right on a release in the picker opens this window over it
(``show``). It shows every NFO and MediaInfo report the indexers have for
the release:

* **Tabs, one per unique text.** Indexers mostly carry the very same NFO, so
  identical texts share a tab, and the tab lists every indexer that carries
  it. A tab grows with that list; it is never cut short (``rail_layout``).
* **The text**, drawn with the add-on's own terminal font so an NFO's art
  lines up on any skin (``text_image``). It is drawn for the screen it is
  on, so how many lines and columns a page holds, and with that the page
  count, follows the resolution (``geometry``). Up and Down turn the page.
* **What the text says of the release**, under it: each attribute with a
  check mark when the listing's name already says it, or a small NEW when
  the text is where it comes from (``doc_attributes``).

What is already kept for the release is shown at once. The indexers that
were not asked yet are asked in the background (``doc_fetch``), and the
tabs are redrawn when they have answered.
"""

import hashlib
import os
import re
import threading

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import (
    doc_attributes,
    doc_fetch,
    mediainfo_text,
    nfo_audio,
    release_docs,
    text_image,
)
from resources.lib.filter import parse_title_metadata, release_identity
from resources.lib.i18n import addon_name
from resources.lib.i18n import fmt as _fmt
from resources.lib.i18n import string as _string
from resources.lib.picker_colors import RES_COLORS, SRC_COLORS
from resources.lib.release_display import clean_release_name
from resources.lib.release_subject import name_form_key

FONT_FILE = "terminal-8x16.bin"

ACTION_MOVE_LEFT = 1
ACTION_MOVE_RIGHT = 2
ACTION_MOVE_UP = 3
ACTION_MOVE_DOWN = 4
ACTION_PAGE_UP = 5
ACTION_PAGE_DOWN = 6
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_MOUSE_WHEEL_UP = 104
ACTION_MOUSE_WHEEL_DOWN = 105

_IMAGE_ID = 20
_ATTRIBUTES_ID = 30

# The window's layout, in its 1920x1080 coordinates (``nfo-viewer.xml``).
_TEXT_LEFT = 528
_TEXT_TOP = 135
_TEXT_WIDTH = 1280
_TEXT_HEIGHT = 736
_ATTRIBUTES_LEFT = 525
_ATTRIBUTES_TOP = 916
_ATTRIBUTES_WIDTH = 1275
_RAIL_LEFT = 108
_RAIL_TOP = 66
_RAIL_WIDTH = 369
_RAIL_HEIGHT = 930
_TAB_PADDING = 15
_TAB_HEADING = 36
_TAB_GAP = 15
_TAB_INSET = 18
# An indexer's line in a tab: this tall, less when the tabs would not fit.
_LINE_HEIGHT = 33
_MIN_LINE_HEIGHT = 24

# An NFO is laid out for 80 columns: the text is drawn as large as still
# shows that many.
_NFO_COLUMNS = 80
# Without the add-on's font the skin's text box shows the text: its font is
# not known, so a page is kept short enough for any.
_FALLBACK_COLUMNS = 100
_FALLBACK_ROWS = 18

_TEXT_COLOR = "D4D4D8"
_CHECK_COLOR = "6B7280"
_NEW_COLOR = "FFFFFF"
_SLOT_COLORS = {
    doc_attributes.HDR: "FBBF24",
    doc_attributes.CODEC: "94A3B8",
    doc_attributes.AUDIO: "E879A8",
    doc_attributes.GROUP: "34D399",
}
_TAB_SELECTED = "0xFF1B1C2A"
_TAB_BORDER = "0xFF23253A"
_NAME_SELECTED = "0xFFFFFFFF"
_NAME_OTHER = "0xFFA1A1AA"

_ALIGN_CENTER_Y = 0x04
_KODI_MARKUP = re.compile(
    r"\[(/?(?:B|I|COLOR|CR|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT|TABS))", re.I
)
_CACHE_PATH = "special://temp/nzbdav-viewer/"


def geometry(screen_width, screen_height):
    """How the text is drawn on a screen of this many pixels.

    ``{"scale", "columns", "rows", "pixels"}``: every font pixel is ``scale``
    screen pixels, a page is ``columns`` by ``rows`` characters, and
    ``pixels`` is how many screen pixels one window coordinate is. The scale
    is the largest that still shows an NFO's 80 columns, so the text is as
    large on a 4K screen as on a 1080p one, and a screen in between shows
    more of it per page.
    """
    try:
        pixels = min(float(screen_width) / 1920.0, float(screen_height) / 1080.0)
    except (TypeError, ValueError):
        pixels = 1.0
    if not 0.3 <= pixels <= 8.0:
        pixels = 1.0
    width = int(_TEXT_WIDTH * pixels + 0.001)
    height = int(_TEXT_HEIGHT * pixels + 0.001)
    scale = width // (_NFO_COLUMNS * text_image.GLYPH_WIDTH)
    scale = min(text_image.MAX_SCALE, max(1, scale))
    return {
        "scale": scale,
        "columns": max(1, width // (text_image.GLYPH_WIDTH * scale)),
        "rows": max(1, height // (text_image.GLYPH_HEIGHT * scale)),
        "pixels": pixels,
    }


def rail_layout(counts, selected, height=_RAIL_HEIGHT):
    """Where each tab goes: ``[{"top", "height", "line", "shown"}]``.

    ``counts`` is how many indexers each tab lists; a tab is as tall as its
    list. When the tabs do not all fit, the lines are set closer; when they
    still do not, the tabs move up so the selected one is whole, and a tab
    that would be cut by the edge is not shown.
    """
    counts = [max(1, int(count)) for count in counts]
    if not counts:
        return []
    fixed = len(counts) * (2 * _TAB_PADDING + _TAB_HEADING)
    fixed += (len(counts) - 1) * _TAB_GAP
    line = _LINE_HEIGHT
    if fixed + line * sum(counts) > height:
        line = max(_MIN_LINE_HEIGHT, (height - fixed) // sum(counts))
        line = min(_LINE_HEIGHT, line)
    tabs = []
    top = 0
    for count in counts:
        tall = 2 * _TAB_PADDING + _TAB_HEADING + line * count
        tabs.append({"top": top, "height": tall, "line": line})
        top += tall + _TAB_GAP
    chosen = tabs[min(max(0, selected), len(tabs) - 1)]
    shift = max(0, chosen["top"] + chosen["height"] - height)
    shift = min(shift, chosen["top"])
    for tab in tabs:
        tab["top"] -= shift
        tab["shown"] = tab["top"] >= 0 and tab["top"] + tab["height"] <= height
    chosen["shown"] = True
    return tabs


def release_rows(entry, active_rows):
    """Every listing of the release the picker entry ``entry`` is.

    The entry's own rows, and the other rows of the list under the very same
    name: the picker's plain list has one row per entry, and the viewer
    names every indexer that carries a text.
    """
    rows = [row for row in entry if isinstance(row, dict)]
    if not rows:
        return []
    key = name_form_key(rows[0].get("title") or "")
    seen = {id(row) for row in rows}
    for row in active_rows or ():
        if not isinstance(row, dict) or id(row) in seen or row.get("_season_pack"):
            continue
        title = row.get("title")
        if isinstance(title, str) and title and name_form_key(title) == key:
            seen.add(id(row))
            rows.append(row)
    return rows


def indexer_names(rows):
    """``host -> [indexer names]`` for the listings' NFO and MediaInfo links."""
    names = {}
    for row in rows:
        name = row.get("indexer")
        if not isinstance(name, str) or not name:
            continue
        for field in ("nfo_url", "media_ref"):
            link = row.get(field)
            host = nfo_audio.host_of(link) if isinstance(link, str) and link else ""
            if host and name not in names.setdefault(host, []):
                names[host].append(name)
    return names


def slot_color(slot, text):
    """The picker's color for an attribute: "RRGGBB"."""
    if slot == doc_attributes.RESOLUTION:
        return RES_COLORS.get(text, "FFEEEEEE")[-6:]
    if slot == doc_attributes.SOURCE:
        return SRC_COLORS.get(text, "FFAAAAAA")[-6:]
    return _SLOT_COLORS.get(slot, "EEEEEE")


def attribute_words(attributes, check):
    """The attributes as ``text_image.words_png`` words.

    Each attribute in its picker color, then small: ``check`` when the
    listing's name already says it, else NEW.
    """
    words = []
    for attribute in attributes:
        color = slot_color(attribute["slot"], attribute["text"])
        words.append((attribute["text"], color, False))
        if attribute["new"]:
            words.append((_string(30781), _NEW_COLOR, True))
        else:
            words.append((check, _CHECK_COLOR, True))
    return words


def _plain(text):
    """``text`` with nothing Kodi would read as label markup."""
    return _KODI_MARKUP.sub(r"[ \1", text)


def attribute_label(attributes):
    """The attributes as one Kodi label, for the skin's own font."""
    parts = []
    for attribute in attributes:
        color = slot_color(attribute["slot"], attribute["text"])
        part = "[COLOR FF{}]{}[/COLOR]".format(color, _plain(attribute["text"]))
        if attribute["new"]:
            part += " [COLOR FF9CA3AF]{}[/COLOR]".format(_string(30781).lower())
        parts.append(part)
    return "     ".join(parts)


def _shown_text(tab):
    if tab["kind"] == release_docs.MEDIAINFO:
        return mediainfo_text.as_text(tab["text"])
    return tab["text"]


def build_tabs(record, rows, title, columns, page_rows):
    """The viewer's tabs for a release's kept texts, each laid out in pages.

    ``release_docs.tabs`` with, per tab: ``name`` (what the header shows:
    the file a MediaInfo report is about, else the release's name),
    ``pages`` (lists of lines) and ``digest`` (names its page images).
    """
    names = indexer_names(rows)
    tabs = release_docs.tabs(record, lambda host: names.get(host, ()))
    for tab in tabs:
        shown = _shown_text(tab)
        tab["pages"] = text_image.paginate(text_image.wrap(shown, columns), page_rows)
        tab["name"] = title
        if tab["kind"] == release_docs.MEDIAINFO:
            tab["name"] = mediainfo_text.file_name(tab["text"]) or title
        tab["digest"] = hashlib.sha1(  # nosec B324
            "{}\n{}".format(tab["kind"], shown).encode("utf-8", "replace")
        ).hexdigest()[:20]
        tab["attributes"] = None
    return tabs


def _write(path, data):
    """Write ``data`` to ``path`` through a temporary file; False on failure."""
    tmp_path = "{}.{}.tmp".format(path, threading.get_ident())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def _empty_cache(folder):
    """Remove the page images of an earlier viewing."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.endswith((".png", ".tmp")):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                # In use, or already gone.
                pass


class NfoViewer(
    xbmcgui.WindowXMLDialog
):  # pylint: disable=too-many-instance-attributes
    """The viewer window; ``show`` opens it."""

    def __init__(self, *args, **kwargs):
        self._rows = kwargs.get("rows") or []
        self._key = kwargs.get("key") or ""
        self._title = kwargs.get("title") or ""
        self._font = kwargs.get("font")
        self._geometry = kwargs.get("geometry") or geometry(1920, 1080)
        self._white = kwargs.get("white") or "white.png"
        self._cache = kwargs.get("cache") or ""
        self._lock = threading.RLock()
        self._tabs = []
        self._tab = 0
        self._page = 0
        self._asking = 0
        self._rail = []
        self._started = False
        self._closed = False
        super().__init__(*args)

    # --- what is shown ------------------------------------------------------

    def _page_size(self):
        if self._font:
            return self._geometry["columns"], self._geometry["rows"]
        return _FALLBACK_COLUMNS, _FALLBACK_ROWS

    def _load(self):
        """Read what is kept for the release and draw it, keeping the place."""
        record = release_docs.load(self._key)
        columns, page_rows = self._page_size()
        tabs = build_tabs(record, self._rows, self._title, columns, page_rows)
        with self._lock:
            if self._closed:
                return
            before = self._tabs[self._tab]["digest"] if self._tabs else None
            page = self._page
            self._tabs = tabs
            self._tab = 0
            self._page = 0
            for index, tab in enumerate(tabs):
                if tab["digest"] == before:
                    self._tab = index
                    self._page = min(page, len(tab["pages"]) - 1)
            self._draw()

    def _current(self):
        return self._tabs[self._tab] if self._tabs else None

    def _attributes(self, tab):
        if tab["attributes"] is None:
            try:
                tab["attributes"] = doc_attributes.attributes(
                    tab["kind"],
                    tab["text"],
                    self._title,
                    parse_title_metadata,
                    release_identity,
                )
            except Exception as exc:  # pylint: disable=broad-except
                xbmc.log(
                    "NeNeTeePee-Stream-Kodi: reading a text's attributes "
                    "failed: {}".format(exc),
                    xbmc.LOGWARNING,
                )
                tab["attributes"] = []
        return tab["attributes"]

    def _hints(self):
        hints = []
        if len(self._tabs) > 1:
            hints.append(_string(30775))
        tab = self._current()
        if tab and len(tab["pages"]) > 1:
            hints.append(_string(30776))
        if self._asking and self._tabs:
            hints.append(_fmt(30779 if self._asking == 1 else 30778, self._asking))
        return "     ".join(hints)

    def _status(self):
        if self._tabs:
            return ""
        if self._asking:
            return _fmt(30779 if self._asking == 1 else 30778, self._asking)
        return _string(30780)

    def _draw(self):
        tab = self._current()
        self.setProperty("mode", ("image" if self._font else "text") if tab else "")
        self.setProperty("status", self._status())
        self.setProperty("hints", self._hints())
        self.setProperty("close_hint", _string(30777))
        self.setProperty("name", tab["name"] if tab else self._title)
        pages = len(tab["pages"]) if tab else 0
        self.setProperty("page", _fmt(30774, self._page + 1, pages) if tab else "")
        self._draw_rail()
        if not tab:
            return
        lines = tab["pages"][self._page]
        attributes = self._attributes(tab)
        if not self._font:
            self.setProperty("text", "[CR]".join(_plain(line) for line in lines))
            self.setProperty("attributes", attribute_label(attributes))
            return
        self._draw_images(tab, lines, attributes)

    def _draw_images(self, tab, lines, attributes):
        view = self._geometry
        scale = view["scale"]
        # Kodi keeps a drawn image by its path: the name says all it shows.
        name = "{}-{}-{}x{}x{}".format(
            tab["digest"], self._page, view["columns"], view["rows"], scale
        )
        page_path = os.path.join(self._cache, name + ".png")
        if not os.path.exists(page_path):
            png = text_image.page_png(
                lines, self._font, view["columns"], view["rows"], _TEXT_COLOR, scale
            )
            _write(page_path, png)
        image = self.getControl(_IMAGE_ID)
        image.setWidth(
            int(view["columns"] * text_image.GLYPH_WIDTH * scale / view["pixels"])
        )
        image.setHeight(
            int(view["rows"] * text_image.GLYPH_HEIGHT * scale / view["pixels"])
        )
        image.setImage(page_path, False)

        # Kodi keeps a drawn image by its path: the name says all it shows.
        words = attribute_words(attributes, text_image.check_mark(self._font))
        width = int(_ATTRIBUTES_WIDTH * view["pixels"])
        said = hashlib.sha1(  # nosec B324
            repr((words, width, scale)).encode("utf-8", "replace")
        ).hexdigest()[:20]
        words_path = os.path.join(self._cache, "words-{}.png".format(said))
        if not os.path.exists(words_path):
            _write(words_path, text_image.words_png(words, self._font, width, scale))
        strip = self.getControl(_ATTRIBUTES_ID)
        strip.setWidth(int(width / view["pixels"]))
        strip.setHeight(int(text_image.GLYPH_HEIGHT * scale / view["pixels"]))
        strip.setImage(words_path, False)

    # --- the tabs -----------------------------------------------------------

    def _tab_controls(self, tab, place, selected):
        """The controls of one tab: its frame, its heading, its indexers."""
        bright = release_docs.COLORS[tab["kind"]]
        dimmed = release_docs.DIMMED_COLORS[tab["kind"]]
        left = _RAIL_LEFT
        top = _RAIL_TOP + place["top"]
        tall = place["height"]
        edge = "0x" + bright if selected else _TAB_BORDER
        controls = []
        if selected:
            controls.append(
                xbmcgui.ControlImage(
                    left,
                    top,
                    _RAIL_WIDTH,
                    tall,
                    self._white,
                    colorDiffuse=_TAB_SELECTED,
                )
            )
        for x, y, wide, high in (
            (left, top, _RAIL_WIDTH, 1),
            (left, top + tall - 1, _RAIL_WIDTH, 1),
            (left, top, 1, tall),
            (left + _RAIL_WIDTH - 1, top, 1, tall),
        ):
            controls.append(
                xbmcgui.ControlImage(x, y, wide, high, self._white, colorDiffuse=edge)
            )
        controls.append(
            xbmcgui.ControlLabel(
                left + _TAB_INSET,
                top + _TAB_PADDING,
                _RAIL_WIDTH - 2 * _TAB_INSET,
                _TAB_HEADING,
                "[B]{}[/B]".format(release_docs.LABELS[tab["kind"]]),
                font="font12",
                textColor="0x" + (bright if selected else dimmed),
                alignment=_ALIGN_CENTER_Y,
            )
        )
        line = place["line"]
        for index, name in enumerate(tab["indexers"]):
            controls.append(
                xbmcgui.ControlLabel(
                    left + _TAB_INSET,
                    top + _TAB_PADDING + _TAB_HEADING + index * line,
                    _RAIL_WIDTH - 2 * _TAB_INSET,
                    line,
                    _plain(name),
                    font="font12" if line >= 30 else "font10",
                    textColor=_NAME_SELECTED if selected else _NAME_OTHER,
                    alignment=_ALIGN_CENTER_Y,
                )
            )
        return controls

    def _draw_rail(self):
        if self._rail:
            self.removeControls(self._rail)
            self._rail = []
        places = rail_layout([len(tab["indexers"]) for tab in self._tabs], self._tab)
        controls = []
        for index, (tab, place) in enumerate(zip(self._tabs, places)):
            if place["shown"]:
                controls.extend(self._tab_controls(tab, place, index == self._tab))
        if controls:
            self.addControls(controls)
            self._rail = controls

    # --- Kodi's callbacks -----------------------------------------------------

    def onInit(self):  # pylint: disable=invalid-name
        """Show what is kept, and ask the indexers not asked yet."""
        if self._started:
            return
        self._started = True
        self._asking = len(doc_fetch.plan(self._rows, release_docs.load(self._key)))
        self._load()
        if self._asking:
            threading.Thread(target=self._ask, daemon=True).start()

    def _ask(self):
        try:
            doc_fetch.gather(self._rows, self._key, self._title, release_identity)
        except Exception as exc:  # pylint: disable=broad-except
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: asking the indexers failed: {}".format(exc),
                xbmc.LOGWARNING,
            )
        self._asking = 0
        try:
            self._load()
        except Exception as exc:  # pylint: disable=broad-except
            # The window went away while the indexers answered.
            xbmc.log(
                "NeNeTeePee-Stream-Kodi: the viewer was not redrawn: {}".format(exc),
                xbmc.LOGDEBUG,
            )

    def _switch(self, step):
        with self._lock:
            if len(self._tabs) > 1:
                self._tab = (self._tab + step) % len(self._tabs)
                self._page = 0
                self._draw()

    def _turn(self, step):
        with self._lock:
            tab = self._current()
            if not tab:
                return
            page = min(max(0, self._page + step), len(tab["pages"]) - 1)
            if page != self._page:
                self._page = page
                self._draw()

    def onAction(self, action):  # pylint: disable=invalid-name
        """Left and Right change the tab, Up and Down the page, Back closes."""
        action_id = action.getId()
        if action_id == ACTION_MOVE_LEFT:
            self._switch(-1)
        elif action_id == ACTION_MOVE_RIGHT:
            self._switch(1)
        elif action_id in (ACTION_MOVE_UP, ACTION_PAGE_UP, ACTION_MOUSE_WHEEL_UP):
            self._turn(-1)
        elif action_id in (ACTION_MOVE_DOWN, ACTION_PAGE_DOWN, ACTION_MOUSE_WHEEL_DOWN):
            self._turn(1)
        elif action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            self.close()

    def close(self):
        """Stop redrawing before the window goes away."""
        with self._lock:
            self._closed = True
        super().close()


def _screen_size():
    try:
        width = xbmcgui.getScreenWidth()
        height = xbmcgui.getScreenHeight()
    except Exception:  # pylint: disable=broad-except
        return 1920, 1080
    if isinstance(width, int) and isinstance(height, int) and width >= 640:
        return width, height
    return 1920, 1080


def show(entry, active_rows=None):
    """Open the viewer on a picker entry (a release's rows, lead first).

    True when the window was shown. When no listing of the release offers an
    NFO or a MediaInfo report and none is kept, there is nothing to open: a
    notification says so.
    """
    rows = release_rows(entry or (), active_rows)
    title = rows[0].get("title") if rows else ""
    title = title if isinstance(title, str) else ""
    key = name_form_key(title) if title else ""
    if key:
        record = release_docs.load(key)
        if not record["docs"] and not doc_fetch.plan(rows, record):
            key = ""
    if not key:
        xbmcgui.Dialog().notification(addon_name(), _string(30780), time=3000)
        return False
    addon_path = xbmcaddon.Addon("plugin.video.nzbdav").getAddonInfo("path")
    cache = xbmcvfs.translatePath(_CACHE_PATH)
    _empty_cache(cache)
    viewer = NfoViewer(
        "nfo-viewer.xml",
        addon_path,
        "Default",
        "1080i",
        rows=rows,
        key=key,
        title=clean_release_name(title) or title,
        font=text_image.load_font(
            os.path.join(addon_path, "resources", "fonts", FONT_FILE)
        ),
        geometry=geometry(*_screen_size()),
        white=os.path.join(
            addon_path, "resources", "skins", "Default", "media", "white.png"
        ),
        cache=cache,
    )
    try:
        viewer.doModal()
    finally:
        viewer.close()
    del viewer
    return True
