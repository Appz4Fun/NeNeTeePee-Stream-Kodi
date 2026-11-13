# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The NZBGet download dialog: the add-on's own window, text only.

Kodi's progress dialog has room for three or four lines and a bar. The
download has more to say than that (the copy and its stage, size and speed, a
failed copy, missing articles, parity, backups, what was not sent), and the
first line already carries the percent, so this window drops the bar and
shows up to eight lines.

It is driven like ``xbmcgui.DialogProgress``: ``create``, ``update``,
``iscanceled``, ``close``. ``open_dialog`` returns Kodi's own dialog instead
when the window cannot be opened, so a skin or Kodi build that refuses it
never stops a download.

Cancel is read from a Home window property the window's Cancel button sets
(on a click and on Back). That is a plain read, so it is seen at once from
any thread; a script callback would only arrive when the polling thread next
waits. ``onAction`` and ``onClick`` set the same flag for the keys the
property does not cover.
"""

import os

import xbmc
import xbmcgui

WINDOW_XML = "download-dialog.xml"
# How many lines the window has room for (``nzbget_progress`` asks).
MAX_LINES = 8
CANCEL_BUTTON_ID = 10
CANCEL_PROPERTY = "nzbdav.download.cancel"
_HOME_WINDOW = 10000
_ACTION_PREVIOUS_MENU = 10
_ACTION_STOP = 13
_ACTION_NAV_BACK = 92
_CANCEL_ACTIONS = frozenset((_ACTION_PREVIOUS_MENU, _ACTION_STOP, _ACTION_NAV_BACK))
_LINE_BREAK = "[CR]"


def split_message(message):
    """``(headline, body)``: the first line, and the rest up to ``MAX_LINES``."""
    lines = [
        line.strip()
        for line in str(message or "").replace("\n", _LINE_BREAK).split(_LINE_BREAK)
    ]
    lines = [line for line in lines if line][:MAX_LINES]
    if not lines:
        return "", ""
    return lines[0], _LINE_BREAK.join(lines[1:])


def _home():
    return xbmcgui.Window(_HOME_WINDOW)


class DownloadDialog(xbmcgui.WindowXMLDialog):
    """A text-only download dialog with the ``DialogProgress`` calls."""

    max_lines = MAX_LINES

    def __init__(self, *args, **kwargs):
        del kwargs
        super().__init__(*args)
        self._canceled = False
        self._shown = False

    def create(self, heading, message=""):
        """Show the window with ``heading`` and the first ``message``."""
        _home().clearProperty(CANCEL_PROPERTY)
        self._canceled = False
        self.setProperty("heading", str(heading or ""))
        self._set_message(message)
        self.show()
        self._shown = True

    def update(self, percent, message=None):
        """Replace the text. ``percent`` is not drawn: the text carries it."""
        del percent
        if message is not None:
            self._set_message(message)

    def _set_message(self, message):
        headline, body = split_message(message)
        self.setProperty("headline", headline)
        self.setProperty("body", body)

    def iscanceled(self):
        """True once Cancel or Back was pressed."""
        if self._canceled:
            return True
        if _home().getProperty(CANCEL_PROPERTY) == "1":
            self._canceled = True
        return self._canceled

    def close(self):
        """Close the window and forget the cancel mark."""
        try:
            if self._shown:
                self._shown = False
                super().close()
        finally:
            _home().clearProperty(CANCEL_PROPERTY)

    def onAction(self, action):  # pylint: disable=invalid-name
        """Back, Escape and Stop cancel; nothing else closes the window."""
        if action.getId() in _CANCEL_ACTIONS:
            self._canceled = True

    def onClick(self, control_id):  # pylint: disable=invalid-name
        """A click on Cancel."""
        if control_id == CANCEL_BUTTON_ID:
            self._canceled = True


def _addon_path():
    """The add-on's folder, from this file's place in it.

    Not the Kodi add-on info API: that lookup can crash CoreELEC inside the
    TMDBHelper RunScript context (see ``cache._get_cache_dir``).
    """
    lib = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(lib))


def open_dialog(heading, message=""):
    """Open the download dialog and return it.

    The add-on's own window when it opens, otherwise Kodi's progress dialog,
    which takes the same calls and shows the first lines.
    """
    try:
        dialog = DownloadDialog(WINDOW_XML, _addon_path(), "Default", "1080i")
        dialog.create(heading, message)
        return dialog
    except Exception as exc:  # pylint: disable=broad-except
        xbmc.log(
            "NeNeTeePee-Stream-Kodi: download dialog unavailable, using "
            "Kodi's progress dialog: {}".format(exc),
            xbmc.LOGWARNING,
        )
    dialog = xbmcgui.DialogProgress()
    dialog.create(heading, message)
    return dialog
