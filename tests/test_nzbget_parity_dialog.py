# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Parity in the NZBGet progress text, and the add-on's own download dialog."""

import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from resources.lib import nzbget_api, nzbget_dialog, nzbget_progress, nzbget_resolver
from resources.lib.nzbget_api import group_status

_DIALOG_XML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "skins",
    "Default",
    "1080i",
    "download-dialog.xml",
)
_GB = 1024


def _getter(key, default=""):
    return {"nzbget_url": "http://box:6789"}.get(key, default)


def _status(**group):
    row = dict({"NZBID": 42, "Status": "DOWNLOADING"}, **group)
    with patch("resources.lib.nzbget_api._rpc_call", return_value=([row], None)):
        return group_status(42, settings_getter=_getter)


def _group(status="DOWNLOADING", downloaded=0, total=89 * _GB, hold=0, **extra):
    wanted = total - hold
    return dict(
        {
            "present": True,
            "status": status,
            "percent": min(int(downloaded * 100 / wanted), 100) if wanted else 0,
            "downloaded_mb": downloaded,
            "total_mb": total,
            "parity_hold_mb": hold,
        },
        **extra,
    )


# --- nzbget_api: the percent and sizes leave out the parity on hold ---------


def test_the_percent_is_of_what_the_download_will_fetch():
    # 89 GB posted, 3.7 GB of it par2 volumes NZBGet keeps paused.
    status = _status(DownloadedSizeMB=87347, FileSizeMB=91136, PausedSizeMB=3789)
    assert status["percent"] == 100
    assert status["total_mb"] == 91136
    assert status["parity_hold_mb"] == 3789


def test_a_release_with_no_parity_on_hold_is_counted_as_before():
    status = _status(DownloadedSizeMB=250, FileSizeMB=1000)
    assert status["percent"] == 25
    assert status["parity_hold_mb"] == 0


def test_a_job_paused_as_a_whole_has_no_parity_taken_off():
    status = _status(
        Status="PAUSED", DownloadedSizeMB=250, FileSizeMB=1000, PausedSizeMB=750
    )
    assert status["percent"] == 25
    assert status["parity_hold_mb"] == 0


def test_odd_size_fields_do_not_break_the_percent():
    assert _status(DownloadedSizeMB=10, FileSizeMB=0, PausedSizeMB=5)["percent"] == 0
    # More on hold than the release holds: clamped, never a negative total.
    status = _status(DownloadedSizeMB=10, FileSizeMB=100, PausedSizeMB=500)
    assert status["parity_hold_mb"] == 100 and status["percent"] == 0
    status = _status(DownloadedSizeMB="x", FileSizeMB="100", PausedSizeMB=None)
    assert status["percent"] == 0 and status["parity_hold_mb"] == 0
    # Downloaded past what was wanted (a par volume fetched): still 100.
    assert _status(DownloadedSizeMB=99, FileSizeMB=100, PausedSizeMB=10)["percent"] == (
        100
    )


def test_a_promoted_backup_reports_the_same_fields():
    row = {
        "NZBID": 7,
        "Status": "DOWNLOADING",
        "DupeKey": "k",
        "DownloadedSizeMB": 450,
        "FileSizeMB": 1000,
        "PausedSizeMB": 100,
    }
    with patch("resources.lib.nzbget_api._rpc_call", return_value=([row], None)):
        active = nzbget_api.active_group_by_dupekey("k", settings_getter=_getter)
    assert active["percent"] == 50 and active["parity_hold_mb"] == 100


def test_par_log_returns_the_downloads_log_text():
    rows = [{"Text": "one"}, "junk", {"Text": None}, {"Text": "two"}]
    with patch("resources.lib.nzbget_api._rpc_call", return_value=(rows, None)) as call:
        assert nzbget_api.par_log("42", settings_getter=_getter) == ["one", "", "two"]
    assert call.call_args.args[:2] == ("loadlog", [42, 0, 120])


def test_par_log_is_none_when_it_cannot_be_read():
    with patch("resources.lib.nzbget_api._rpc_call", return_value=(None, "boom")):
        assert nzbget_api.par_log(42, settings_getter=_getter) is None
    with patch("resources.lib.nzbget_api._rpc_call") as call:
        assert nzbget_api.par_log(None, settings_getter=_getter) is None
        assert nzbget_api.par_log("abc", settings_getter=_getter) is None
    call.assert_not_called()


# --- nzbget_progress: the parity lines --------------------------------------


def test_the_size_line_leaves_out_the_parity_on_hold():
    group = _group(downloaded=43 * _GB, total=89 * _GB, hold=int(3.7 * _GB))
    text = nzbget_progress.progress_message({}, group, None, now=0, max_lines=8)
    lines = text.split("[CR]")
    assert lines[0] == "Copy 1 - downloading 50%"
    assert lines[1] == "43.0 of 85.3 GB"
    assert lines[2] == "Parity: 3.7 GB on hold - not needed so far"


def test_no_parity_line_without_parity_on_hold():
    text = nzbget_progress.progress_message(
        {}, _group(downloaded=_GB, total=10 * _GB), None, now=0, max_lines=8
    )
    assert "Parity" not in text
    assert text.split("[CR]")[1] == "1.0 of 10.0 GB"


def test_a_small_amount_of_parity_reads_in_megabytes():
    group = _group(downloaded=100, total=2000, hold=80)
    assert nzbget_progress.parity_lines({}, group) == [
        "Parity: 80 MB on hold - not needed so far"
    ]


def test_parity_taken_off_hold_for_a_repair():
    state = {"current": 42}
    hold = int(3.7 * _GB)
    nzbget_progress.parity_lines(state, _group(hold=hold))
    # A repair asked for volumes: 1.2 GB is no longer paused.
    group = _group(status="DOWNLOADING", hold=hold - int(1.2 * _GB))
    assert nzbget_progress.parity_lines(state, group) == [
        "Parity: 1.2 GB of 3.7 GB taken off hold for repair"
    ]
    assert round(nzbget_progress.parity_requested(state, group)) == int(1.2 * _GB)


def test_the_block_line_follows_once_the_log_gave_the_counts():
    state = {"current": 42}
    nzbget_progress.parity_lines(state, _group(hold=4000))
    group = _group(status="REPAIRING", hold=2500)
    state["parity_blocks"] = {"needed": 112, "requested": 128}
    assert nzbget_progress.parity_lines(state, group) == [
        "Parity: 1.5 GB of 3.9 GB taken off hold for repair",
        "Repair needs 112 parity blocks - 128 requested",
    ]
    state["parity_blocks"] = {"needed": 112, "requested": 0}
    assert nzbget_progress.parity_lines(state, group)[1] == (
        "Repair needs 112 parity blocks"
    )
    state["parity_blocks"] = {"needed": None, "requested": "x"}
    assert len(nzbget_progress.parity_lines(state, group)) == 1


def test_a_backup_that_takes_over_starts_its_own_parity_count():
    state = {"current": 42}
    nzbget_progress.parity_lines(state, _group(hold=4000))
    nzbget_progress.parity_lines(state, _group(hold=1000))
    state["current"] = 43
    assert nzbget_progress.parity_lines(state, _group(hold=900)) == [
        "Parity: 0.9 GB on hold - not needed so far"
    ]


def test_parity_blocks_reads_the_need_and_the_volumes_taken_off_hold():
    log = [
        "Verifying Movie.part01.rar",
        "Need more 112 par-block(s) for Movie",
        "Unpausing Movie/Movie.vol000+64.par2 for par-recovery",
        "Unpausing Movie/Movie.vol064+32.PAR2 for par-recovery",
        "Unpausing Movie/Movie.vol096+32.par2 for par-recovery",
        "Need more 4 par-block(s) for Movie",
        None,
    ]
    assert nzbget_progress.parity_blocks(log) == {"needed": 112, "requested": 128}


def test_parity_blocks_with_nothing_to_report():
    assert nzbget_progress.parity_blocks(["Unpacking", "Moving"]) is None
    assert nzbget_progress.parity_blocks(None) is None
    # Volumes taken off hold but the need line already scrolled away.
    assert nzbget_progress.parity_blocks(
        ["Unpausing A/A.vol00+16.par2 for par-recovery"]
    ) == {"needed": 16, "requested": 16}


def test_kodis_dialog_keeps_four_lines_and_the_own_dialog_shows_more():
    state = {
        "current": 42,
        "failed": [(1, "FAILURE/HEALTH")],
        "backup_kinds": {"twin": 2, "alt": 3},
    }
    group = _group(
        downloaded=5 * _GB,
        total=12 * _GB,
        hold=2 * _GB,
        failed_articles=155,
        recovered_articles=155,
    )
    four = nzbget_progress.progress_message(dict(state), group, None, now=0)
    assert len(four.split("[CR]")) == 4
    assert "Parity" not in four
    for odd in (None, 0, 2, "8", True, MagicMock()):
        text = nzbget_progress.progress_message(
            dict(state), group, None, now=0, max_lines=odd
        )
        assert len(text.split("[CR]")) == 4, odd
    eight = nzbget_progress.progress_message(
        dict(state), group, None, now=0, max_lines=8
    ).split("[CR]")
    assert eight == [
        "Copy 1 - downloading 50%",
        "5.0 of 10.0 GB",
        "Copy 1 failed: too many missing articles",
        "155 articles missing - 155 recovered from backups",
        "Parity: 2.0 GB on hold - not needed so far",
        "Backups: 2 twins, 3 alts",
    ]


# --- nzbget_resolver: reading the block counts ------------------------------


def test_the_log_is_read_only_once_parity_was_taken_off_hold():
    state = {"current": 42}
    with patch("resources.lib.nzbget_resolver.nzbget_api.par_log") as par_log:
        par_log.return_value = [
            "Need more 20 par-block(s) for X",
            "Unpausing X/X.vol00+32.par2 for par-recovery",
        ]
        for _tick in range(12):
            nzbget_resolver._refresh_parity_blocks(state, _group(hold=4000), _getter)
        par_log.assert_not_called()
        assert "parity_blocks" not in state

        for _tick in range(11):
            nzbget_resolver._refresh_parity_blocks(state, _group(hold=3000), _getter)
    # Every fifth poll: ticks 0, 5 and 10.
    assert par_log.call_count == 3
    assert par_log.call_args.args == (42, _getter)
    assert state["parity_blocks"] == {"needed": 20, "requested": 32}


def test_an_unreadable_log_keeps_the_last_counts_and_never_raises():
    state = {"current": 42}
    nzbget_resolver._refresh_parity_blocks(state, _group(hold=4000), _getter)
    state["parity_blocks"] = {"needed": 20, "requested": 32}
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.par_log", return_value=None
    ), patch.dict(state, {"parity_ticks": 0}):
        nzbget_resolver._refresh_parity_blocks(state, _group(hold=3000), _getter)
    assert state["parity_blocks"] == {"needed": 20, "requested": 32}
    with patch(
        "resources.lib.nzbget_resolver.nzbget_api.par_log", side_effect=RuntimeError
    ), patch.dict(state, {"parity_ticks": 0}):
        nzbget_resolver._refresh_parity_blocks(state, _group(hold=3000), _getter)
    assert state["parity_blocks"] == {"needed": 20, "requested": 32}


def test_the_dialog_is_given_as_many_lines_as_it_has_room_for():
    state = {
        "current": 42,
        "failed": [(1, "FAILURE/HEALTH")],
        "backup_kinds": {"twin": 2},
    }
    group = _group(downloaded=_GB, total=12 * _GB, hold=2 * _GB, failed_articles=3)
    kodi_dialog = MagicMock()
    nzbget_resolver._update_active_dialog(kodi_dialog, group, dict(state), None)
    assert len(kodi_dialog.update.call_args.args[1].split("[CR]")) == 4

    own = MagicMock()
    own.max_lines = 8
    nzbget_resolver._update_active_dialog(own, group, dict(state), None)
    assert len(own.update.call_args.args[1].split("[CR]")) == 6


# --- nzbget_dialog: the add-on's own window ---------------------------------


class _Action:  # pylint: disable=too-few-public-methods
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


def _home():
    home = MagicMock()
    properties = {}
    home.getProperty.side_effect = lambda key: properties.get(key, "")
    home.setProperty.side_effect = properties.__setitem__
    home.clearProperty.side_effect = lambda key: properties.pop(key, None)
    return home


def _open(home):
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        return nzbget_dialog.open_dialog("Add-on", "Finding copies...")


def test_the_first_line_is_the_headline_and_the_rest_the_body():
    assert nzbget_dialog.split_message("a[CR]b[CR]c") == ("a", "b[CR]c")
    assert nzbget_dialog.split_message("only") == ("only", "")
    assert nzbget_dialog.split_message("") == ("", "")
    assert nzbget_dialog.split_message(None) == ("", "")
    assert nzbget_dialog.split_message("a\nb[CR][CR] c ") == ("a", "b[CR]c")
    nine = "[CR]".join(str(n) for n in range(1, 10))
    assert nzbget_dialog.split_message(nine) == ("1", "2[CR]3[CR]4[CR]5[CR]6[CR]7[CR]8")


def test_open_dialog_shows_the_own_window_with_its_text():
    home = _home()
    home.setProperty(nzbget_dialog.CANCEL_PROPERTY, "1")  # left by an earlier play
    dialog = _open(home)

    assert isinstance(dialog, nzbget_dialog.DownloadDialog)
    assert dialog.max_lines == 8
    assert dialog.getProperty("heading") == "Add-on"
    assert dialog.getProperty("headline") == "Finding copies..."
    assert dialog.getProperty("body") == ""
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        assert dialog.iscanceled() is False

    dialog.update(43, "Copy 1 - downloading 43%[CR]4.5 of 10.4 GB[CR]Backups: 2 twins")
    assert dialog.getProperty("headline") == "Copy 1 - downloading 43%"
    assert dialog.getProperty("body") == "4.5 of 10.4 GB[CR]Backups: 2 twins"
    # A percent with no message leaves the text alone.
    dialog.update(50)
    assert dialog.getProperty("headline") == "Copy 1 - downloading 43%"


def test_the_cancel_button_and_back_are_seen_through_the_home_property():
    home = _home()
    dialog = _open(home)
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        assert dialog.iscanceled() is False
        # What the window's <onclick> and <onback> do, with no script callback.
        home.setProperty(nzbget_dialog.CANCEL_PROPERTY, "1")
        assert dialog.iscanceled() is True
        dialog.close()
    assert home.getProperty(nzbget_dialog.CANCEL_PROPERTY) == ""
    # It stays canceled for the code that asks again after closing it.
    assert dialog.iscanceled() is True


def test_back_escape_and_stop_cancel_and_other_keys_do_not():
    for action_id, canceled in ((92, True), (10, True), (13, True), (7, False)):
        home = _home()
        dialog = _open(home)
        dialog.onAction(_Action(action_id))
        with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
            assert dialog.iscanceled() is canceled, action_id


def test_a_click_cancels_only_on_the_cancel_button():
    home = _home()
    dialog = _open(home)
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        dialog.onClick(3)
        assert dialog.iscanceled() is False
        dialog.onClick(nzbget_dialog.CANCEL_BUTTON_ID)
        assert dialog.iscanceled() is True


def test_close_is_safe_to_call_twice_and_before_the_window_showed():
    home = _home()
    dialog = _open(home)
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        dialog.close()
        dialog.close()
    assert dialog._closed is True
    with patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home):
        never_shown = nzbget_dialog.DownloadDialog("x.xml", "/addon", "Default")
        never_shown.close()
    assert never_shown._closed is False


def test_kodis_dialog_is_used_when_the_window_cannot_open():
    home = _home()
    with patch.object(
        nzbget_dialog.DownloadDialog, "show", side_effect=RuntimeError("no skin file")
    ), patch.object(nzbget_dialog.xbmcgui, "Window", return_value=home), patch.object(
        nzbget_dialog.xbmcgui, "DialogProgress"
    ) as progress, patch.object(
        nzbget_dialog.xbmc, "log"
    ) as log:
        dialog = nzbget_dialog.open_dialog("Add-on", "Finding copies...")

    assert dialog is progress.return_value
    progress.return_value.create.assert_called_once_with("Add-on", "Finding copies...")
    assert "no skin file" in log.call_args.args[0]
    # Kodi's dialog has no room for the extra lines.
    assert not isinstance(getattr(dialog, "max_lines", None), int)


def test_the_window_file_is_where_the_dialog_looks_for_it():
    assert os.path.isfile(_DIALOG_XML)
    assert os.path.basename(_DIALOG_XML) == nzbget_dialog.WINDOW_XML
    path = nzbget_dialog._addon_path()
    assert os.path.isfile(os.path.join(path, "addon.xml"))
    assert os.path.isfile(
        os.path.join(
            path, "resources", "skins", "Default", "1080i", "download-dialog.xml"
        )
    )


def test_the_window_has_the_controls_the_dialog_relies_on():
    root = ET.parse(_DIALOG_XML).getroot()
    assert root.tag == "window" and root.get("type") == "dialog"
    assert root.findtext("defaultcontrol") == str(nzbget_dialog.CANCEL_BUTTON_ID)
    controls = {c.get("id"): c for c in root.iter("control") if c.get("id")}
    labels = {
        key: controls[key].findtext("label") for key in ("1", "2", "3", "4", "10")
    }
    assert labels["1"] == "$INFO[Window.Property(heading)]"
    assert labels["2"] == "$INFO[Window.Property(headline)]"
    assert labels["3"] == "$INFO[Window.Property(body)]"
    assert controls["3"].get("type") == "textbox"
    button = controls[str(nzbget_dialog.CANCEL_BUTTON_ID)]
    assert button.get("type") == "button"
    cancel = "SetProperty({},1,home)".format(nzbget_dialog.CANCEL_PROPERTY)
    assert button.findtext("onclick") == cancel
    assert button.findtext("onback") == cancel
    # No progress bar.
    assert not [c for c in root.iter("control") if c.get("type") == "progress"]
    # Every texture is one the add-on ships.
    media = os.path.join(os.path.dirname(os.path.dirname(_DIALOG_XML)), "media")
    textures = {
        node.text
        for tag in ("texture", "texturefocus", "texturenofocus")
        for node in root.iter(tag)
    }
    assert textures and all(os.path.isfile(os.path.join(media, t)) for t in textures)


def test_the_windows_strings_exist():
    from resources.lib.i18n import string

    assert string(30694) == "Cancel"
    assert string(30695) == "Cancelling..."
