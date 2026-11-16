# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The search progress window: what happens between play and the list.

``open_progress`` shows the add-on's own window (``search-progress.xml``):
five stages with their state, a table of how each indexer answered, the time
taken, where the log is and a Cancel button. It draws a
``search_progress.Session``. When the window cannot be opened, or the setting
turns it off, Kodi's small corner indicator shows the stage in hand instead,
so a skin or Kodi build that refuses the window never stops a search.

The window is shown, not run modally, and one short-lived thread draws it:
the add-on's own thread is busy searching, often blocked on the network.
That thread is the only one that touches the window while it is open. It
also reads Cancel from a Home window property the Cancel button and the Back
action set: Kodi runs those without the add-on's help, so the window closes
at once even while the searching thread waits on the network. Other keys
reach ``onAction`` only when Kodi next delivers the add-on's callbacks.
"""

import os
import threading

import xbmc
import xbmcgui

from resources.lib import search_progress

WINDOW_XML = "search-progress.xml"
SETTING = "search_progress_window"
CANCEL_BUTTON_ID = 10
INDEXER_LIST_ID = 50
CANCEL_PROPERTY = "nzbdav.search.cancel"
_HOME_WINDOW = 10000
_ACTION_PREVIOUS_MENU = 10
_ACTION_STOP = 13
_ACTION_NAV_BACK = 92
_CANCEL_ACTIONS = frozenset((_ACTION_PREVIOUS_MENU, _ACTION_STOP, _ACTION_NAV_BACK))
# How often the window is redrawn and Cancel is read, in seconds.
_TICK_SECONDS = 0.25
_JOIN_SECONDS = 2.0


def _home():
    return xbmcgui.Window(_HOME_WINDOW)


def _addon_path():
    """The add-on's folder, from this file's place in it.

    Not the Kodi add-on info API: that lookup can crash CoreELEC inside the
    TMDBHelper RunScript context (see ``cache._get_cache_dir``).
    """
    lib = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(lib))


def log_path():
    """Where Kodi writes ``kodi.log`` on this device, or just its name."""
    try:
        import xbmcvfs

        folder = xbmcvfs.translatePath("special://logpath/")
    except Exception:  # pylint: disable=broad-except
        folder = ""
    if not isinstance(folder, str) or not folder:
        return "kodi.log"
    return os.path.join(folder, "kodi.log")


def _debug(what, error):
    """Log a window step that failed; the search goes on without it."""
    xbmc.log(
        "{}: search progress window: {} failed: {}".format(
            search_progress.LOG_TAG, what, error
        ),
        xbmc.LOGDEBUG,
    )


def _format_elapsed(seconds):
    seconds = max(0, int(seconds))
    return "{}:{:02d}".format(seconds // 60, seconds % 60)


class SearchWindow(xbmcgui.WindowXMLDialog):
    """The window itself: properties for the stages, a list for the table."""

    def __init__(self, *args, **kwargs):
        del kwargs
        super().__init__(*args)
        self._canceled = False
        self._shown = False
        self._table = None
        self._drawn = None

    def open(self, session):
        """Show the window with its fixed texts and the first snapshot."""
        _home().clearProperty(CANCEL_PROPERTY)
        self._canceled = False
        self.setProperty("heading", session.text("heading"))
        self.setProperty("table_heading", session.text("table_heading"))
        self.setProperty("table_empty", session.text("table_empty"))
        self.setProperty(
            "log_hint",
            session.text("log_hint", log_path(), search_progress.LOG_TAG),
        )
        self.render(session.snapshot())
        self.show()
        self._shown = True

    def render(self, snapshot):
        """Draw a ``Session.snapshot()``; skipped when nothing changed."""
        drawn = (snapshot["version"], snapshot["elapsed"])
        if drawn == self._drawn and snapshot["indexers"] == self._table:
            return
        self._drawn = drawn
        self.setProperty("subject", snapshot["subject"])
        self.setProperty("elapsed", _format_elapsed(snapshot["elapsed"]))
        for number, stage in enumerate(snapshot["stages"], 1):
            prefix = "stage.{}.".format(number)
            self.setProperty(prefix + "name", stage["name"])
            self.setProperty(prefix + "state", stage["state"])
            self.setProperty(prefix + "text", stage["text"])
        rows = snapshot["indexers"]
        self.setProperty("indexers", "1" if rows else "")
        if rows != self._table and self._fill_table(rows):
            self._table = rows

    def _fill_table(self, rows):
        """Put the indexer rows in the list; False when it is not there yet."""
        if not self._shown:
            return False
        try:
            items = []
            for row in rows:
                item = xbmcgui.ListItem(
                    label=row["name"], label2=row["result"], offscreen=True
                )
                item.setProperty("state", row["state"])
                item.setProperty("details", row["details"])
                item.setProperty("details_state", row["details_state"])
                items.append(item)
            control = self.getControl(INDEXER_LIST_ID)
            # Keep the viewer's place when the table is refilled.
            position = control.getSelectedPosition()
            control.reset()
            control.addItems(items)
            if isinstance(position, int) and 0 < position < len(items):
                control.selectItem(position)
            return True
        except Exception:  # pylint: disable=broad-except
            return False

    def cancel_requested(self):
        """True once Cancel or Back was pressed."""
        if self._canceled:
            return True
        if _home().getProperty(CANCEL_PROPERTY) == "1":
            self._canceled = True
        return self._canceled

    def close(self):
        """Close the window and forget the cancel mark.

        A Cancel pressed up to this moment is kept in ``cancel_requested``.
        """
        try:
            if self._shown:
                self._shown = False
                super().close()
        finally:
            home = _home()
            if home.getProperty(CANCEL_PROPERTY) == "1":
                self._canceled = True
            home.clearProperty(CANCEL_PROPERTY)

    def onAction(self, action):  # pylint: disable=invalid-name
        """Back, Escape and Stop cancel once delivered; nothing else closes it."""
        if action.getId() in _CANCEL_ACTIONS:
            self._canceled = True

    def onClick(self, control_id):  # pylint: disable=invalid-name
        """A click on Cancel."""
        if control_id == CANCEL_BUTTON_ID:
            self._canceled = True


class CornerIndicator:
    """Kodi's background progress indicator, fed the stage in hand."""

    def __init__(self, heading):
        self._heading = heading
        self._dialog = xbmcgui.DialogProgressBG()
        self._last = None
        self._open = False

    def open(self, session):
        """Show the indicator with the first snapshot's line."""
        snapshot = session.snapshot()
        self._last = (snapshot["percent"], snapshot["summary"])
        self._dialog.create(self._heading, snapshot["summary"])
        self._open = True

    def render(self, snapshot):
        """Show the current stage's line when it changed."""
        line = (snapshot["percent"], snapshot["summary"])
        if line != self._last:
            self._last = line
            self._dialog.update(line[0], self._heading, line[1])

    @staticmethod
    def cancel_requested():
        """The indicator has no Cancel."""
        return False

    def close(self):
        """Close the indicator, once."""
        if self._open:
            self._open = False
            self._dialog.close()


class _Painter:
    """Draws a session on a surface from its own thread until closed."""

    def __init__(self, session, surface):
        self._session = session
        self._surface = surface
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run, name="nzbdav-search-progress", daemon=True
        )

    def start(self):
        """Begin drawing."""
        self._thread.start()

    def _run(self):
        monitor = xbmc.Monitor()
        failed = False
        while not self._stop.is_set():
            if monitor.waitForAbort(_TICK_SECONDS) or self._stop.is_set():
                return
            try:
                self._tick()
            except Exception as exc:  # pylint: disable=broad-except
                # Keep going: Cancel is read on the next tick all the same.
                if not failed:
                    failed = True
                    xbmc.log(
                        "{}: search progress window could not be drawn: {}".format(
                            search_progress.LOG_TAG, exc
                        ),
                        xbmc.LOGDEBUG,
                    )

    def _tick(self):
        with self._lock:
            if self._closed:
                return
            if self._surface.cancel_requested() is True:
                # The search cannot be interrupted mid-request: the window
                # goes now and the add-on stops at the next step.
                self._session.cancel()
                self._closed = True
                self._surface.close()
                return
            self._surface.render(self._session.snapshot())

    def close(self):
        """Stop drawing and close the surface; safe to call twice."""
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(_JOIN_SECONDS)
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._surface.close()
        except Exception as error:  # pylint: disable=broad-except
            _debug("closing", error)
        try:
            # A Cancel pressed after the last tick, up to the close itself,
            # still counts: the caller checks the session once it is closed.
            if self._surface.cancel_requested() is True:
                self._session.cancel()
        except Exception as error:  # pylint: disable=broad-except
            _debug("reading Cancel on close", error)


def _open_window(session):
    window = SearchWindow(WINDOW_XML, _addon_path(), "Default", "1080i")
    try:
        window.open(session)
    except Exception:
        # A half-opened window must not outlive the failure.
        try:
            window.close()
        except Exception as error:  # pylint: disable=broad-except
            _debug("closing a half-opened window", error)
        raise
    return window


def _open_surface(session, heading, detailed):
    """``(surface, is_window)``: the window when wanted and possible, else the
    corner indicator (``surface`` None when neither opens)."""
    if detailed:
        try:
            return _open_window(session), True
        except Exception as exc:  # pylint: disable=broad-except
            xbmc.log(
                "{}: search progress window unavailable, using Kodi's "
                "progress indicator: {}".format(search_progress.LOG_TAG, exc),
                xbmc.LOGWARNING,
            )
    try:
        indicator = CornerIndicator(heading)
        indicator.open(session)
        return indicator, False
    except Exception:  # pylint: disable=broad-except
        return None, False


def open_progress(heading, detailed=True):
    """Start showing a search and return its ``Session``.

    Call it, and the session's ``close``, on the add-on's own thread. The
    session is made the one ``search_progress``'s module-level calls report
    to until it is closed. A search is never stopped by a window that will
    not open: the session is returned either way.
    """
    try:
        texts = search_progress.load_texts()
    except Exception:  # pylint: disable=broad-except
        texts = {}
    session = search_progress.Session(texts)
    session.on_close(lambda: search_progress.deactivate(session))
    surface, session.detailed = _open_surface(session, heading, detailed)
    if surface is not None:
        painter = _Painter(session, surface)
        session.on_close(painter.close)
        try:
            painter.start()
        except Exception as error:  # pylint: disable=broad-except
            # No thread to draw with: leave the first frame up until close.
            _debug("starting the drawing thread", error)
    return search_progress.activate(session)
