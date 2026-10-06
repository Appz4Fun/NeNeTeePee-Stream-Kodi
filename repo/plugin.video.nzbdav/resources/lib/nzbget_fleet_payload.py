# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Stream base64 fleet JSON to a private temporary file, without buffering bodies."""

import base64
import contextlib
import json
import os
import tempfile


@contextlib.contextmanager
def fleet_payload(params):
    """A mapping for small callers, or a seekable encoded JSON request for spools."""
    members = params[0]["Members"]
    paths = [row["ContentPath"] for row in members if "ContentPath" in row]
    if not paths:
        yield None
        return
    with tempfile.TemporaryFile(dir=os.path.dirname(paths[0])) as out:
        out.write(b'{"id": 1, "method": "appendfleet", "params": [')
        header = dict(params[0])
        del header["Members"]
        out.write(json.dumps(header).encode("utf-8")[:-1])
        out.write(b', "Members": [')
        for index, member in enumerate(members):
            if index:
                out.write(b", ")
            _write_member(out, member)
        out.write(b"]}] }")
        out.seek(0)
        yield out


def _write_member(out, member):
    """48 KiB chunks are divisible by three, so base64 padding appears only last."""
    path = member.get("ContentPath")
    if path is None:
        out.write(json.dumps(member).encode("utf-8"))
        return
    out.write(b'{"NZBFilename": ')
    out.write(json.dumps(member["NZBFilename"]).encode("utf-8"))
    out.write(b', "Content": "')
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(48 * 1024), b""):
            out.write(base64.b64encode(chunk))
    out.write(b'"}')
