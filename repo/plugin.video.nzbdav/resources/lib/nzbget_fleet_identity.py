# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Unambiguous fleet response names, without changing displayed release identity."""

import re

_TAG = re.compile(r"^(.*) \[NZBDAV copy [0-9]+\](?:\.nzb)?$", re.DOTALL)


def member_filename(row, number):
    """A distinct name even when several indexers report the identical title."""
    return "{} [NZBDAV copy {:02d}].nzb".format(
        row.get("title") or "submission", number
    )


def release_name(name):
    """Remove only our copy label when exposing history to the picker/player."""
    name = str(name or "")
    match = _TAG.match(name)
    return match.group(1) if match else name


def record_members(reply, kept, dupe_key):
    """Use echoed names, never health-rank positions, to persist accepted links."""
    from resources.lib import nzbget_submit_ledger

    by_name = {}
    for number, (row, _handle, _token) in enumerate(kept, 1):
        name = member_filename(row, number)
        by_name[name.casefold()] = row
        by_name[name[:-4].casefold()] = row
    accepted = []
    mapped = []
    for member in reply.get("Members", []):
        if member.get("Status") not in ("QUEUED", "BACKUP"):
            continue
        name = str(member.get("Name") or member.get("NZBFilename") or "")
        row = by_name.get(name.casefold())
        nzbid = member.get("NZBID")
        if (
            row is None
            or not isinstance(nzbid, int)
            or isinstance(nzbid, bool)
            or nzbid <= 0
        ):
            continue
        row.update(_nzbid=nzbid, _submitted=True)
        accepted.append(row)
        mapped.append(nzbid)
    nzbget_submit_ledger.record(accepted, dupe_key)
    return mapped
