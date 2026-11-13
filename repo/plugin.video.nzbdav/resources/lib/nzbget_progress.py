# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What the NZBGet download dialog says while a release downloads.

A download takes minutes, and the dialog used to say only "Downloading... 43%"
or "Post-processing...". These helpers turn what the poll already knows into
up to three lines:

    Copy 2 of 12 - downloading 43%
    4.5 of 10.4 GB - 62 MB/s - about 1m 35s left
    Copy 1 failed: too many missing articles

The first line names the copy being followed (the pick is copy 1; every
failover to a backup counts one up) and the stage NZBGet reports. The second
appears while it downloads and the sizes are known. The third appears once a
copy has failed, so a switch to a backup is never silent.

Everything here is presentation: it reads the poll ``state`` and never changes
what the poll does. The speed is worked out from the progress between polls,
so no extra NZBGet call is made.
"""

import time

from resources.lib.i18n import fmt, string

# NZBGet ``listgroups`` statuses -> the stage shown on the first line.
_STAGE_STRINGS = {
    "QUEUED": 30640,
    "PAUSED": 30640,
    "PP_QUEUED": 30641,
    "LOADING_PARS": 30641,
    "VERIFYING_SOURCES": 30641,
    "VERIFYING_REPAIRED": 30641,
    "REPAIRING": 30642,
    "RENAMING": 30643,
    "UNPACKING": 30643,
    "MOVING": 30644,
    "EXECUTING_SCRIPT": 30645,
    "PP_FINISHED": 30645,
}
_DOWNLOADING = frozenset(("DOWNLOADING", "FETCHING"))

# NZBGet history statuses -> why a copy failed. The part after the slash
# decides; "DELETED/HEALTH" and "FAILURE/HEALTH" read the same. Pairs, not a
# dict literal: a scanner reads a "PASSWORD" key beside a value as a secret.
_REASON_STRINGS = dict(
    (
        ("HEALTH", 30654),
        ("PAR", 30655),
        ("REPAIRABLE", 30655),
        ("UNPACK", 30656),
        ("PASSWORD", 30657),
        ("DISK", 30658),
        ("SPACE", 30658),
        ("DUPE", 30659),
        ("COPY", 30659),
        ("DAMAGED", 30660),
        ("BAD", 30660),
        ("MOVE", 30662),
        ("FETCH", 30663),
        ("SCAN", 30663),
        ("MANUAL", 30664),
    )
)
_GENERIC_REASON = 30661

# The speed is averaged over the samples of about this many seconds.
_RATE_WINDOW = 30.0
_MB_PER_GB = 1024.0


def record_failure(state, status):
    """Remember that the copy being followed failed, and why."""
    failed = state.setdefault("failed", [])
    failed.append((copy_number(state), str(status or "")))


def next_copy(state):
    """A backup took over: count the copy up and forget the old speed."""
    state["copy_number"] = copy_number(state) + 1
    state.pop("rate_samples", None)


def copy_number(state):
    """The copy being followed: 1 for the pick, +1 per failover."""
    number = state.get("copy_number", 1)
    return number if isinstance(number, int) and number > 0 else 1


def total_copies(fleet):
    """How many copies this download sent to NZBGet, or 0 when unknown."""
    owned = (fleet or {}).get("owned_nzbids")
    if owned is None:
        return 0
    try:
        return len(list(owned() or []))
    except Exception:  # pylint: disable=broad-except
        return 0


def failure_reason(status):
    """A short reason for an NZBGet history status ("FAILURE/HEALTH")."""
    detail = str(status or "").upper().rsplit("/", 1)[-1]
    msg_id = _REASON_STRINGS.get(detail)
    if msg_id is not None:
        return string(msg_id)
    return fmt(_GENERIC_REASON, str(status or "").upper() or "FAILURE")


def _copy_label(state, fleet):
    number = copy_number(state)
    total = total_copies(fleet)
    if total >= number and total > 1:
        return fmt(30637, number, total)
    return fmt(30638, number)


def _stage(group):
    status = str(group.get("status") or "").upper()
    if status in _DOWNLOADING:
        return fmt(30639, group.get("percent", 0))
    return string(_STAGE_STRINGS.get(status, 30645))


def _megabytes(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def _rate(state, downloaded, now):
    """MB per second over the recent polls, or 0.0 with too little to go on."""
    samples = state.setdefault("rate_samples", [])
    if samples and downloaded < samples[-1][1]:
        # The counter went back: another job, or NZBGet restarted it.
        del samples[:]
    samples.append((now, downloaded))
    while len(samples) > 2 and now - samples[0][0] > _RATE_WINDOW:
        samples.pop(0)
    elapsed = now - samples[0][0]
    if elapsed <= 0:
        return 0.0
    return max(downloaded - samples[0][1], 0.0) / elapsed


def _duration(seconds):
    """ "45s", "1m 35s", "2h 5m"."""
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return "{}m {}s".format(minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    return "{}h {}m".format(hours, minutes)


def _size_line(state, group, now):
    """ "4.5 of 10.4 GB - 62 MB/s - about 1m 35s left", or "" when unknown."""
    total = _megabytes(group.get("total_mb"))
    downloaded = min(_megabytes(group.get("downloaded_mb")), total)
    if not total:
        return ""
    parts = [
        fmt(
            30646,
            "{:.1f}".format(downloaded / _MB_PER_GB),
            "{:.1f}".format(total / _MB_PER_GB),
        )
    ]
    if str(group.get("status") or "").upper() in _DOWNLOADING:
        rate = _rate(state, downloaded, now)
        if rate >= 0.05:
            parts.append(fmt(30647, "{:.1f}".format(rate) if rate < 10 else int(rate)))
            parts.append(fmt(30648, _duration((total - downloaded) / rate)))
    return " - ".join(parts)


def failed_line(state):
    """ "Copy 1 failed: ...", or the count and last reason, or ""."""
    failed = state.get("failed") or []
    if not failed:
        return ""
    number, status = failed[-1]
    reason = failure_reason(status)
    if len(failed) == 1:
        return fmt(30649, number, reason)
    return fmt(30650, len(failed), reason)


def progress_message(state, group, fleet=None, now=None):
    """The dialog text for a copy that is in NZBGet's queue."""
    now = time.monotonic() if now is None else now
    lines = [
        "{} - {}".format(_copy_label(state, fleet), _stage(group)),
        _size_line(state, group, now),
        failed_line(state),
    ]
    return "[CR]".join(line for line in lines if line)


def waiting_message(state):
    """The dialog text between copies: why the last one failed, then waiting.

    Empty when no copy has failed, so the caller keeps its own text for the
    short gap between a job leaving the queue and reaching the history.
    """
    line = failed_line(state)
    if not line:
        return ""
    return "{}[CR]{}".format(line, string(30651))


def ready_toast(copies, health=None):
    """The short toast once NZBGet accepts the copies.

    No brackets and no commas: the notification call replaces both with
    look-alike characters most skin fonts cannot draw, and the toast is
    narrow, so the long sentence lost its last characters ("(100% health)"
    showed as "(10").
    """
    if not _is_count(copies) or copies < 1:
        label = string(30666)
    elif copies == 1:
        label = string(30653)
    else:
        label = fmt(30652, copies)
    if _is_count(health) and health >= 0:
        return fmt(30665, label, int(health))
    return label


def _is_count(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)
