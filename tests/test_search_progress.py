# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The search progress session: stages, the indexer table, the wording."""

import os
import re

import pytest
from resources.lib import search_progress as sp
from resources.lib.i18n import _FALLBACK_STRINGS


class _Clock:  # pylint: disable=too-few-public-methods
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture(name="session")
def _session():
    return sp.Session(clock=_Clock())


@pytest.fixture(autouse=True)
def _no_active_session():
    sp.deactivate()
    yield
    sp.deactivate()


def _stage(session, key):
    return next(s for s in session.snapshot()["stages"] if s["key"] == key)


def _row(session, name):
    return next(r for r in session.snapshot()["indexers"] if r["name"] == name)


# --- stage 1: the title -----------------------------------------------------


def test_an_episode_is_named_with_its_season_episode_and_ids(session):
    session.identified(
        "episode",
        "The Expanse",
        season="2",
        episode="5",
        tmdb="63639",
        tvdb="280619",
        imdb="tt3230854",
    )

    stage = _stage(session, sp.IDENTIFY)
    assert stage["state"] == sp.ACTIVE
    assert stage["text"] == (
        "The Expanse · Season 2, Episode 5[CR]"
        "TMDb 63639 · TVDB 280619 · IMDb tt3230854"
    )
    assert session.snapshot()["subject"] == "The Expanse · Season 2, Episode 5"


def test_a_film_is_named_with_its_year_and_says_when_no_ids_came(session):
    session.identified("movie", "Heat", year="1995")

    assert _stage(session, sp.IDENTIFY)["text"] == (
        "Heat (1995)[CR]No IDs supplied; searching by title"
    )


def test_an_id_the_add_on_looked_up_is_marked_and_settles_the_stage(session):
    session.identified("episode", "Severance", season="1", episode="1", tmdb="95396")

    session.ids_resolved(tvdb="371980")

    stage = _stage(session, sp.IDENTIFY)
    assert stage["state"] == sp.DONE
    assert stage["text"].endswith("TMDb 95396 · TVDB 371980 (looked up)")


def test_an_id_the_player_sent_is_not_marked_as_looked_up(session):
    session.identified("movie", "Heat", year="1995", tmdb="949", imdb="tt0113277")

    session.ids_resolved(imdb="tt0113277")

    assert _stage(session, sp.IDENTIFY)["text"].endswith("TMDb 949 · IMDb tt0113277")


# --- stage 2: the search ----------------------------------------------------


def test_a_live_search_waits_on_its_providers_and_shows_how_long(session):
    session.identified("movie", "Heat", year="1995")
    session.search_started(["NZBHydra2"])

    assert _stage(session, sp.IDENTIFY)["state"] == sp.DONE
    assert _stage(session, sp.SEARCH)["text"] == "Waiting for NZBHydra2"

    session._clock.now += 12  # pylint: disable=protected-access
    assert _stage(session, sp.SEARCH)["text"] == "Waiting for NZBHydra2 · 12 s"


def test_a_title_retry_is_said_while_the_search_is_still_waiting(session):
    session.search_started(["NZBHydra2"])
    session.search_retry()

    assert _stage(session, sp.SEARCH)["text"] == (
        "Waiting for NZBHydra2[CR]Nothing matched the ID; trying the title"
    )


def test_saved_results_say_how_old_they_are(session):
    session.search_cached(40, age_seconds=20)
    assert _stage(session, sp.SEARCH)["text"] == (
        "Using results saved moments ago[CR]40 results"
    )

    session.search_cached(40, age_seconds=60 * 14)
    assert _stage(session, sp.SEARCH)["text"].startswith(
        "Using results saved 14 min ago"
    )

    session.search_cached(40, age_seconds=3 * 3600)
    assert _stage(session, sp.SEARCH)["text"].startswith("Using results saved 3 h ago")
    assert _stage(session, sp.SEARCH)["state"] == sp.DONE


def test_a_finished_search_names_who_answered_and_who_failed(session):
    session.search_started(["NZBHydra2", "Prowlarr"])
    session.provider_finished("NZBHydra2", 2)
    session.provider_finished("Prowlarr", 0, "Prowlarr search failed: timed out")

    session.search_finished([{"indexer": "A"}, {"indexer": "A"}])

    stage = _stage(session, sp.SEARCH)
    assert stage["state"] == sp.WARN
    assert stage["text"] == (
        "2 results from NZBHydra2[CR]"
        "Prowlarr failed: Prowlarr search failed: timed out"
    )


def test_a_search_that_failed_outright_shows_the_reason(session):
    session.search_started(["NZBHydra2"])
    session.provider_finished("NZBHydra2", 0, "NZBHydra unavailable: refused")

    session.search_finished([], "NZBHydra unavailable: refused")

    stage = _stage(session, sp.SEARCH)
    assert stage["state"] == sp.WARN
    assert stage["text"] == "The search failed[CR]NZBHydra unavailable: refused"
    assert _stage(session, sp.INDEXERS)["state"] == sp.SKIPPED


def test_an_empty_search_says_no_results(session):
    session.search_started(["NZBHydra2"])
    session.provider_finished("NZBHydra2", 0)

    session.search_finished([])

    assert _stage(session, sp.SEARCH)["text"] == "No results"


# --- stage 3: the indexers --------------------------------------------------


def _hydra_report():
    return [
        {"name": "Alpha", "ok": True, "count": 12, "ms": 409, "error": None},
        {"name": "Beta", "ok": True, "count": 0, "ms": 231, "error": None},
        {
            "name": "Gamma",
            "ok": False,
            "count": 0,
            "ms": 0,
            "error": (
                "Error while communicating with indexer Gamma. "
                "Server returned: No route to host (connect failed)"
            ),
        },
    ]


def test_an_indexer_report_fills_the_table_and_the_stage(session):
    session.search_started(["NZBHydra2"])
    session.indexer_report(_hydra_report())
    session.provider_finished("NZBHydra2", 10)
    session.search_finished([{"indexer": "Alpha"}] * 10)

    stage = _stage(session, sp.INDEXERS)
    assert stage["state"] == sp.WARN
    assert stage["text"] == (
        "2 of 3 indexers answered · 10 results[CR]No answer from Gamma"
    )
    assert _row(session, "Alpha")["result"] == "12 results · 0.4 s"
    assert _row(session, "Beta")["result"] == "no results · 0.2 s"
    assert _row(session, "Gamma")["result"] == (
        "failed: No route to host (connect failed)"
    )
    # Results first, then the failures, then the indexers with nothing.
    names = [row["name"] for row in session.snapshot()["indexers"]]
    assert names == ["Alpha", "Gamma", "Beta"]


def test_without_a_report_the_table_counts_the_result_rows(session):
    session.search_cached(3, age_seconds=5)
    session.search_finished(
        [{"indexer": "Alpha"}, {"indexer": "Alpha"}, {"indexer": "Beta"}]
    )

    stage = _stage(session, sp.INDEXERS)
    assert stage["state"] == sp.DONE
    assert stage["text"] == "3 results from 2 indexers"
    assert _row(session, "Alpha")["result"] == "2 results"
    assert _row(session, "Beta")["result"] == "1 result"


def test_indexers_asked_directly_show_as_searching_until_they_answer(session):
    session.search_started(["direct indexers"])
    session.indexers_expected(["Alpha", "Beta"])

    assert _row(session, "Alpha")["result"] == "searching…"
    assert _stage(session, sp.INDEXERS)["text"].startswith("0 of 2 indexers answered")

    session.indexer_report([{"name": "Alpha", "ok": True, "count": 4, "ms": 1500}])

    assert _stage(session, sp.INDEXERS)["state"] == sp.ACTIVE
    assert _stage(session, sp.INDEXERS)["text"].startswith("1 of 2 indexers answered")
    assert _row(session, "Alpha")["result"] == "4 results · 1.5 s"


def test_many_failed_indexers_are_summarised(session):
    session.indexer_report(
        [{"name": "I{}".format(n), "ok": False, "error": "x"} for n in range(6)]
    )
    session.search_finished([])

    assert _stage(session, sp.INDEXERS)["text"].endswith(
        "No answer from I0, I1, I2, I3 and 2 more"
    )


def test_an_error_is_shortened_and_never_shows_a_key():
    long = "Server returned: " + "x" * 200
    assert len(sp.short_error(long)) == 72
    assert sp.short_error(long).endswith("…")
    assert "SECRET" not in sp.short_error("GET /api?apikey=SECRET&t=search failed")
    assert sp.short_error(None) == ""


# --- stage 4: NFO and MediaInfo ---------------------------------------------


def _rows_with_hosts():
    return [
        {"indexer": "Alpha", "nfo_url": "https://api.alpha.example/api?t=getnfo"},
        {"indexer": "Beta", "media_ref": "https://beta.example/api?t=details"},
    ]


def test_release_detail_reads_are_named_by_indexer(session):
    session.search_finished(_rows_with_hosts())
    session.ranking(2)
    assert _stage(session, sp.DETAILS)["text"] == ("Ranking 2 results")

    session.details_started(3, ["api.alpha.example", "beta.example"])
    assert _stage(session, sp.DETAILS)["text"] == (
        "Checking the top 3 releases[CR]Asking Alpha, Beta"
    )

    session.details_result("beta.example", sp.MEDIAINFO, sp.READ)
    session.details_result("api.alpha.example", sp.NFO, sp.REFUSED)
    session.details_finished()

    stage = _stage(session, sp.DETAILS)
    assert stage["state"] == sp.DONE
    assert stage["text"] == "MediaInfo from Beta[CR]Refused by Alpha"
    assert _row(session, "Beta")["details"] == "MediaInfo read"
    assert _row(session, "Beta")["details_state"] == sp.READ
    assert _row(session, "Alpha")["details"] == "NFO refused"
    assert _row(session, "Alpha")["details_state"] == sp.FAILED


def test_an_indexer_still_reading_when_the_wait_ends_is_named(session):
    session.search_finished(_rows_with_hosts())
    session.details_started(1, ["api.alpha.example", "beta.example"])
    assert _stage(session, sp.DETAILS)["text"].startswith("Checking the top release")
    session.details_result("beta.example", sp.NFO, sp.NOTHING)

    session.details_finished()

    assert _stage(session, sp.DETAILS)["text"] == (
        "No new details for these releases[CR]Still waiting on Alpha; carrying on"
    )
    assert _row(session, "Beta")["details"] == "NFO: nothing new"


def test_a_host_no_result_row_names_is_shown_by_its_host(session):
    session.details_started(1, ["www.gamma.example"])
    session.details_result("www.gamma.example", sp.NFO, sp.READ)
    session.details_finished()

    assert _stage(session, sp.DETAILS)["text"] == "NFO from gamma.example"


def test_reads_turned_off_or_not_needed_are_said(session):
    session.details_skipped(turned_off=True)
    assert _stage(session, sp.DETAILS)["state"] == sp.SKIPPED
    assert _stage(session, sp.DETAILS)["text"] == "Turned off in settings"

    session.details_skipped()
    assert _stage(session, sp.DETAILS)["state"] == sp.DONE
    assert _stage(session, sp.DETAILS)["text"] == (
        "Nothing new to read for the top releases"
    )


# --- stage 5: the list ------------------------------------------------------


def test_the_list_stage_closes_the_details_stage_and_ends_ready(session):
    session.ranking(30)
    session.sorting(30)

    assert _stage(session, sp.DETAILS)["state"] == sp.DONE
    assert _stage(session, sp.PREPARE)["text"] == "Filtering and sorting 30 results"

    session.checking_downloads()
    assert _stage(session, sp.PREPARE)["text"] == (
        "Filtering and sorting 30 results[CR]Checking what is already downloaded"
    )

    session.ready(12, 30)
    stage = _stage(session, sp.PREPARE)
    assert stage["state"] == sp.DONE
    assert stage["text"] == "12 of 30 results match your filters[CR]Opening the list"


def test_auto_select_says_the_best_match_is_played(session):
    session.auto_selected()

    assert _stage(session, sp.PREPARE)["text"] == "Playing the best match"


# --- the snapshot, cancel and close -----------------------------------------


def test_the_corner_line_and_percent_follow_the_stage_in_hand(session):
    assert session.snapshot()["percent"] == 0

    session.identified("movie", "Heat", year="1995")
    session.search_started(["NZBHydra2"])

    snapshot = session.snapshot()
    assert snapshot["summary"] == "Search: Waiting for NZBHydra2"
    assert snapshot["percent"] == 30


def test_cancel_is_remembered_and_close_runs_each_closer_once(session):
    closed = []
    session.on_close(lambda: closed.append("a"))
    session.on_close(lambda: 1 / 0)
    session.on_close(lambda: closed.append("b"))

    assert not session.cancelled()
    session.cancel()
    assert session.cancelled()
    assert session.snapshot()["cancelled"] is True

    session.close()
    session.close()
    assert closed == ["a", "b"]


def test_a_stage_change_is_written_to_the_log_once(session):
    import xbmc

    xbmc.log.reset_mock()
    session.search_started(["NZBHydra2"])
    session.search_started(["NZBHydra2"])

    lines = [call.args[0] for call in xbmc.log.call_args_list]
    assert (
        lines.count(
            "NeNeTeePee-Stream-Kodi: Search progress: Search [active] "
            "Waiting for NZBHydra2"
        )
        == 1
    )


def test_a_bad_translation_never_raises():
    session = sp.Session({"waiting": "Waiting {} {} {}"})

    assert session.text("waiting", "x") == "Waiting {} {} {} x"


# --- the module-level calls -------------------------------------------------


def test_reports_go_nowhere_without_a_session_in_view():
    assert sp.active() is None
    assert sp.cancelled() is False
    sp.search_started(["NZBHydra2"])
    sp.indexer_report([{"name": "Alpha"}])
    sp.details_result("h", sp.NFO, sp.READ)
    sp.dismiss()


def test_reports_reach_the_active_session_until_it_is_deactivated(session):
    assert sp.activate(session) is session

    sp.identified("movie", "Heat", year="1995")
    sp.search_started(["NZBHydra2"])
    sp.provider_finished("NZBHydra2", 1)
    sp.search_finished([{"indexer": "Alpha"}])
    sp.ranking(1)
    sp.details_skipped()
    sp.sorting(1)
    sp.checking_downloads()
    sp.ready(1, 1)
    assert [s["state"] for s in session.snapshot()["stages"]] == [sp.DONE] * 5

    session.cancel()
    assert sp.cancelled() is True

    sp.deactivate(sp.Session())
    assert sp.active() is session
    sp.deactivate(session)
    assert sp.active() is None


def test_a_report_that_fails_is_swallowed(session):
    sp.activate(session)
    session.search_started = lambda *_a: 1 / 0

    sp.search_started(["NZBHydra2"])


def test_dismiss_closes_the_session_in_view(session):
    closed = []
    session.on_close(lambda: closed.append(True))
    sp.activate(session)

    sp.dismiss()

    assert closed == [True]


# --- the strings ------------------------------------------------------------


def _po_ids():
    path = os.path.join(
        "repo",
        "plugin.video.nzbdav",
        "resources",
        "language",
        "resource.language.en_gb",
        "strings.po",
    )
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    pairs = re.findall(r'msgctxt "#(\d+)"\nmsgid "((?:[^"\\]|\\.)*)"', content)
    return {int(msg_id): text.replace('\\"', '"') for msg_id, text in pairs}


def test_every_string_is_in_the_language_file_and_the_fallbacks():
    po = _po_ids()
    for name, msg_id in sp.TEXT_IDS.items():
        assert po.get(msg_id), "strings.po lacks #{} ({})".format(msg_id, name)
        assert _FALLBACK_STRINGS.get(msg_id) == po[msg_id], name
    # The window's XML uses this one by number, and settings.xml these three.
    for msg_id in (30709, 30766, 30767, 30768):
        assert po.get(msg_id), "strings.po lacks #{}".format(msg_id)


def test_load_texts_reads_every_string_by_name():
    texts = sp.load_texts()

    assert set(texts) == set(sp.TEXT_IDS)


def test_a_late_indexer_answer_after_the_search_changes_nothing(session):
    session.search_started([sp.DIRECT_PROVIDER])
    session.indexers_expected(["A", "B"])
    session.indexer_report([{"name": "A", "ok": True, "count": 2, "ms": 300}])
    session.indexer_report([{"name": "B", "ok": False, "error": "timed out"}])
    # B's worker finishes after the fan-out gave up on it.
    session.indexer_report([{"name": "B", "ok": True, "count": 5, "ms": 25000}])
    session.search_finished([{"indexer": "A"}, {"indexer": "A"}])
    session.indexer_report([{"name": "B", "ok": True, "count": 5, "ms": 25000}])
    session.indexers_expected(["C"])

    assert _stage(session, sp.INDEXERS)["state"] == sp.WARN
    assert _row(session, "B")["result"] == "failed: timed out"
    assert [row["name"] for row in session.snapshot()["indexers"]] == ["A", "B"]


def test_the_direct_indexer_provider_is_named_in_words(session):
    session.search_started([sp.DIRECT_PROVIDER])
    assert _stage(session, sp.SEARCH)["text"] == "Waiting for Direct indexers"

    session.provider_finished(sp.DIRECT_PROVIDER, 1)
    session.search_finished([{"indexer": "A"}])
    assert _stage(session, sp.SEARCH)["text"] == "1 results from Direct indexers"
