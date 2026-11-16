# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The search progress window and its corner-indicator stand-in."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import search_dialog
from resources.lib import search_progress as sp


class _Action:  # pylint: disable=too-few-public-methods
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


def _home():
    home = MagicMock()
    properties = {}
    home.getProperty.side_effect = lambda key: properties.get(key, "")
    home.setProperty.side_effect = properties.__setitem__
    home.clearProperty.side_effect = lambda key: properties.pop(key, None)
    return home


@pytest.fixture(name="home")
def _home_window():
    home = _home()
    with patch.object(search_dialog.xbmcgui, "Window", return_value=home):
        yield home


@pytest.fixture(autouse=True)
def _no_active_session():
    sp.deactivate()
    yield
    session = sp.active()
    if session is not None:
        session.close()
    sp.deactivate()


def _window(session):
    window = search_dialog.SearchWindow(search_dialog.WINDOW_XML, "/addon")
    window.open(session)
    return window


# --- the window --------------------------------------------------------------


def test_the_window_opens_with_its_texts_and_the_first_stage(home):
    session = sp.Session()
    session.identified("movie", "Heat", year="1995", tmdb="949")
    home.setProperty(search_dialog.CANCEL_PROPERTY, "1")

    with patch.object(search_dialog, "log_path", return_value="/logs/kodi.log"):
        window = _window(session)

    assert window.getProperty("heading") == "Finding sources"
    assert window.getProperty("subject") == "Heat (1995)"
    assert window.getProperty("elapsed") == "0:00"
    assert window.getProperty("stage.1.name") == "Identify the title"
    assert window.getProperty("stage.1.state") == sp.ACTIVE
    assert window.getProperty("stage.1.text") == "Heat (1995)[CR]TMDb 949"
    assert window.getProperty("stage.5.state") == sp.PENDING
    assert window.getProperty("table_heading") == "Indexers"
    assert window.getProperty("log_hint") == (
        "Log file: /logs/kodi.log[CR]"
        "The add-on's lines start with NeNeTeePee-Stream-Kodi."
    )
    assert window.getProperty("indexers") == ""
    # A cancel left over from an earlier search is not this one's.
    assert home.getProperty(search_dialog.CANCEL_PROPERTY) == ""
    assert not window.cancel_requested()


def test_the_indexer_table_is_filled_once_per_change(home):
    del home
    session = sp.Session()
    window = _window(session)
    session.indexer_report(
        [
            {"name": "Alpha", "ok": True, "count": 3, "ms": 500},
            {"name": "Beta", "ok": False, "error": "refused"},
        ]
    )
    items = []

    def _list_item(**kwargs):
        items.append(kwargs)
        return MagicMock()

    with patch.object(search_dialog.xbmcgui, "ListItem", side_effect=_list_item):
        window.render(session.snapshot())
        window.render(session.snapshot())

    assert window.getProperty("indexers") == "1"
    assert [(i["label"], i["label2"]) for i in items] == [
        ("Alpha", "3 results · 0.5 s"),
        ("Beta", "failed: refused"),
    ]
    assert all(i["offscreen"] is True for i in items)
    control = window.getControl(search_dialog.INDEXER_LIST_ID)
    control.reset.assert_called_once()
    control.addItems.assert_called_once()


def test_a_table_that_cannot_be_filled_is_tried_again(home):
    del home
    session = sp.Session()
    window = _window(session)
    session.indexer_report([{"name": "Alpha", "ok": True, "count": 3}])
    control = window.getControl(search_dialog.INDEXER_LIST_ID)
    control.reset.side_effect = [RuntimeError("no such control"), None]

    window.render(session.snapshot())
    window.render(session.snapshot())

    assert control.addItems.call_count == 1


def test_cancel_comes_from_the_home_property_a_click_or_back(home):
    window = _window(sp.Session())
    assert not window.cancel_requested()
    home.setProperty(search_dialog.CANCEL_PROPERTY, "1")
    assert window.cancel_requested()

    window = _window(sp.Session())
    window.onClick(999)
    window.onAction(_Action(7))
    assert not window.cancel_requested()
    window.onClick(search_dialog.CANCEL_BUTTON_ID)
    assert window.cancel_requested()

    window = _window(sp.Session())
    window.onAction(_Action(92))
    assert window.cancel_requested()


def test_closing_twice_closes_once_and_clears_the_cancel_mark(home):
    window = _window(sp.Session())
    home.setProperty(search_dialog.CANCEL_PROPERTY, "1")

    with patch.object(
        search_dialog.xbmcgui.WindowXMLDialog, "close", autospec=True
    ) as close:
        window.close()
        window.close()

    assert close.call_count == 1
    assert home.getProperty(search_dialog.CANCEL_PROPERTY) == ""


def test_the_log_path_is_kodis_log_folder():
    with patch("xbmcvfs.translatePath", return_value="/storage/.kodi/temp/"):
        assert search_dialog.log_path() == "/storage/.kodi/temp/kodi.log"
    with patch("xbmcvfs.translatePath", side_effect=RuntimeError("no vfs")):
        assert search_dialog.log_path() == "kodi.log"
    with patch("xbmcvfs.translatePath", return_value=MagicMock()):
        assert search_dialog.log_path() == "kodi.log"


# --- the corner indicator -----------------------------------------------------


def test_the_corner_indicator_shows_the_stage_in_hand_when_it_changes():
    session = sp.Session()
    session.search_started(["NZBHydra2"])
    with patch.object(search_dialog.xbmcgui, "DialogProgressBG") as dialog_cls:
        indicator = search_dialog.CornerIndicator("Add-on")
        indicator.open(session)
        indicator.render(session.snapshot())
        session.search_finished([])
        indicator.render(session.snapshot())
        indicator.close()
        indicator.close()

    dialog = dialog_cls.return_value
    dialog.create.assert_called_once_with("Add-on", "Search: Waiting for NZBHydra2")
    dialog.update.assert_called_once()
    assert dialog.update.call_args.args[1] == "Add-on"
    dialog.close.assert_called_once()
    assert indicator.cancel_requested() is False


# --- the painter ---------------------------------------------------------------


class _Surface:
    def __init__(self):
        self.frames = []
        self.cancel = False
        self.closed = 0
        self.drawn = threading.Event()

    def render(self, snapshot):
        self.frames.append(snapshot)
        self.drawn.set()

    def cancel_requested(self):
        return self.cancel

    def close(self):
        self.closed += 1


def test_a_tick_draws_and_a_cancel_closes_the_surface_at_once():
    session = sp.Session()
    surface = _Surface()
    painter = search_dialog._Painter(session, surface)

    painter._tick()
    assert len(surface.frames) == 1 and not session.cancelled()

    surface.cancel = True
    painter._tick()
    painter._tick()
    assert session.cancelled()
    assert surface.closed == 1
    assert len(surface.frames) == 1

    painter.close()
    assert surface.closed == 1


def test_the_painter_thread_draws_until_it_is_closed():
    session = sp.Session()
    surface = _Surface()
    monitor = MagicMock()
    monitor.waitForAbort.side_effect = lambda seconds: time.sleep(seconds) or False
    with patch.object(
        search_dialog.xbmc, "Monitor", return_value=monitor
    ), patch.object(search_dialog, "_TICK_SECONDS", 0.01):
        painter = search_dialog._Painter(session, surface)
        painter.start()
        assert surface.drawn.wait(5)
        painter.close()
        painter.close()

    assert surface.closed == 1
    assert not painter._thread.is_alive()


def test_the_painter_stops_when_kodi_is_shutting_down():
    surface = _Surface()
    monitor = MagicMock()
    monitor.waitForAbort.return_value = True
    with patch.object(
        search_dialog.xbmc, "Monitor", return_value=monitor
    ), patch.object(search_dialog, "_TICK_SECONDS", 0.01):
        painter = search_dialog._Painter(sp.Session(), surface)
        painter.start()
        painter._thread.join(5)

    assert not painter._thread.is_alive()
    assert not surface.frames
    painter.close()
    assert surface.closed == 1


def test_a_drawing_error_is_logged_once_and_drawing_goes_on():
    surface = _Surface()
    surface.render = MagicMock(side_effect=RuntimeError("window gone"))
    monitor = MagicMock()
    monitor.waitForAbort.side_effect = lambda seconds: time.sleep(seconds) or False
    fast = patch.object(search_dialog, "_TICK_SECONDS", 0.01)
    logger = patch.object(search_dialog.xbmc, "log")
    with patch.object(search_dialog.xbmc, "Monitor", return_value=monitor), fast:
        log = logger.start()
        painter = search_dialog._Painter(sp.Session(), surface)
        painter.start()
        deadline = time.monotonic() + 5
        while surface.render.call_count < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert painter._thread.is_alive()
        surface.cancel = True
        while surface.closed == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        painter.close()

        logger.stop()

    assert surface.closed == 1
    assert not painter._thread.is_alive()
    drawing = [c for c in log.call_args_list if "could not be drawn" in c.args[0]]
    assert len(drawing) == 1


# --- open_progress -------------------------------------------------------------


def test_open_progress_shows_the_window_and_reports_to_its_session(home):
    del home
    with patch.object(search_dialog.xbmcgui, "DialogProgressBG") as corner:
        session = search_dialog.open_progress("Add-on")

    assert sp.active() is session
    assert session.detailed is True
    corner.assert_not_called()
    sp.search_started(["NZBHydra2"])
    assert session.snapshot()["stages"][1]["state"] == sp.ACTIVE

    session.close()
    assert sp.active() is None


def test_the_setting_off_uses_the_corner_indicator(home):
    del home
    with patch.object(search_dialog, "SearchWindow") as window_cls, patch.object(
        search_dialog.xbmcgui, "DialogProgressBG"
    ) as corner:
        session = search_dialog.open_progress("Add-on", detailed=False)
        session.close()

    assert session.detailed is False
    window_cls.assert_not_called()
    corner.return_value.create.assert_called_once()
    corner.return_value.close.assert_called_once()


def test_a_window_that_will_not_open_falls_back_to_the_corner_indicator(home):
    del home
    window = MagicMock()
    window.open.side_effect = RuntimeError("skin refused the window")
    with patch.object(search_dialog, "SearchWindow", return_value=window), patch.object(
        search_dialog.xbmcgui, "DialogProgressBG"
    ) as corner:
        session = search_dialog.open_progress("Add-on")
        session.close()

    window.close.assert_called_once()
    corner.return_value.create.assert_called_once()
    corner.return_value.close.assert_called_once()


def test_nothing_to_draw_on_still_gives_a_working_session(home):
    del home
    with patch.object(
        search_dialog, "SearchWindow", side_effect=RuntimeError("no window")
    ), patch.object(
        search_dialog.xbmcgui, "DialogProgressBG", side_effect=RuntimeError("no dialog")
    ), patch.object(
        sp, "load_texts", side_effect=RuntimeError("no strings")
    ):
        session = search_dialog.open_progress("Add-on")

    assert sp.active() is session
    assert session.text("heading") == "Finding sources"
    session.close()
    assert sp.active() is None


def test_a_cancel_pressed_after_the_last_tick_is_seen_on_close():
    session = sp.Session()
    surface = _Surface()
    painter = search_dialog._Painter(session, surface)
    painter._tick()
    surface.cancel = True

    painter.close()

    assert session.cancelled()
    assert surface.closed == 1


def test_a_cancel_pressed_while_the_window_closes_is_kept(home):
    session = sp.Session()
    window = _window(session)
    painter = search_dialog._Painter(session, window)
    home.setProperty(search_dialog.CANCEL_PROPERTY, "1")

    with patch.object(search_dialog.xbmcgui.WindowXMLDialog, "close", autospec=True):
        painter.close()

    assert session.cancelled()
    assert home.getProperty(search_dialog.CANCEL_PROPERTY) == ""


def test_refilling_the_table_keeps_the_viewers_place(home):
    del home
    session = sp.Session()
    window = _window(session)
    control = window.getControl(search_dialog.INDEXER_LIST_ID)
    control.getSelectedPosition.return_value = 2
    session.indexer_report(
        [{"name": n, "ok": True, "count": 1} for n in ("A", "B", "C", "D")]
    )

    window.render(session.snapshot())

    control.selectItem.assert_called_once_with(2)
