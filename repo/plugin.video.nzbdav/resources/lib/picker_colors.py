# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The picker's colors for a release's resolution and source.

Shared by the rows of the picker (``results_dialog``) and by the NFO and
MediaInfo viewer (``nfo_viewer``), which shows the same attributes in the
same colors. Each is "AARRGGBB", as Kodi's ``[COLOR]`` takes it.
"""

RES_COLORS = {
    "4320p": "FFA78BFA",
    "2160p": "FFA78BFA",
    "1440p": "FF60A5FA",
    "1080p": "FF60A5FA",
    "720p": "FF4ADE80",
    "480p": "FFFBBF24",
}

SRC_COLORS = {
    "BluRay REMUX": "FF60A5FA",
    "REMUX": "FF60A5FA",
    "BluRay": "FF4ADE80",
    "WEB-DL": "FFC084FC",
    "WEBRip": "FFF0ABFC",
    "HDTV": "FFFDE68A",
}
