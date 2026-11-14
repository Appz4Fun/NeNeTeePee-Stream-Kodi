# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The unpack's volume and time left in the NZBGet download dialog."""

from unittest.mock import patch

from resources.lib import nzbget_api, nzbget_progress, nzbget_resolver

_GB = 1024
_NAME = "Some.Movie.2023.2160p.Remux-GRP [NZBDAV copy 01]"


def _getter(key, default=""):
    return default


def _unpacking(part=None, seconds=0, **extra):
    text = "Extracting from x8Fq.part{:02d}.rar".format(part) if part else ""
    return dict(
        {
            "present": True,
            "status": "UNPACKING",
            "percent": 100,
            "downloaded_mb": 77 * _GB,
            "total_mb": 80 * _GB,
            "parity_hold_mb": 3 * _GB,
            "post_percent": 0,
            "post_seconds": seconds,
            "post_text": text,
        },
        **extra,
    )


def _log(volumes, other=()):
    lines = [
        "Successfully downloaded {}/x8Fq.part{:02d}.rar".format(_NAME, n)
        for n in range(1, volumes + 1)
    ]
    return lines + list(other)


def _lines(state, group, now):
    text = nzbget_progress.progress_message(state, group, None, now=now, max_lines=8)
    return text.split("[CR]")


# --- reading the volume and the count --------------------------------------


def test_the_volume_named_by_the_stage_text():
    number = nzbget_progress._volume_number
    assert number("Extracting from z0R8.part13.rar") == 13
    assert number("Unrar: Extracting from Name With Space.part007.rar  ") == 7
    # Old-style names: name.rar is the first volume, name.r00 the second.
    assert number("Extracting from name.rar") == 1
    assert number("Extracting from name.r00") == 2
    assert number("Extracting from name.r11") == 13
    for other in ("", None, "Unpacking Some.Movie", "Extracting  movie.mkv", 5):
        assert number(other) is None


def test_the_volume_count_comes_from_the_downloads_log():
    log = _log(
        45,
        other=[
            "Successfully downloaded {}/x8Fq.vol003+004.par2".format(_NAME),
            "Successfully downloaded {}/x8Fq.nfo".format(_NAME),
            # The same file logged twice counts once.
            "Successfully downloaded {}/x8Fq.part01.rar".format(_NAME),
            "Unrar: Extracting from x8Fq.part05.rar",
            None,
        ],
    )
    assert nzbget_progress.rar_volumes(log) == {"x8fq": 45}
    old = ["Successfully downloaded job/name.rar"] + [
        "Successfully downloaded job/name.r{:02d}".format(n) for n in range(20)
    ]
    assert nzbget_progress.rar_volumes(old) == {"name": 21}
    assert nzbget_progress.rar_volumes(["Successfully downloaded job/a1b2c3"]) is None
    assert nzbget_progress.rar_volumes([]) is None
    assert nzbget_progress.rar_volumes(None) is None


def test_the_job_carries_the_stage_text():
    with patch("resources.lib.nzbget_api._rpc_call") as call:
        call.return_value = (
            [
                {
                    "NZBID": 42,
                    "Status": "UNPACKING",
                    "FileSizeMB": 100,
                    "PostInfoText": "Extracting from a.part13.rar",
                }
            ],
            None,
        )
        job = nzbget_api.group_status(42, settings_getter=_getter)
    assert job["post_text"] == "Extracting from a.part13.rar"


def test_the_file_log_is_read_long():
    rows = [{"Text": "one"}, {"Text": None}, "junk", {"Text": "two"}]
    with patch("resources.lib.nzbget_api._rpc_call", return_value=(rows, None)) as c:
        assert nzbget_api.file_log(42, settings_getter=_getter) == ["one", "", "two"]
    assert c.call_args.args[0] == "loadlog"
    assert c.call_args.args[1][0] == 42 and c.call_args.args[1][2] >= 1000
    with patch("resources.lib.nzbget_api._rpc_call", return_value=(None, "down")):
        assert nzbget_api.file_log(42, settings_getter=_getter) is None
    with patch("resources.lib.nzbget_api._rpc_call") as call:
        assert nzbget_api.file_log("abc", settings_getter=_getter) is None
    call.assert_not_called()


# --- the dialog --------------------------------------------------------------


def test_the_stage_names_the_volume_and_the_time_left_follows_the_pace():
    state = {"current": 42}
    # Volumes 5, 6 and 7 seen 20 seconds apart.
    for part, now in ((5, 100), (6, 120), (7, 140)):
        nzbget_progress.note_unpack(state, _unpacking(part), now=now)
    state["unpack"]["totals"] = {"x8fq": 45}
    lines = _lines(state, _unpacking(7, seconds=500), now=150)
    assert lines[0] == "Copy 1 - unpacking - part 7 of 45"
    # 39 volumes left at 20 s each, less the 10 s spent on this one.
    assert lines[1] == "8m 20s so far - about 12m 50s left"
    # With no stage time from the server, only the time left.
    lines = _lines(state, _unpacking(7), now=150)
    assert lines[1] == "about 12m 50s left"


def test_before_the_count_and_the_pace_are_known():
    state = {"current": 42}
    nzbget_progress.note_unpack(state, _unpacking(5), now=100)
    lines = _lines(state, _unpacking(5, seconds=30), now=101)
    assert lines[0] == "Copy 1 - unpacking - part 5"
    assert lines[1] == "30s so far"
    # The count is known, the pace is not yet: two volumes are one interval.
    state["unpack"]["totals"] = {"x8fq": 45}
    nzbget_progress.note_unpack(state, _unpacking(6), now=120)
    lines = _lines(state, _unpacking(6, seconds=50), now=121)
    assert lines[0] == "Copy 1 - unpacking - part 6 of 45"
    assert lines[1] == "50s so far"
    # No volume named at all: as before.
    lines = _lines({"current": 42}, _unpacking(None, seconds=50), now=0)
    assert lines[0] == "Copy 1 - unpacking" and lines[1] == "50s so far"


def test_the_servers_own_percent_is_used_when_it_gives_one():
    state = {"current": 42}
    for part, now in ((5, 100), (6, 120), (7, 140)):
        nzbget_progress.note_unpack(state, _unpacking(part), now=now)
    state["unpack"]["totals"] = {"x8fq": 45}
    group = _unpacking(7, seconds=30, post_percent=25)
    lines = _lines(state, group, now=150)
    assert lines[0] == "Copy 1 - unpacking 25%"
    assert lines[1] == "30s so far - about 1m 30s left"


def test_a_slow_start_does_not_set_the_pace():
    state = {"current": 42}
    # Four volumes at 75 s each while another copy held the disk, then 20 s.
    marks = [(1, 0), (2, 75), (3, 150), (4, 225), (5, 300)]
    marks += [(n, 300 + (n - 5) * 20) for n in range(6, 21)]
    for part, now in marks:
        nzbget_progress.note_unpack(state, _unpacking(part), now=now)
    state["unpack"]["totals"] = {"x8fq": 45}
    left = nzbget_progress.unpack_seconds_left(state, _unpacking(20), now=600)
    # 26 volumes left; the volumes of the last five minutes took 20 s each.
    assert 500 <= left <= 540


def test_the_marks_follow_the_copy_and_the_stage():
    state = {"current": 42}
    for part, now in ((5, 100), (6, 120), (7, 140)):
        nzbget_progress.note_unpack(state, _unpacking(part), now=now)
    # The same volume again adds nothing.
    nzbget_progress.note_unpack(state, _unpacking(7), now=145)
    assert [mark[0] for mark in state["unpack"]["marks"]] == [5, 6, 7]
    # A text with no volume while still unpacking keeps what is known.
    nzbget_progress.note_unpack(state, _unpacking(None), now=146)
    assert "unpack" in state
    # A second archive in the job starts again.
    nzbget_progress.note_unpack(state, _unpacking(1), now=150)
    assert [mark[0] for mark in state["unpack"]["marks"]] == [1]
    # Another copy, and leaving the unpack, drop them.
    state["current"] = 43
    nzbget_progress.note_unpack(state, _unpacking(3), now=160)
    assert state["unpack"]["copy"] == 43 and len(state["unpack"]["marks"]) == 1
    moving = dict(_unpacking(None), status="MOVING")
    nzbget_progress.note_unpack(state, moving, now=170)
    assert "unpack" not in state
    assert nzbget_progress.unpack_part(state, moving) == (None, None)
    # Junk state is ignored.
    nzbget_progress.note_unpack(None, _unpacking(3), now=1)
    assert nzbget_progress.unpack_seconds_left(None, _unpacking(3), now=1) is None


def test_a_count_smaller_than_the_volume_is_not_shown():
    state = {"current": 42}
    nzbget_progress.note_unpack(state, _unpacking(9), now=1)
    state["unpack"]["totals"] = {"x8fq": 4}
    assert nzbget_progress.unpack_part(state, _unpacking(9)) == (9, None)


# --- the resolver reads the count once --------------------------------------


def test_the_volume_count_is_read_once_the_unpack_is_on_a_volume():
    state = {"current": 42}
    with patch("resources.lib.nzbget_resolver.nzbget_api.file_log") as log:
        log.return_value = _log(45)
        # Not unpacking, or no volume named yet: nothing is read.
        nzbget_resolver._refresh_unpack(state, {"status": "DOWNLOADING"}, _getter)
        nzbget_resolver._refresh_unpack(state, _unpacking(None), _getter)
        assert log.call_count == 0
        nzbget_resolver._refresh_unpack(state, _unpacking(3), _getter)
        nzbget_resolver._refresh_unpack(state, _unpacking(4), _getter)
    assert log.call_count == 1 and log.call_args.args[0] == 42
    assert state["unpack"]["totals"] == {"x8fq": 45}


def test_an_unreadable_log_is_tried_a_few_times_then_left():
    state = {"current": 42}
    with patch("resources.lib.nzbget_resolver.nzbget_api.file_log") as log:
        log.return_value = None
        for part in range(1, 9):
            nzbget_resolver._refresh_unpack(state, _unpacking(part), _getter)
    assert log.call_count == 3
    assert state["unpack"]["totals"] is None
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.file_log", side_effect=OSError("x")
    ):
        nzbget_resolver._refresh_unpack({"current": 7}, _unpacking(2), _getter)


def test_a_job_with_two_archives_counts_each_archives_own_volumes():
    log = [
        "Successfully downloaded job/Film.part{:02d}.rar".format(n)
        for n in range(1, 11)
    ] + [
        "Successfully downloaded job/Extras.part{:02d}.rar".format(n)
        for n in range(1, 5)
    ]
    assert nzbget_progress.rar_volumes(log) == {"film": 10, "extras": 4}

    def group(name, part):
        text = "Extracting from {}.part{:02d}.rar".format(name, part)
        return dict(_unpacking(None), post_text=text)

    state = {"current": 42}
    for part, now in ((8, 100), (9, 120), (10, 140)):
        nzbget_progress.note_unpack(state, group("Film", part), now=now)
    state["unpack"]["totals"] = nzbget_progress.rar_volumes(log)
    assert _lines(state, group("Film", 10), now=141)[0].endswith("part 10 of 10")
    # The next archive: its own count, and a pace of its own volumes.
    nzbget_progress.note_unpack(state, group("Extras", 1), now=160)
    lines = _lines(state, group("Extras", 1), now=161)
    assert lines[0].endswith("part 1 of 4")
    assert "left" not in " ".join(lines[1:2])
    for part, now in ((2, 170), (3, 180)):
        nzbget_progress.note_unpack(state, group("Extras", part), now=now)
    left = nzbget_progress.unpack_seconds_left(state, group("Extras", 3), now=180)
    assert left == 20
    # A higher volume of another archive is another archive too.
    nzbget_progress.note_unpack(state, group("Film", 9), now=190)
    assert state["unpack"]["archive"] == "film"
    assert [mark[0] for mark in state["unpack"]["marks"]] == [9]
    # An archive the log did not name has no count.
    nzbget_progress.note_unpack(state, group("Unknown", 2), now=200)
    assert nzbget_progress.unpack_part(state, group("Unknown", 2)) == (2, None)
