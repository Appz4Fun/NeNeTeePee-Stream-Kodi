# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What a search is doing, stage by stage, for the search progress window.

Between pressing play and the list of releases the add-on works out which
title was asked for, searches, hears back from each indexer, reads the NFO or
MediaInfo of the top releases and sorts the list. The code that does each of
those tells this module what happened; a ``Session`` keeps it as five stages
and a table of indexers, and ``search_dialog`` draws it.

Every module-level call here is safe from any thread, never raises, and does
nothing when no search is being shown: the search itself never depends on the
window. A ``Session`` only holds text and counts. It makes no Kodi GUI or
settings call, so worker threads may report to it freely; its strings are
read once, on the add-on's own thread, by ``load_texts``.
"""

import threading
import time
from urllib.parse import urlparse

import xbmc

IDENTIFY = "identify"
SEARCH = "search"
INDEXERS = "indexers"
DETAILS = "details"
PREPARE = "prepare"
STAGES = (IDENTIFY, SEARCH, INDEXERS, DETAILS, PREPARE)

# Stage states.
PENDING = "pending"
ACTIVE = "active"
DONE = "done"
WARN = "warn"
SKIPPED = "skipped"

# How an indexer answered the search.
WAITING = "waiting"
OK = "ok"
EMPTY = "empty"
FAILED = "failed"

# What an NFO or MediaInfo read came to.
MEDIAINFO = "mediainfo"
NFO = "nfo"
READ = "read"
NOTHING = "nothing"
REFUSED = "refused"

# Every line the add-on writes to kodi.log starts with this.
LOG_TAG = "NeNeTeePee-Stream-Kodi"
# How the search code names the direct-indexer provider; shown translated.
DIRECT_PROVIDER = "direct indexers"

TEXT_IDS = {
    "heading": 30710,
    "stage_identify": 30711,
    "stage_search": 30712,
    "stage_indexers": 30713,
    "stage_details": 30714,
    "stage_prepare": 30715,
    "table_heading": 30716,
    # 30709 ("Cancel") is used by the window's XML.
    "log_hint": 30718,
    "episode": 30719,
    "season": 30720,
    "looked_up": 30721,
    "no_ids": 30722,
    "reading_title": 30723,
    "waiting": 30724,
    "saved_moments": 30725,
    "saved_minutes": 30726,
    "saved_hours": 30727,
    "retry_title": 30728,
    "results_from": 30729,
    "results_total": 30730,
    "no_results": 30731,
    "search_failed": 30732,
    "provider_failed": 30733,
    "indexers_answered": 30734,
    "indexers_from_rows": 30735,
    "indexers_failed": 30736,
    "and_more": 30737,
    "row_waiting": 30738,
    "row_results": 30739,
    "row_one_result": 30740,
    "row_no_results": 30741,
    "row_failed": 30742,
    "row_failed_plain": 30743,
    "details_ranking": 30744,
    "details_checking": 30745,
    "details_checking_one": 30746,
    "details_asking": 30747,
    "details_off": 30748,
    "details_known": 30749,
    "details_mediainfo": 30750,
    "details_nfo": 30751,
    "details_none": 30752,
    "details_refused": 30753,
    "details_failed": 30754,
    "details_waiting": 30755,
    "media_read": 30756,
    "media_nothing": 30757,
    "media_refused": 30758,
    "media_failed": 30759,
    "sorting": 30760,
    "checking_downloads": 30761,
    "ready": 30762,
    "opening": 30763,
    "best_match": 30764,
    "table_empty": 30765,
    "direct_indexers": 30769,
}

_STAGE_NAMES = {
    IDENTIFY: "stage_identify",
    SEARCH: "stage_search",
    INDEXERS: "stage_indexers",
    DETAILS: "stage_details",
    PREPARE: "stage_prepare",
}
_KIND_NAMES = {MEDIAINFO: "MediaInfo", NFO: "NFO"}
_MEDIA_TEXTS = {
    READ: "media_read",
    NOTHING: "media_nothing",
    REFUSED: "media_refused",
    FAILED: "media_failed",
}
# An indexer read for several releases shows its best outcome per kind in the
# table; the stage still names it for any refusal or failure.
_MEDIA_RANK = {READ: 3, NOTHING: 2, REFUSED: 1, FAILED: 0}
_INDEXER_ORDER = {OK: 0, WAITING: 1, FAILED: 2, EMPTY: 3}

_LINE_BREAK = "[CR]"
_SEPARATOR = " · "
_MAX_LINES = 2
_MAX_NAMES = 4
_MAX_ERROR_CHARS = 72
# An active stage shows how long it has been running once that is worth
# knowing.
_ELAPSED_AFTER_SECONDS = 2
_HYDRA_ERROR_MARKER = "Server returned:"


def load_texts():
    """``{name: text}`` for a session, from Kodi's string table.

    Call it on the add-on's own thread: a ``Session`` formats these from any
    thread and never touches Kodi's add-on API itself.
    """
    from resources.lib.i18n import string

    return {name: string(msg_id) for name, msg_id in TEXT_IDS.items()}


def _default_text(name):
    from resources.lib.i18n import _FALLBACK_STRINGS

    return _FALLBACK_STRINGS.get(TEXT_IDS.get(name), "")


def short_error(message):
    """One short clause from an indexer's or a provider's error message."""
    from resources.lib.http_util import redact_text

    text = " ".join(redact_text(str(message or "")).split())
    if _HYDRA_ERROR_MARKER in text:
        text = text.split(_HYDRA_ERROR_MARKER, 1)[1].strip()
    text = text.rstrip(". ")
    if len(text) > _MAX_ERROR_CHARS:
        text = text[: _MAX_ERROR_CHARS - 1].rstrip() + "…"
    return text


def host_label(host):
    """A readable name for an indexer known only by its host."""
    host = str(host or "").strip().lower()
    for prefix in ("api.", "www."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def _host_of(link):
    try:
        return (urlparse(str(link or "")).hostname or "").lower()
    except ValueError:
        return ""


class _Stage:  # pylint: disable=too-few-public-methods
    """One stage: its state, its lines and how long it has been active."""

    def __init__(self):
        self.state = PENDING
        self.lines = []
        self.started = None
        self.seconds = 0


class Session:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The stages and indexer table of one search, safe from any thread."""

    def __init__(self, texts=None, clock=time.monotonic):
        self._lock = threading.RLock()
        self._texts = dict(texts or {})
        self._clock = clock
        self._opened = clock()
        self._stages = {key: _Stage() for key in STAGES}
        self._subject = ""
        self._id_rows = []
        self._indexers = {}
        self._hosts = {}
        self._providers = []
        self._waiting_for = ""
        self._result_total = None
        self._asked = []
        self._details_told = False
        self._version = 0
        self._cancelled = False
        self._search_over = False
        self._closers = []
        self._closed = False
        # True while the add-on's own window shows the indexer table; the
        # NZBHydra2 history is read only then (see ``hydra_history``).
        self.detailed = False

    # --- plumbing -----------------------------------------------------------

    def text(self, name, *args):
        """The string ``name``, formatted; never raises on a bad template."""
        template = self._texts.get(name) or _default_text(name)
        if not args:
            return template
        try:
            return template.format(*args)
        except (IndexError, KeyError, ValueError):
            return "{} {}".format(template, " ".join(str(arg) for arg in args))

    def _provider(self, name):
        """A search provider's name as shown."""
        name = str(name or "")
        return self.text("direct_indexers") if name == DIRECT_PROVIDER else name

    def _names(self, names):
        """ "A, B, C, D and 2 more" for a list of indexer names."""
        names = [name for name in names if name]
        shown = ", ".join(names[:_MAX_NAMES])
        if len(names) > _MAX_NAMES:
            shown = self.text("and_more", shown, len(names) - _MAX_NAMES)
        return shown

    def _set(self, key, state, *lines):
        lines = [str(line) for line in lines if line][:_MAX_LINES]
        with self._lock:
            stage = self._stages[key]
            now = self._clock()
            if state == ACTIVE and stage.state != ACTIVE:
                stage.started = now
            elif state != ACTIVE and stage.state == ACTIVE:
                stage.seconds = now - (stage.started or now)
            changed = stage.state != state or stage.lines != lines
            stage.state = state
            stage.lines = lines
            self._version += 1
        if changed:
            self._log(key, state, lines)

    def _log(self, key, state, lines):
        xbmc.log(
            "{}: Search progress: {} [{}] {}".format(
                LOG_TAG, self.text(_STAGE_NAMES[key]), state, " | ".join(lines)
            ),
            xbmc.LOGINFO,
        )

    def _state(self, key):
        with self._lock:
            return self._stages[key].state

    # --- the window's side --------------------------------------------------

    def on_close(self, closer):
        """Register what ``close`` shuts (the window and its thread)."""
        with self._lock:
            self._closers.append(closer)

    def close(self):
        """Shut the window, once. Later reports are kept but never shown."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            closers, self._closers = self._closers, []
        for closer in closers:
            try:
                closer()
            except Exception as error:  # pylint: disable=broad-except
                xbmc.log(
                    "{}: search progress close step failed: {}".format(LOG_TAG, error),
                    xbmc.LOGDEBUG,
                )

    def cancel(self):
        """Note that the viewer asked to stop."""
        with self._lock:
            self._cancelled = True
            self._version += 1

    def cancelled(self):
        """True once the viewer pressed Cancel or Back."""
        with self._lock:
            return self._cancelled

    # --- stage 1: which title -----------------------------------------------

    def identified(  # pylint: disable=too-many-arguments
        self,
        search_type,
        title,
        year="",
        season="",
        episode="",
        tmdb="",
        tvdb="",
        imdb="",
    ):
        """The title, season and episode asked for, and the ids that came."""
        subject = self._subject_text(search_type, title, year, season, episode)
        ids = [("TMDb", tmdb), ("TVDB", tvdb), ("IMDb", imdb)]
        with self._lock:
            self._subject = subject
            looked_up = {name for name, _value, was in self._id_rows if was}
            self._id_rows = [
                (name, str(value), name in looked_up) for name, value in ids if value
            ]
        self._set(
            IDENTIFY, ACTIVE, subject or self.text("reading_title"), self._ids_line()
        )

    def _subject_text(self, search_type, title, year, season, episode):
        title = str(title or "").strip()
        if search_type == "episode":
            if season and episode:
                return self.text("episode", title, season, episode).strip(" ·")
            if season:
                return self.text("season", title, season).strip(" ·")
            return title
        if title and year:
            return "{} ({})".format(title, year)
        return title

    def _ids_line(self):
        with self._lock:
            parts = [
                (
                    self.text("looked_up", "{} {}".format(name, value))
                    if looked_up
                    else "{} {}".format(name, value)
                )
                for name, value, looked_up in self._id_rows
            ]
        return _SEPARATOR.join(parts) if parts else self.text("no_ids")

    def ids_resolved(self, tvdb="", imdb=""):
        """Ids the add-on looked up itself; the title is now settled."""
        with self._lock:
            known = {name: value for name, value, _was in self._id_rows}
            for name, value in (("TVDB", tvdb), ("IMDb", imdb)):
                if value and str(value) != known.get(name):
                    self._id_rows = [row for row in self._id_rows if row[0] != name]
                    self._id_rows.append((name, str(value), True))
            order = {"TMDb": 0, "TVDB": 1, "IMDb": 2}
            self._id_rows.sort(key=lambda row: order.get(row[0], 9))
            subject = self._subject
        self._set(IDENTIFY, DONE, subject, self._ids_line())

    def _identify_done(self):
        if self._state(IDENTIFY) in (PENDING, ACTIVE):
            with self._lock:
                subject = self._subject
            self._set(IDENTIFY, DONE, subject, self._ids_line())

    # --- stage 2: the search ------------------------------------------------

    def search_cached(self, count, age_seconds=None):
        """The answer came from the add-on's saved searches."""
        self._identify_done()
        if age_seconds is None or age_seconds < 90:
            saved = self.text("saved_moments")
        elif age_seconds < 7200:
            saved = self.text("saved_minutes", int(round(age_seconds / 60.0)))
        else:
            saved = self.text("saved_hours", int(round(age_seconds / 3600.0)))
        self._set(SEARCH, DONE, saved, self.text("results_total", count))

    def search_started(self, providers):
        """A live search went out to ``providers`` (their names)."""
        self._identify_done()
        with self._lock:
            self._waiting_for = ", ".join(
                self._provider(name) for name in providers if name
            )
            waiting = self._waiting_for
        self._set(SEARCH, ACTIVE, self.text("waiting", waiting))

    def search_retry(self):
        """The id search found nothing; the title is being tried."""
        with self._lock:
            waiting = self._waiting_for
        if self._state(SEARCH) == ACTIVE:
            self._set(
                SEARCH, ACTIVE, self.text("waiting", waiting), self.text("retry_title")
            )

    def provider_finished(self, label, count, error=None):
        """One search provider answered, or failed."""
        with self._lock:
            self._providers.append((self._provider(label), int(count or 0), error))

    def search_finished(self, rows, error=None):
        """The search is over: ``rows`` are its results, ``error`` why none."""
        rows = rows or []
        self._identify_done()
        with self._lock:
            # A direct indexer that answers after the fan-out gave up on it
            # has its rows thrown away: its late report must change nothing.
            self._search_over = True
            providers = list(self._providers)
            self._result_total = len(rows)
        failures = [
            self.text("provider_failed", label, short_error(problem))
            for label, _count, problem in providers
            if problem
        ]
        if self._state(SEARCH) != DONE:
            answered = [label for label, _count, problem in providers if not problem]
            if error and not rows:
                self._set(SEARCH, WARN, self.text("search_failed"), short_error(error))
            elif not rows:
                self._set(
                    SEARCH, DONE, self.text("no_results"), _SEPARATOR.join(failures)
                )
            else:
                self._set(
                    SEARCH,
                    WARN if failures else DONE,
                    (
                        self.text("results_from", len(rows), ", ".join(answered))
                        if answered
                        else self.text("results_total", len(rows))
                    ),
                    _SEPARATOR.join(failures),
                )
        self._note_rows(rows)
        self._indexers_stage(final=True)

    # --- stage 3: the indexers ----------------------------------------------

    def indexers_expected(self, names):
        """Indexers that were just asked and have not answered yet."""
        with self._lock:
            if self._search_over:
                return
            for name in names:
                if name and name not in self._indexers:
                    self._indexers[name] = _indexer_row(name, WAITING)
            self._version += 1
            self._indexers_stage()

    def indexer_report(self, entries):
        """How indexers answered: dicts of name, ok, count, ms and error.

        Ignored once the search is over, and a row already reported as
        failed (a direct indexer the fan-out timed out) is not overwritten
        by that indexer's late answer: its rows were not used.
        """
        with self._lock:
            if self._search_over:
                return
            for entry in entries or ():
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                known = self._indexers.get(name)
                if known is not None and known["reported"] and known["state"] == FAILED:
                    continue
                count = int(entry.get("count") or 0)
                if not entry.get("ok", True):
                    state = FAILED
                else:
                    state = OK if count else EMPTY
                row = self._indexers.get(name) or _indexer_row(name, state)
                row.update(
                    state=state,
                    count=count,
                    ms=entry.get("ms"),
                    error=short_error(entry.get("error")) if state == FAILED else "",
                    reported=True,
                )
                self._indexers[name] = row
            self._version += 1
            self._indexers_stage()

    def _note_rows(self, rows):
        """Take the indexer names, hosts and counts the result rows carry."""
        counts = {}
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("indexer") or "").strip()
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + 1
                for field in ("nfo_url", "media_ref"):
                    host = _host_of(row.get(field))
                    if host:
                        self._hosts.setdefault(host, name)
            for name, count in counts.items():
                if name not in self._indexers:
                    self._indexers[name] = _indexer_row(name, OK, count=count)
                elif self._indexers[name]["state"] == WAITING:
                    self._indexers[name].update(state=OK, count=count)
            self._version += 1

    def _indexers_stage(self, final=False):
        with self._lock:
            rows = list(self._indexers.values())
            total = self._result_total
        if not rows:
            if final:
                self._set(INDEXERS, SKIPPED)
            return
        failed = [row["name"] for row in rows if row["state"] == FAILED]
        answered = [row for row in rows if row["state"] in (OK, EMPTY)]
        if total is None:
            total = sum(row["count"] or 0 for row in answered)
        if any(row["reported"] for row in rows) or len(answered) < len(rows):
            first = self.text("indexers_answered", len(answered), len(rows), total)
        else:
            first = self.text("indexers_from_rows", total, len(rows))
        second = self.text("indexers_failed", self._names(failed)) if failed else ""
        if final:
            state = WARN if failed else DONE
        else:
            state = ACTIVE
        self._set(INDEXERS, state, first, second)

    # --- stage 4: NFO and MediaInfo -----------------------------------------

    def ranking(self, count):
        """The results are being sorted to find the releases to read up on."""
        self._set(DETAILS, ACTIVE, self.text("details_ranking", count))

    def details_skipped(self, turned_off=False):
        """No release needs a read: turned off, or everything is known."""
        with self._lock:
            self._details_told = True
        if turned_off:
            self._set(DETAILS, SKIPPED, self.text("details_off"))
        else:
            self._set(DETAILS, DONE, self.text("details_known"))

    def details_started(self, releases, hosts):
        """``releases`` top releases are being read up on, at ``hosts``."""
        with self._lock:
            self._details_told = True
            self._asked = [host for host in hosts if host]
            names = [self._indexer_name(host) for host in self._asked]
        checking = (
            self.text("details_checking_one")
            if releases == 1
            else self.text("details_checking", releases)
        )
        self._set(
            DETAILS, ACTIVE, checking, self.text("details_asking", self._names(names))
        )

    def details_result(self, host, kind, outcome):
        """One indexer answered a MediaInfo or NFO read."""
        if kind not in _KIND_NAMES or outcome not in _MEDIA_TEXTS:
            return
        with self._lock:
            name = self._indexer_name(host)
            row = self._indexers.get(name)
            if row is None:
                row = _indexer_row(name, "")
                self._indexers[name] = row
            row["media"].setdefault(kind, set()).add(outcome)
            row["media_hosts"].add(str(host or "").lower())
            self._version += 1
            # Under the lock, so a read landing as the wait ends cannot turn
            # the finished stage back into a running one.
            if self._stages[DETAILS].state == ACTIVE:
                self._details_stage(ACTIVE)

    def details_finished(self):
        """The wait for the reads is over; slower ones finish unseen."""
        with self._lock:
            if self._stages[DETAILS].state == ACTIVE:
                self._details_stage(DONE)

    def _indexer_name(self, host):
        host = str(host or "").lower()
        return self._hosts.get(host) or host_label(host)

    def _details_stage(self, state):
        with self._lock:
            rows = list(self._indexers.values())
            answered = set()
            for row in rows:
                answered.update(row["media_hosts"])
            waiting = [
                self._indexer_name(host)
                for host in self._asked
                if host.lower() not in answered
            ]
            lines = list(self._stages[DETAILS].lines[:1])

        def _with(kind, outcome):
            return [
                row["name"] for row in rows if outcome in row["media"].get(kind, ())
            ]

        got = []
        for kind, name in ((MEDIAINFO, "details_mediainfo"), (NFO, "details_nfo")):
            names = _with(kind, READ)
            if names:
                got.append(self.text(name, self._names(names)))
        trouble = []
        refused = sorted(set(_with(MEDIAINFO, REFUSED) + _with(NFO, REFUSED)))
        failed = sorted(set(_with(MEDIAINFO, FAILED) + _with(NFO, FAILED)))
        if refused:
            trouble.append(self.text("details_refused", self._names(refused)))
        if failed:
            trouble.append(self.text("details_failed", self._names(failed)))
        if state == ACTIVE:
            self._set(DETAILS, ACTIVE, *(lines + [_SEPARATOR.join(got + trouble)]))
            return
        if waiting:
            trouble.append(self.text("details_waiting", self._names(waiting)))
        self._set(
            DETAILS,
            DONE,
            _SEPARATOR.join(got) or self.text("details_none"),
            _SEPARATOR.join(trouble),
        )

    # --- stage 5: the list --------------------------------------------------

    def sorting(self, count):
        """The results are being filtered and sorted for the list."""
        with self._lock:
            told = self._details_told
        if not told:
            self.details_skipped()
        elif self._state(DETAILS) == ACTIVE:
            self._details_stage(DONE)
        self._set(PREPARE, ACTIVE, self.text("sorting", count))

    def checking_downloads(self):
        """The download client is being asked what it already has."""
        with self._lock:
            lines = list(self._stages[PREPARE].lines[:1])
        self._set(PREPARE, ACTIVE, *(lines + [self.text("checking_downloads")]))

    def ready(self, shown, total):
        """The list is about to open with ``shown`` of ``total`` results."""
        self._set(PREPARE, DONE, self.text("ready", shown, total), self.text("opening"))

    def auto_selected(self):
        """The best match is being played without showing the list."""
        self._set(PREPARE, DONE, self.text("best_match"))

    # --- what the window draws ----------------------------------------------

    def snapshot(self):
        """A consistent copy of everything the window shows."""
        with self._lock:
            now = self._clock()
            stages = []
            current = None
            for index, key in enumerate(STAGES):
                stage = self._stages[key]
                lines = list(stage.lines)
                if stage.state == ACTIVE:
                    current = (index, key, lines)
                    seconds = int(now - (stage.started or now))
                    if seconds >= _ELAPSED_AFTER_SECONDS and lines:
                        lines[0] = "{}{}{} s".format(lines[0], _SEPARATOR, seconds)
                stages.append(
                    {
                        "key": key,
                        "name": self.text(_STAGE_NAMES[key]),
                        "state": stage.state,
                        "text": _LINE_BREAK.join(lines),
                    }
                )
            rows = sorted(
                self._indexers.values(),
                key=lambda row: (
                    _INDEXER_ORDER.get(row["state"], 4),
                    -(row["count"] or 0),
                    row["name"].lower(),
                ),
            )
            return {
                "version": self._version,
                "subject": self._subject,
                "elapsed": int(now - self._opened),
                "stages": stages,
                "indexers": [self._table_row(row) for row in rows],
                "cancelled": self._cancelled,
                "summary": self._summary(current),
                "percent": self._percent(current),
            }

    def _summary(self, current):
        """One line for the corner indicator: the stage in hand."""
        if current is None:
            return self._subject
        _index, key, lines = current
        name = self.text(_STAGE_NAMES[key])
        return "{}: {}".format(name, lines[0]) if lines else name

    def _percent(self, current):
        done = sum(
            1 for key in STAGES if self._stages[key].state in (DONE, WARN, SKIPPED)
        )
        if current is None and done == 0:
            return 0
        return min(100, int(100 * (done + 0.5) / len(STAGES)))

    def _table_row(self, row):
        state = row["state"]
        if state == WAITING:
            result = self.text("row_waiting")
        elif state == FAILED:
            result = (
                self.text("row_failed", row["error"])
                if row["error"]
                else self.text("row_failed_plain")
            )
        elif state == EMPTY:
            result = self.text("row_no_results")
        elif state == OK:
            result = (
                self.text("row_one_result")
                if row["count"] == 1
                else self.text("row_results", row["count"])
            )
        else:
            result = ""
        if state in (OK, EMPTY) and row["ms"]:
            result = "{}{}{:.1f} s".format(result, _SEPARATOR, row["ms"] / 1000.0)
        media = [
            (kind, max(row["media"][kind], key=_MEDIA_RANK.get))
            for kind in (MEDIAINFO, NFO)
            if row["media"].get(kind)
        ]
        best = max((_MEDIA_RANK[outcome] for _kind, outcome in media), default=-1)
        return {
            "name": row["name"],
            "state": state,
            "result": result,
            "details": ", ".join(
                self.text(_MEDIA_TEXTS[outcome], _KIND_NAMES[kind])
                for kind, outcome in media
            ),
            "details_state": {3: READ, 2: NOTHING, 1: FAILED, 0: FAILED}.get(best, ""),
        }


def _indexer_row(name, state, count=None):
    return {
        "name": name,
        "state": state,
        "count": count,
        "ms": None,
        "error": "",
        "reported": False,
        "media": {},
        "media_hosts": set(),
    }


# --- the search's side: tell the session in view, if there is one -----------

_ACTIVE = None
_ACTIVE_LOCK = threading.Lock()


def activate(session):
    """Make ``session`` the one the module-level calls report to."""
    global _ACTIVE  # pylint: disable=global-statement
    with _ACTIVE_LOCK:
        _ACTIVE = session
    return session


def deactivate(session=None):
    """Stop reporting to ``session`` (or to whichever is active)."""
    global _ACTIVE  # pylint: disable=global-statement
    with _ACTIVE_LOCK:
        if session is None or _ACTIVE is session:
            _ACTIVE = None


def active():
    """The session being shown, or None."""
    return _ACTIVE


def cancelled():
    """True when the viewer cancelled the search being shown."""
    session = _ACTIVE
    return bool(session is not None and session.cancelled())


def dismiss():
    """Close the window now, ahead of a dialog that needs the screen."""
    session = _ACTIVE
    if session is not None:
        session.close()


def _tell(method, *args, **kwargs):
    session = _ACTIVE
    if session is None:
        return
    try:
        getattr(session, method)(*args, **kwargs)
    except Exception as exc:  # pylint: disable=broad-except
        # Narration must never cost a search.
        xbmc.log(
            "{}: search progress report '{}' failed: {}".format(LOG_TAG, method, exc),
            xbmc.LOGDEBUG,
        )


def identified(search_type, title, **facts):
    """See ``Session.identified``."""
    _tell("identified", search_type, title, **facts)


def ids_resolved(tvdb="", imdb=""):
    """See ``Session.ids_resolved``."""
    _tell("ids_resolved", tvdb=tvdb, imdb=imdb)


def search_cached(count, age_seconds=None):
    """See ``Session.search_cached``."""
    _tell("search_cached", count, age_seconds)


def search_started(providers):
    """See ``Session.search_started``."""
    _tell("search_started", providers)


def search_retry():
    """See ``Session.search_retry``."""
    _tell("search_retry")


def provider_finished(label, count, error=None):
    """See ``Session.provider_finished``."""
    _tell("provider_finished", label, count, error)


def search_finished(rows, error=None):
    """See ``Session.search_finished``."""
    _tell("search_finished", rows, error)


def indexers_expected(names):
    """See ``Session.indexers_expected``."""
    _tell("indexers_expected", names)


def indexer_report(entries):
    """See ``Session.indexer_report``."""
    _tell("indexer_report", entries)


def ranking(count):
    """See ``Session.ranking``."""
    _tell("ranking", count)


def details_skipped(turned_off=False):
    """See ``Session.details_skipped``."""
    _tell("details_skipped", turned_off)


def details_started(releases, hosts):
    """See ``Session.details_started``."""
    _tell("details_started", releases, hosts)


def details_result(host, kind, outcome):
    """See ``Session.details_result``."""
    _tell("details_result", host, kind, outcome)


def details_finished():
    """See ``Session.details_finished``."""
    _tell("details_finished")


def sorting(count):
    """See ``Session.sorting``."""
    _tell("sorting", count)


def checking_downloads():
    """See ``Session.checking_downloads``."""
    _tell("checking_downloads")


def ready(shown, total):
    """See ``Session.ready``."""
    _tell("ready", shown, total)


def auto_selected():
    """See ``Session.auto_selected``."""
    _tell("auto_selected")
