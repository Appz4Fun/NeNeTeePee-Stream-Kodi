# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The post details an indexer sends beside a search result.

A newznab item carries more than its title, link and size. Indexers add
``newznab:attr`` elements, and NZBHydra2 passes every one of them through:

* ``usenetdate``: when the release was posted to Usenet. The item's
  ``pubDate`` is often when the indexer added it, hours or days later, and
  differs between indexers for one upload.
* ``poster``: who posted it.
* ``group``: the newsgroup.
* ``password``: 0 for none, 1 for a passworded archive, other values for
  "not known".
* ``files`` and ``grabs``: how many files the release has and how often its
  NZB was fetched.

The add-on uses the first four to tell whether two listings are the same
upload (``nzbget_fleet_dedup.same_listing``). Only the fields an indexer sent
are returned, so a result from one that sends none is unchanged.
"""

import re

# The row keys, which are the attribute names.
POST_DETAIL_KEYS = ("usenetdate", "poster", "group", "password", "files", "grabs")
_MAX_LENGTH = 256


def post_details(item):
    """``{key: value}`` for the post details on a newznab ``item`` element."""
    details = {}
    for attr in item.iter():
        tag = attr.tag
        if not isinstance(tag, str) or tag.rsplit("}", 1)[-1] != "attr":
            continue
        name = attr.get("name", "")
        if name not in POST_DETAIL_KEYS or name in details:
            continue
        value = str(attr.get("value", "") or "").strip()
        if value:
            details[name] = value[:_MAX_LENGTH]
    return details


_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def poster_address(row):
    """The poster's address in lower case, or "" when the row names none.

    Indexers write one poster differently ("name@host (Name)", "Name
    <name@host>"), so only the address is compared, and a poster with no
    address in it is not compared at all.
    """
    poster = row.get("poster") if isinstance(row, dict) else None
    if not isinstance(poster, str):
        return ""
    match = _ADDRESS.search(poster)
    return match.group(0).lower() if match else ""


def password_flag(row):
    """True for a passworded release, False for one without, None if unknown."""
    value = row.get("password") if isinstance(row, dict) else None
    value = str(value).strip() if value is not None else ""
    if value == "0":
        return False
    if value == "1":
        return True
    return None
