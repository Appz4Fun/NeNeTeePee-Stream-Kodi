"""What the NZBGet download dialog and its toast say."""

from unittest.mock import MagicMock, patch

import pytest
from resources.lib import nzbget_bulk, nzbget_progress
from resources.lib.http_util import _escape_builtin_arg
from resources.lib.nzbget_resolver import poll_nzbget_job

_GS = "resources.lib.nzbget_resolver.nzbget_api.group_status"
_HS = "resources.lib.nzbget_resolver.nzbget_api.history_status"
_ACT = "resources.lib.nzbget_resolver.nzbget_api.active_group_by_dupekey"
_SUC = "resources.lib.nzbget_resolver.nzbget_api.history_success_by_dupekey"


class _Dialog:
    def __init__(self):
        self.lines = []

    def iscanceled(self):
        return False

    def update(self, percent, message=""):
        self.lines.append((percent, message))


class _Monitor:  # pylint: disable=too-few-public-methods
    def waitForAbort(self, _seconds):
        return False


def _lines(text):
    return text.split("[CR]", 3)


def _fleet(*nzbids):
    return {"owned_nzbids": lambda: list(nzbids)}


def _group(status="DOWNLOADING", percent=43, downloaded=4608, total=10650):
    return {
        "present": True,
        "status": status,
        "percent": percent,
        "downloaded_mb": downloaded,
        "total_mb": total,
    }


def test_first_line_names_the_copy_and_the_percent():
    text = nzbget_progress.progress_message({}, _group(), _fleet(1, 2, 3), now=0)
    assert _lines(text)[0] == "Copy 1 of 3 - downloading 43%"


def test_a_single_copy_has_no_total():
    text = nzbget_progress.progress_message({}, _group(), _fleet(1), now=0)
    assert text.startswith("Copy 1 - downloading 43%")
    assert nzbget_progress.progress_message({}, _group(), None, now=0).startswith(
        "Copy 1 - "
    )


@pytest.mark.parametrize(
    "status, stage",
    [
        ("QUEUED", "waiting in the queue"),
        ("LOADING_PARS", "checking the files"),
        ("VERIFYING_SOURCES", "checking the files"),
        ("REPAIRING", "repairing"),
        ("UNPACKING", "unpacking"),
        ("MOVING", "moving into place"),
        ("EXECUTING_SCRIPT", "finishing up"),
        ("SOMETHING_NEW", "finishing up"),
    ],
)
def test_each_nzbget_stage_has_its_own_words(status, stage):
    text = nzbget_progress.progress_message({}, _group(status, 100), None, now=0)
    assert _lines(text)[0] == "Copy 1 - " + stage


def test_size_speed_and_time_left_come_from_the_polls():
    state = {}
    first = nzbget_progress.progress_message(state, _group(downloaded=4000), now=100)
    assert _lines(first)[1] == "3.9 of 10.4 GB"  # one sample: no speed yet
    second = nzbget_progress.progress_message(
        state, _group(downloaded=4620, total=10650), now=110
    )
    assert _lines(second)[1] == "4.5 of 10.4 GB - 62 MB/s - about 1m 37s left"


def test_slow_speed_keeps_a_decimal_and_long_waits_show_hours():
    state = {}
    nzbget_progress.progress_message(state, _group(downloaded=0, total=20000), now=0)
    text = nzbget_progress.progress_message(
        state, _group(downloaded=25, total=20000), now=10
    )
    assert _lines(text)[1] == "0.0 of 19.5 GB - 2.5 MB/s - about 2h 13m left"


def test_speed_is_dropped_while_nothing_arrives_or_the_counter_resets():
    state = {}
    nzbget_progress.progress_message(state, _group(downloaded=4000), now=0)
    stalled = nzbget_progress.progress_message(state, _group(downloaded=4000), now=10)
    assert _lines(stalled)[1] == "3.9 of 10.4 GB"
    reset = nzbget_progress.progress_message(state, _group(downloaded=100), now=20)
    assert _lines(reset)[1] == "0.1 of 10.4 GB"


def test_speed_follows_the_last_half_minute_not_the_whole_download():
    state = {}
    for second, done in ((0, 0), (10, 1000), (20, 2000), (30, 3000), (40, 3100)):
        text = nzbget_progress.progress_message(
            state, _group(downloaded=done, total=10000), now=second
        )
    # 3,100 MB in the 30 s since the 10 s sample would be 70; the window is
    # 10..40 s: (3100 - 1000) / 30.
    assert " - 70 MB/s - " in text
    for second, done in ((50, 3200), (60, 3300), (70, 3400)):
        text = nzbget_progress.progress_message(
            state, _group(downloaded=done, total=10000), now=second
        )
    assert " - 10 MB/s - " in text


def test_no_size_line_when_nzbget_gave_no_sizes_or_is_past_downloading():
    plain = {"present": True, "status": "DOWNLOADING", "percent": 5}
    assert nzbget_progress.progress_message({}, plain, None, now=0) == (
        "Copy 1 - downloading 5%"
    )
    unpacking = nzbget_progress.progress_message(
        {}, _group("UNPACKING", 100, 10650, 10650), None, now=0
    )
    assert unpacking == "Copy 1 - unpacking[CR]10.4 of 10.4 GB"


@pytest.mark.parametrize(
    "status, reason",
    [
        ("FAILURE/HEALTH", "too many missing articles"),
        ("WARNING/HEALTH", "too many missing articles"),
        ("FAILURE/PAR", "it could not be repaired"),
        ("FAILURE/UNPACK", "it could not be unpacked"),
        ("FAILURE/PASSWORD", "it needs a password"),
        ("WARNING/SPACE", "the download disk is full"),
        ("DELETED/DUPE", "NZBGet skipped it as a duplicate"),
        ("DELETED/COPY", "NZBGet skipped it as a duplicate"),
        ("FAILURE/BAD", "the files are damaged"),
        ("DELETED/MANUAL", "it was removed from NZBGet"),
        ("FAILURE/SCRIPT", "NZBGet reported FAILURE/SCRIPT"),
        ("", "NZBGet reported FAILURE"),
        (None, "NZBGet reported FAILURE"),
    ],
)
def test_a_failure_gets_a_plain_reason(status, reason):
    assert nzbget_progress.failure_reason(status) == reason


def test_a_failed_copy_stays_on_the_dialog_after_the_switch():
    state = {}
    nzbget_progress.record_failure(state, "FAILURE/HEALTH")
    assert nzbget_progress.waiting_message(state) == (
        "Copy 1 failed: too many missing articles[CR]Waiting for the next copy..."
    )
    nzbget_progress.next_copy(state)
    text = nzbget_progress.progress_message(
        state, _group(percent=5, downloaded=0, total=0), _fleet(1, 2, 3), now=0
    )
    assert text == (
        "Copy 2 of 3 - downloading 5%[CR]Copy 1 failed: too many missing articles"
    )


def test_several_failures_are_counted_with_the_last_reason():
    state = {}
    nzbget_progress.record_failure(state, "FAILURE/HEALTH")
    nzbget_progress.next_copy(state)
    nzbget_progress.record_failure(state, "FAILURE/UNPACK")
    nzbget_progress.next_copy(state)
    text = nzbget_progress.progress_message(state, _group(), _fleet(1, 2, 3), now=0)
    assert _lines(text)[0] == "Copy 3 of 3 - downloading 43%"
    assert _lines(text)[-1] == ("2 copies failed. Last one: it could not be unpacked")


def test_a_new_copy_starts_its_speed_from_scratch():
    state = {}
    nzbget_progress.progress_message(state, _group(downloaded=9000), now=0)
    nzbget_progress.next_copy(state)
    text = nzbget_progress.progress_message(state, _group(downloaded=9500), now=10)
    assert "MB/s" not in text


def test_no_waiting_text_before_any_failure():
    assert nzbget_progress.waiting_message({}) == ""


def test_total_copies_survives_a_broken_or_missing_fleet():
    assert nzbget_progress.total_copies(None) == 0
    assert nzbget_progress.total_copies({}) == 0
    assert nzbget_progress.total_copies({"owned_nzbids": lambda: None}) == 0

    def boom():
        raise RuntimeError("gone")

    assert nzbget_progress.total_copies({"owned_nzbids": boom}) == 0
    # More failovers than the fleet knows about: drop the total, keep counting.
    state = {"copy_number": 4}
    assert nzbget_progress.progress_message(
        state, _group(), _fleet(1, 2), now=0
    ).startswith("Copy 4 - ")


@pytest.mark.parametrize(
    "copies, health, text",
    [
        (12, 100, "12 copies ready - health 100%"),
        (9, 100, "9 copies ready - health 100%"),
        (1, 97.6, "1 copy ready - health 97%"),
        (12, -1, "12 copies ready"),
        (12, None, "12 copies ready"),
        (0, 100, "Download started - health 100%"),
        (0, -1, "Download started"),
    ],
)
def test_the_toast_is_short_and_plain(copies, health, text):
    toast = nzbget_progress.ready_toast(copies, health)
    assert toast == text
    # Nothing the notification call would swap for a look-alike character.
    assert _escape_builtin_arg(toast) == toast
    assert len(toast) <= 32


def _health_reply(alive):
    members = [{"NZBID": 100 + n, "Status": "BACKUP", "Alive": 90} for n in range(11)]
    members.insert(0, {"NZBID": 42, "Status": "QUEUED", "Alive": alive})
    return {"Chosen": 42, "Members": members}


def test_accepting_twelve_copies_toasts_the_whole_health_figure():
    progress = MagicMock()
    with patch("resources.lib.nzbget_resolver._notify") as notify:
        nzbget_bulk._health_message(progress, _health_reply(100))
    assert notify.call_args.args[1] == "12 copies ready - health 100%"
    assert notify.call_args.args[2] == 8000


def test_unknown_health_is_left_out_of_the_toast():
    progress = MagicMock()
    with patch("resources.lib.nzbget_resolver._notify") as notify:
        nzbget_bulk._health_message(progress, _health_reply(-1))
    assert notify.call_args.args[1] == "12 copies ready"


def test_the_poll_shows_copy_stage_size_and_the_earlier_failure():
    # Pick (1) downloads, fails on health; backup (9) takes over and succeeds.
    dialog = _Dialog()
    groups = {
        1: [_group(percent=43), {"present": False, "status": "", "percent": 0}],
        9: [
            _group("UNPACKING", 100, 10650, 10650),
            {"present": False, "status": "", "percent": 0},
        ],
    }
    history = {
        1: {"present": True, "success": False, "status": "FAILURE/HEALTH"},
        9: {
            "present": True,
            "success": True,
            "status": "SUCCESS/ALL",
            "dest_dir": "/dl/B",
        },
    }
    promoted = dict(_group(percent=5, downloaded=532, total=10650), nzbid=9)
    with patch(
        _GS, side_effect=lambda n, settings_getter=None: groups[n].pop(0)
    ), patch(_HS, side_effect=lambda n, settings_getter=None: history[n]), patch(
        _ACT, return_value=promoted
    ), patch(
        _SUC, return_value={"present": False}
    ):
        result = poll_nzbget_job(
            1,
            dialog,
            _Monitor(),
            60,
            interval=0,
            dupe_key="k",
            fleet=_fleet(1, 9, 10),
        )
    assert result["outcome"] == "success"
    assert result["nzbid"] == 9
    messages = [message for _percent, message in dialog.lines]
    assert messages[0] == "Copy 1 of 3 - downloading 43%[CR]4.5 of 10.4 GB"
    assert messages[1] == (
        "Copy 2 of 3 - downloading 5%[CR]0.5 of 10.4 GB"
        "[CR]Copy 1 failed: too many missing articles"
    )
    assert messages[2] == (
        "Copy 2 of 3 - unpacking[CR]10.4 of 10.4 GB"
        "[CR]Copy 1 failed: too many missing articles"
    )


def test_the_poll_names_the_failure_while_it_waits_for_a_backup():
    dialog = _Dialog()
    absent = {"present": False, "status": "", "percent": 0}
    history = {1: {"present": True, "success": False, "status": "FAILURE/PAR"}}
    with patch(_GS, return_value=absent), patch(
        _HS, side_effect=lambda n, settings_getter=None: history[n]
    ), patch(
        _ACT,
        return_value={"present": False, "paused_present": False, "paused_nzbids": []},
    ), patch(
        _SUC, return_value={"present": False}
    ), patch(
        "resources.lib.nzbget_resolver._PROMOTION_GRACE", 0
    ):
        result = poll_nzbget_job(
            1, dialog, _Monitor(), 60, interval=0, dupe_key="k", fleet=_fleet(1)
        )
    assert result["outcome"] == "failed"
    assert not dialog.lines or all(
        message
        == "Copy 1 failed: it could not be repaired[CR]Waiting for the next copy..."
        for _percent, message in dialog.lines
    )


# --- what was sent, what was left out, twins and alts ------------------------


def _report(sent=(), refused=None, failed=0, duplicates=0):
    blocked = {name.lower() for name in (refused or {})}
    report = nzbget_progress.FleetReport(blocked)
    for name in sent:
        report.kept({"indexer": name})
    for name, count in (refused or {}).items():
        for _ in range(count):
            report.dropped({"indexer": name})
    for _ in range(failed):
        report.dropped({"indexer": "Somewhere"})
    for _ in range(duplicates):
        report.duplicate()
    return report


_SIX = ("Ninjacentral", "Zurg", "Square Eyed", "altHUB", "abNZB", "Zurg")
_DONE = {
    "present": True,
    "success": True,
    "status": "SUCCESS/ALL",
    "dest_dir": "/d",
}


def test_the_send_stage_names_the_indexers_and_what_was_left_out():
    report = _report(_SIX, refused={"Digital Carnage": 1}, duplicates=1)
    assert nzbget_progress.sending_message(report, 6) == (
        "Sending 6 NZBs to NZBGet..."
        "[CR]abNZB, altHUB, Ninjacentral, Square Eyed, Zurg x2"
        "[CR]Not sent: Digital Carnage refused 1, 1 duplicate upload"
    )
    assert report.found() == 8


def test_the_send_stage_is_one_line_when_nothing_is_known():
    assert nzbget_progress.sending_message(None, 3) == "Sending 3 NZBs to NZBGet..."
    plain = nzbget_progress.FleetReport()
    plain.kept({})  # a copy with no indexer name
    assert nzbget_progress.sending_message(plain, 1) == "Sending 1 NZBs to NZBGet..."


def test_a_long_indexer_list_ends_in_a_count():
    names = ["Indexer Number %02d" % n for n in range(1, 9)]
    line = nzbget_progress.indexers_line(_report(names))
    assert line == ("Indexer Number 01, Indexer Number 02, Indexer Number 03, +5 more")


@pytest.mark.parametrize(
    "kwargs, line",
    [
        ({}, ""),
        ({"duplicates": 1}, "Not sent: 1 duplicate upload"),
        ({"duplicates": 3}, "Not sent: 3 duplicate uploads"),
        ({"failed": 2}, "Not sent: 2 could not be downloaded"),
        (
            {"refused": {"Square Eyed": 2, "abNZB": 1}, "failed": 1},
            "Not sent: abNZB refused 1, Square Eyed refused 2, "
            "1 could not be downloaded",
        ),
    ],
)
def test_the_not_sent_line(kwargs, line):
    assert nzbget_progress.not_sent_line(_report(("Geek",), **kwargs)) == line


def test_a_dropped_copy_is_only_called_refused_when_its_indexer_refused():
    report = nzbget_progress.FleetReport({"square eyed"})
    report.dropped({"indexer": "Square Eyed"})
    report.dropped({"indexer": "Geek"})  # a timeout or a missing NZB
    report.dropped({})
    assert report.refused == {"Square Eyed": 1}
    assert report.failed == 2


def _history_row(nzbid, kind=None, status="DELETED/DUPE"):
    parameters = [{"Name": "DupeAlive", "Value": "100"}]
    if kind:
        parameters.append({"Name": "DupeKind", "Value": kind})
    return {"NZBID": nzbid, "Status": status, "Parameters": parameters}


def test_a_backup_that_was_promoted_and_failed_is_no_longer_counted():
    # Copy 2 took over and failed; its history row keeps DupeKind=alt. Only
    # the copies still parked are backups.
    rows = [
        _history_row(1, None, "FAILURE/HEALTH"),
        _history_row(2, "alt", "FAILURE/PAR"),
        _history_row(3, "alt"),
        _history_row(4, "twin", "WARNING/DAMAGED"),
        _history_row(5, "twin", "SUCCESS/ALL"),
        _history_row(6, "twin", "deleted/dupe"),
    ]
    kinds = nzbget_progress.backup_kinds(rows, [1, 2, 3, 4, 5, 6, 7], 7)
    assert kinds == {"alt": 1, "twin": 1}
    assert nzbget_progress.kinds_line(kinds) == "Backups: 1 twin, 1 alt"


def test_backups_are_counted_as_twins_and_alts():
    rows = [
        _history_row(1, "twin"),  # the copy being downloaded: not a backup
        _history_row(2, "twin"),
        _history_row(3, "twin:12/40"),
        _history_row(4, "alt"),
        _history_row(5),  # not marked yet
        _history_row(99, "twin"),  # another play's
        "junk",
    ]
    kinds = nzbget_progress.backup_kinds(rows, [1, 2, 3, 4, 5], 1)
    assert kinds == {"twin": 2, "alt": 1}
    assert nzbget_progress.kinds_line(kinds) == "Backups: 2 twins, 1 alt"
    assert nzbget_progress.kinds_line({"alt": 5}) == "Backups: 5 alts"
    assert nzbget_progress.kinds_line({"twin": 1}) == "Backups: 1 twin"
    # Stock NZBGet sets no such mark: the line is left out.
    assert not nzbget_progress.backup_kinds([_history_row(2)], [1, 2], 1)
    assert nzbget_progress.kinds_line({}) == ""
    assert not nzbget_progress.backup_kinds(None, None, None)


def test_the_download_dialog_adds_backup_kinds_and_what_was_not_sent():
    report = _report(_SIX, refused={"Digital Carnage": 1}, duplicates=1)
    fleet = dict(_fleet(1, 2, 3, 4, 5, 6), report=report)
    state = {"backup_kinds": {"alt": 5}}
    text = nzbget_progress.progress_message(state, _group(), fleet, now=0)
    assert _lines(text) == [
        "Copy 1 of 6 - downloading 43%",
        "4.5 of 10.4 GB",
        "Backups: 5 alts",
        "Not sent: Digital Carnage refused 1, 1 duplicate upload",
    ]
    # A copy that failed mid-download takes the last line.
    nzbget_progress.record_failure(state, "FAILURE/HEALTH")
    nzbget_progress.next_copy(state)
    text = nzbget_progress.progress_message(state, _group(), fleet, now=0)
    assert _lines(text)[-1] == "Copy 1 failed: too many missing articles"
    assert len(_lines(text)) == 4


@pytest.mark.parametrize(
    "copies, report_kwargs, text",
    [
        (
            6,
            {"refused": {"DC": 1}, "duplicates": 1},
            "6 of 8 copies ready - health 100%",
        ),
        (5, {}, "5 of 6 copies ready - health 100%"),  # one dead on the server
        (6, {}, "6 copies ready - health 100%"),
    ],
)
def test_the_toast_counts_the_copies_that_were_left_out(copies, report_kwargs, text):
    toast = nzbget_progress.ready_toast(copies, 100, _report(_SIX, **report_kwargs))
    assert toast == text
    assert _escape_builtin_arg(toast) == toast
    assert len(toast) <= 36


def test_the_poll_reads_the_backup_kinds_a_few_times_at_most():
    dialog = _Dialog()
    groups = [_group(percent=n) for n in range(1, 41)]
    groups.append({"present": False, "status": "", "percent": 0})
    history = dict(_DONE)
    reads = []

    def _history_rows(_getter=None):
        reads.append(1)
        # The server marks the second backup only by the second read.
        marked = [_history_row(2, "twin")]
        if len(reads) > 1:
            marked.append(_history_row(3, "alt"))
        return marked

    with patch(_GS, side_effect=groups), patch(_HS, return_value=history), patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows",
        side_effect=_history_rows,
    ):
        result = poll_nzbget_job(
            1, dialog, _Monitor(), 600, interval=0, fleet=_fleet(1, 2, 3)
        )
    assert result["outcome"] == "success"
    assert len(reads) == 2  # both backups marked: no third read
    messages = [message for _percent, message in dialog.lines]
    assert "Backups: 1 twin" in messages[0]
    assert "Backups: 1 twin, 1 alt" in messages[10]
    assert "Backups: 1 twin, 1 alt" in messages[-1]


def test_the_poll_shows_no_backup_line_for_a_single_copy_or_stock_nzbget():
    absent = {"present": False, "status": "", "percent": 0}
    history = dict(_DONE)
    cases = ((_fleet(1), [_history_row(1)]), (_fleet(1, 2), [_history_row(2)]))
    for fleet, rows in cases:
        dialog = _Dialog()
        with patch(_GS, side_effect=[_group(), _group(), absent]), patch(
            _HS, return_value=history
        ), patch(
            "resources.lib.nzbget_resolver.nzbget_api.history_rows", return_value=rows
        ) as read:
            poll_nzbget_job(1, dialog, _Monitor(), 60, interval=0, fleet=fleet)
        assert all("Backups" not in message for _percent, message in dialog.lines)
        assert read.call_count <= 1


def test_a_failed_history_read_never_breaks_the_poll():
    dialog = _Dialog()
    absent = {"present": False, "status": "", "percent": 0}
    history = dict(_DONE)
    with patch(_GS, side_effect=[_group(), absent]), patch(
        _HS, return_value=history
    ), patch(
        "resources.lib.nzbget_resolver.nzbget_api.history_rows",
        side_effect=RuntimeError("boom"),
    ):
        result = poll_nzbget_job(
            1, dialog, _Monitor(), 60, interval=0, fleet=_fleet(1, 2)
        )
    assert result["outcome"] == "success"
    assert dialog.lines[0][1].startswith("Copy 1 of 2 - downloading 43%")
