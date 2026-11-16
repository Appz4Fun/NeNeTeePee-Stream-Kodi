# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A fingerprint of the add-on's code, for what is stored on disk by it.

Parses the search cache and the parsed-names store keep must not outlive
the code that made them. The fingerprint is the name, size and
modification time of ``addon.xml`` and every Python file under
``resources/lib``, so any install or update changes it.
"""

import hashlib
import os

_STAMP = []


def code_stamp():
    """The fingerprint, worked out once per run, or None if it cannot be read."""
    if not _STAMP:
        _STAMP.append(_compute())
    return _STAMP[0]


def _compute():
    lib = os.path.dirname(os.path.abspath(__file__))
    addon_xml = os.path.join(os.path.dirname(os.path.dirname(lib)), "addon.xml")
    seen = []
    try:
        paths = [addon_xml]
        for folder, _dirs, names in os.walk(lib):
            paths.extend(
                os.path.join(folder, name) for name in names if name.endswith(".py")
            )
        for path in paths:
            stat = os.stat(path)
            seen.append((os.path.relpath(path, lib), stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError):
        return None
    return hashlib.sha256(repr(sorted(seen)).encode("utf-8")).hexdigest()[:20]
