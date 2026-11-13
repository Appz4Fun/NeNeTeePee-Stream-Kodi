# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What the NZBGet dialogs say while copies are collected, sent and downloaded.

While the copies are handed to NZBGet (its health check can take most of a
minute) the dialog says what is being sent and what was left out:

    Sending 6 NZBs to NZBGet...
    abNZB, altHUB, Ninjacentral, Square Eyed, Zurg x2
    Not sent: Digital Carnage refused 1, 1 duplicate upload

While a copy downloads it shows up to four lines:

    Copy 2 of 12 (abNZB, 4 days) - downloading 43%
    4.5 of 10.4 GB - 62 MB/s - about 1m 35s left
    Copy 1 failed: too many missing articles
    155 articles missing - 155 recovered from backups

The first line names the copy being followed (the pick is copy 1; every
failover to a backup counts one up), the indexer that supplied it and its
age, and the stage NZBGet reports. The second line is the size, speed and
time left while it downloads; after the download it is how long the stage has
run and, when the server gives the stage's progress, how long is left
("25s so far - about 33s left"), so a long repair, unpack, move or script is
not mistaken for a hang.

Up to two more lines follow, the most important first: the most recent copy
that failed; the articles no server had and what the server recovered for
them from a twin or an alt or by stream repair; what the backups are (a twin
is a byte-identical copy the server can borrow missing articles from, an alt
is another upload kept as a fallback); and what was never sent.

Everything here is presentation: it reads the poll ``state`` and the fleet's
``FleetReport`` and never changes what the poll does. The speed is worked out
from the progress between polls, so no extra NZBGet call is made.
"""

import time

from resources.lib.http_util import calculate_age
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
    "EXECUTING_SCRIPT": 30679,
    "STREAM_REPAIRING": 30680,
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


class FleetReport:
    """What happened to each copy while one play collected its NZBs.

    Filled in by the collector (kept, dropped, duplicate) and read by the
    dialog text below. ``blocked`` is the play's set of indexers that refused
    an NZB (``FleetDedup.blocked_indexers``): a dropped copy from one of them
    is reported as refused by that indexer, any other as not downloaded.
    """

    def __init__(self, blocked=None):
        self.sent = []  # indexer name per kept copy, in the order kept
        self._rows = []  # the kept rows; each gets ``_nzbid`` once accepted
        self.refused = {}  # indexer name -> copies dropped after it refused
        self.failed = 0  # copies dropped for any other reason
        self.duplicates = 0  # copies that were the same posting as a kept one
        self._blocked = blocked if blocked is not None else set()

    @staticmethod
    def _indexer(row):
        return str((row or {}).get("indexer") or "").strip()

    def kept(self, row):
        # The listing that supplied the NZB, when a mirror stood in for the
        # release's first listing (``nzbget_fleet_dedup.SUPPLIED_BY``).
        supplier = str((row or {}).get("_supplied_by") or "").strip()
        self.sent.append(supplier or self._indexer(row))
        if isinstance(row, dict):
            self._rows.append(row)

    def copy_source(self, nzbid):
        """ "abNZB, 4 days" for the kept copy NZBGet knows as ``nzbid``, or "".

        The indexer that supplied the copy and how long ago it was posted.
        Empty for a job this play did not send (an adopted download).
        """
        if nzbid is None:
            return ""
        for row, name in zip(self._rows, self.sent):
            if str(row.get("_nzbid")) == str(nzbid):
                age = calculate_age(row.get("pubdate") or "") or row.get("age") or ""
                return ", ".join(part for part in (name, str(age)) if part)
        return ""

    def duplicate(self):
        self.duplicates += 1

    def dropped(self, row):
        name = self._indexer(row)
        if name and name.lower() in self._blocked:
            self.refused[name] = self.refused.get(name, 0) + 1
        else:
            self.failed += 1

    def not_sent(self):
        """How many copies were found but not kept."""
        return sum(self.refused.values()) + self.failed + self.duplicates

    def found(self):
        return len(self.sent) + self.not_sent()


# A backup NZBGet holds in history and can still promote on a failover.
_PARKED_STATUS = "DELETED/DUPE"

# The indexer line stops adding names past this many characters.
_INDEXER_LINE_WIDTH = 64


def indexers_line(report):
    """ "abNZB, altHUB, Zurg x2": where the kept copies came from, or ""."""
    counts = {}
    for name in getattr(report, "sent", None) or []:
        if name:
            counts[name] = counts.get(name, 0) + 1
    labels = [
        fmt(30667, name, count) if count > 1 else name
        for name, count in sorted(counts.items(), key=lambda item: item[0].lower())
    ]
    shown = []
    for index, label in enumerate(labels):
        if shown and len(", ".join(shown + [label])) > _INDEXER_LINE_WIDTH:
            shown.append(fmt(30489, len(labels) - index))
            break
        shown.append(label)
    return ", ".join(shown)


def not_sent_line(report):
    """ "Not sent: Digital Carnage refused 1, 1 duplicate upload", or ""."""
    if not isinstance(report, FleetReport) or not report.not_sent():
        return ""
    parts = [
        fmt(30669, name, count)
        for name, count in sorted(report.refused.items(), key=lambda i: i[0].lower())
    ]
    if report.failed:
        parts.append(fmt(30670, report.failed))
    if report.duplicates:
        parts.append(_counted(report.duplicates, 30671, 30672))
    return fmt(30668, ", ".join(parts))


def sending_message(report, total):
    """The dialog text while the copies are handed to NZBGet."""
    lines = [fmt(30617, total), indexers_line(report), not_sent_line(report)]
    return "[CR]".join(line for line in lines if line)


def backup_kinds(rows, owned, current):
    """Count the owned backups NZBGet marked as twins or alts.

    ``rows`` are history rows; a server that sets no ``DupeKind`` parameter
    (stock NZBGet) yields ``{}``. Only a parked backup counts: a
    ``DELETED/DUPE`` row the server can still promote. ``current`` (the copy
    being downloaded) is not a backup, and a copy that was promoted and failed
    keeps its mark but is no longer one.
    """
    wanted = {str(nzbid) for nzbid in owned or [] if str(nzbid) != str(current)}
    kinds = {}
    for row in rows or []:
        if not isinstance(row, dict) or str(row.get("NZBID")) not in wanted:
            continue
        if str(row.get("Status") or "").upper() != _PARKED_STATUS:
            continue
        for parameter in row.get("Parameters") or []:
            if isinstance(parameter, dict) and parameter.get("Name") == "DupeKind":
                kind = str(parameter.get("Value") or "").lower()
                key = "twin" if kind.startswith("twin") else "alt" if kind else ""
                if key:
                    kinds[key] = kinds.get(key, 0) + 1
    return kinds


def kinds_line(kinds):
    """ "Backups: 2 twins, 3 alts", or "" when the server did not say."""
    parts = []
    if (kinds or {}).get("twin"):
        parts.append(_counted(kinds["twin"], 30674, 30675))
    if (kinds or {}).get("alt"):
        parts.append(_counted(kinds["alt"], 30676, 30677))
    return fmt(30673, ", ".join(parts)) if parts else ""


def _counted(count, one_id, many_id):
    return string(one_id) if count == 1 else fmt(many_id, count)


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
        label = fmt(30637, number, total)
    else:
        label = fmt(30638, number)
    report = (fleet or {}).get("report")
    source = (
        report.copy_source(state.get("current"))
        if isinstance(report, FleetReport)
        else ""
    )
    return fmt(30681, label, source) if source else label


# A stage's running time is shown once it has lasted this long.
_STAGE_TIME_FROM = 5


def _stage(group):
    """ "downloading 43%", "unpacking 43%", "moving into place"."""
    status = str(group.get("status") or "").upper()
    if status in _DOWNLOADING:
        return fmt(30639, group.get("percent", 0))
    label = string(_STAGE_STRINGS.get(status, 30645))
    percent = stage_percent(group)
    return label if percent is None else "{} {}%".format(label, percent)


def _stage_time_line(group):
    """ "25s so far - about 33s left" for a post-processing stage, or "".

    The time left is worked out from the stage's own progress, so it only
    appears for a stage NZBGet reports a percent for (a repair, an unpack).
    """
    if str(group.get("status") or "").upper() in _DOWNLOADING:
        return ""
    seconds = _megabytes(group.get("post_seconds"))
    if seconds < _STAGE_TIME_FROM:
        return ""
    parts = [fmt(30687, _duration(seconds))]
    percent = stage_percent(group)
    if percent is not None:
        parts.append(fmt(30648, _duration(seconds * (100 - percent) / percent)))
    return " - ".join(parts)


def health_line(group):
    """ "155 articles missing - 155 recovered from backups", or "".

    What no server had, and what the server got back from the release's other
    copies: whole articles from a twin or alt, or bytes rebuilt by stream
    repair. Nothing on a clean download.
    """
    missing = int(_megabytes(group.get("failed_articles")))
    recovered = int(_megabytes(group.get("recovered_articles")))
    rebuilt_mb = int(_megabytes(group.get("recovered_mb")))
    parts = []
    if missing:
        parts.append(_counted(missing, 30682, 30683))
    if recovered:
        parts.append(fmt(30684, recovered))
    elif rebuilt_mb:
        parts.append(fmt(30685, rebuilt_mb))
    if group.get("live_repairing") is True:
        parts.append(string(30686))
    return " - ".join(parts)


def stage_percent(group):
    """The post-processing stage's own percent (1 to 99), or None.

    None while downloading, and for a stage NZBGet gives no progress for (a
    move, a script): the bar then keeps the download's 100%.
    """
    if str(group.get("status") or "").upper() in _DOWNLOADING:
        return None
    percent = _megabytes(group.get("post_percent"))
    return int(percent) if 1 <= percent <= 99 else None


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


# The dialog shows at most this many lines; beyond the copy line and the
# size or time line, the most important of the rest are kept.
_MAX_LINES = 4


def progress_message(state, group, fleet=None, now=None):
    """The dialog text for a copy that is in NZBGet's queue."""
    now = time.monotonic() if now is None else now
    downloading = str(group.get("status") or "").upper() in _DOWNLOADING
    head = [
        "{} - {}".format(_copy_label(state, fleet), _stage(group)),
        # While it downloads: size, speed, time left. After: how long the
        # stage has run and, when NZBGet gives its progress, how long is left.
        _size_line(state, group, now) if downloading else _stage_time_line(group),
    ]
    # In order of importance: a copy that failed, articles that were missing,
    # what the backups are, what was never sent.
    rest = [
        failed_line(state),
        health_line(group),
        kinds_line(state.get("backup_kinds")),
        not_sent_line((fleet or {}).get("report")),
    ]
    lines = [line for line in head if line]
    lines += [line for line in rest if line][: _MAX_LINES - len(lines)]
    return "[CR]".join(lines)


def waiting_message(state):
    """The dialog text between copies: why the last one failed, then waiting.

    Empty when no copy has failed, so the caller keeps its own text for the
    short gap between a job leaving the queue and reaching the history.
    """
    line = failed_line(state)
    if not line:
        return ""
    return "{}[CR]{}".format(line, string(30651))


def ready_toast(copies, health=None, report=None):
    """The short toast once NZBGet accepts the copies.

    No brackets and no commas: the notification call replaces both with
    look-alike characters most skin fonts cannot draw, and the toast is
    narrow, so the long sentence lost its last characters ("(100% health)"
    showed as "(10"). When fewer copies are usable than were found (one
    refused, one a duplicate, one dead on the server) it says so:
    "6 of 8 copies ready".
    """
    found = report.found() if isinstance(report, FleetReport) else 0
    if not _is_count(copies) or copies < 1:
        label = string(30666)
    elif found > copies:
        label = fmt(30678, copies, found)
    elif copies == 1:
        label = string(30653)
    else:
        label = fmt(30652, copies)
    if _is_count(health) and health >= 0:
        return fmt(30665, label, int(health))
    return label


def _is_count(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)
