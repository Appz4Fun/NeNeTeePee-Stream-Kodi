# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The NFO and MediaInfo texts kept for the picker's viewer: the store, the
tabs identical texts share, the reader that asks every indexer, what a text
says beside the listing's name, and a MediaInfo report as text."""

import http.server
import json
import threading
from unittest.mock import patch

import pytest
from resources.lib import (
    doc_attributes,
    doc_fetch,
    indexer_media,
    mediainfo_text,
    nfo_audio,
    release_docs,
)
from resources.lib.filter import parse_title_metadata, release_identity
from resources.lib.indexer_media import GETNFO, REST_V1, Offer
from resources.lib.release_subject import name_form_key

_ID = "0123456789abcdef0123456789abcdef"
NAME = "Metropolis.1927.2160p.WEB-DL.H.265-GRP"
KEY = name_form_key(NAME)
NFO_TEXT = (
    "  Metropolis.1927.Restored.2160p.UHD.BluRay.REMUX.DV.HDR.HEVC"
    ".DTS-HD.MA.5.1-LANTERN\n\n"
    "  Video....: HEVC 2160p\n"
    "  Audio....: DTS-HD MA 5.1\n"
)
# An NFO that names the very release the listings name.
OWN_NFO = NAME + "\n\n  Audio....: DD+ 5.1\n"
REPORT = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<MediaInfo xmlns="https://mediaarea.net/mediainfo" version="2.0">\n'
    '<media ref="/data/up/Metropolis.1927.2160p.WEB-DL.mkv">'
    '<track type="General"><Format>Matroska</Format>'
    "<FileSize>12345678901</FileSize><Duration>8880.5</Duration></track>"
    '<track type="Video"><Format>HEVC</Format><BitDepth>10</BitDepth>'
    "<Width>3840</Width><Height>2160</Height>"
    "<transfer_characteristics>BT.709</transfer_characteristics></track>"
    '<track type="Audio"><Format>E-AC-3</Format>'
    "<Format_Commercial_IfAny>Dolby Digital Plus with Dolby Atmos"
    "</Format_Commercial_IfAny>"
    "<Format_AdditionalFeatures>JOC</Format_AdditionalFeatures>"
    "<Channels>6</Channels></track></media></MediaInfo>"
)


@pytest.fixture(name="store", autouse=True)
def _store(tmp_path):
    release_docs.forget_all()
    nfo_audio.forget_all()
    folder = tmp_path / "release_docs"
    with patch.object(release_docs, "_dir", return_value=str(folder)), patch.object(
        nfo_audio, "_store_path", return_value=str(tmp_path / "nfo_audio.json")
    ):
        yield folder
    release_docs.forget_all()
    nfo_audio.forget_all()


# --- the store --------------------------------------------------------------


def test_a_text_is_kept_per_indexer_and_read_back():
    release_docs.keep(KEY, release_docs.NFO, "a.example", "one")
    release_docs.keep(KEY, release_docs.NFO, "b.example", "two")
    release_docs.keep(KEY, release_docs.NFO, "a.example", "one, again")

    record = release_docs.load(KEY)
    assert sorted((doc["host"], doc["text"]) for doc in record["docs"]) == [
        ("a.example", "one, again"),
        ("b.example", "two"),
    ]
    assert release_docs.kinds_known(KEY) == (True, False)
    assert release_docs.load("another release") == {"docs": [], "none": {}}


def test_what_is_kept_survives_a_restart(store):
    release_docs.keep(KEY, release_docs.MEDIAINFO, "a.example", REPORT)
    release_docs.mark_none(KEY, release_docs.NFO, "a.example")
    release_docs.forget_all()

    assert release_docs.kinds_known(KEY) == (False, True)
    assert release_docs.asked_none(KEY) == {"nfo a.example"}
    assert json.loads((store / "index.json").read_text())["v"] == 1


def test_an_indexer_with_nothing_is_remembered_until_it_has_something():
    release_docs.mark_none(KEY, release_docs.NFO, "a.example")
    record = release_docs.load(KEY)
    assert release_docs.settled(record, release_docs.NFO, "a.example")
    assert not release_docs.settled(record, release_docs.MEDIAINFO, "a.example")
    assert not release_docs.settled(record, release_docs.NFO, "b.example")

    release_docs.keep(KEY, release_docs.NFO, "a.example", "text")
    assert release_docs.asked_none(KEY) == frozenset()
    # A kept text is not replaced by "nothing".
    release_docs.mark_none(KEY, release_docs.NFO, "a.example")
    assert release_docs.kinds_known(KEY) == (True, False)


def test_empty_and_odd_values_are_not_kept():
    release_docs.keep(KEY, release_docs.NFO, "a.example", "   \n")
    release_docs.keep(KEY, "poster", "a.example", "text")
    release_docs.keep("", release_docs.NFO, "a.example", "text")
    release_docs.keep(KEY, release_docs.NFO, "", "text")
    release_docs.mark_none(KEY, "poster", "a.example")
    assert release_docs.load(KEY) == {"docs": [], "none": {}}
    assert release_docs.kinds_known(None) == (False, False)
    assert release_docs.asked_none(None) == frozenset()


def test_a_damaged_file_reads_as_nothing_kept(store):
    release_docs.keep(KEY, release_docs.NFO, "a.example", "text")
    (store / release_docs._file_name(KEY)).write_text("{not json")
    assert release_docs.load(KEY) == {"docs": [], "none": {}}


def test_only_the_newest_releases_are_kept(store):
    with patch.object(release_docs, "_MAX_RELEASES", 2), patch.object(
        release_docs.time, "time", side_effect=[100, 200, 300]
    ):
        for name in ("first", "second", "third"):
            release_docs.keep(name, release_docs.NFO, "a.example", name)

    assert release_docs.load("first") == {"docs": [], "none": {}}
    assert release_docs.kinds_known("second") == (True, False)
    assert release_docs.kinds_known("third") == (True, False)
    assert len(list(store.glob("*.json"))) == 3  # two releases and the index


def test_a_lost_index_is_made_again_from_the_files(store):
    release_docs.keep(KEY, release_docs.NFO, "a.example", "text")
    release_docs.mark_none(KEY, release_docs.MEDIAINFO, "a.example")
    release_docs.keep("another", release_docs.MEDIAINFO, "b.example", REPORT)
    (store / "index.json").write_text("{not json")
    (store / "notes.json").write_text("{}")
    release_docs.forget_all()

    # Known again without the index, so the old files still count, and go
    # when their turn comes.
    assert release_docs.kinds_known(KEY) == (True, False)
    assert release_docs.asked_none(KEY) == {"mediainfo a.example"}
    with patch.object(release_docs, "_MAX_RELEASES", 2):
        release_docs.keep("third", release_docs.NFO, "a.example", "text")
    assert len(json.loads((store / "index.json").read_text())["releases"]) == 2
    assert len(list(store.glob("*.json"))) == 4  # two releases, the index, notes


def test_what_another_run_kept_is_not_dropped_from_the_index(store):
    release_docs.keep(KEY, release_docs.NFO, "a.example", "text")
    # Another interpreter of the add-on keeps a release this one never saw.
    index = json.loads((store / "index.json").read_text())
    theirs = release_docs._file_name("theirs")
    index["releases"][theirs] = {"nfo": True, "mediainfo": False, "at": 5, "none": []}
    (store / "index.json").write_text(json.dumps(index))

    release_docs.keep("mine", release_docs.NFO, "a.example", "text")
    assert theirs in json.loads((store / "index.json").read_text())["releases"]
    assert release_docs.kinds_known("theirs") == (True, False)


def test_only_this_folders_own_files_are_ever_removed(store, tmp_path):
    outside = tmp_path / "keep-me.json"
    outside.write_text("{}")
    release_docs.keep(KEY, release_docs.NFO, "a.example", "text")
    index = json.loads((store / "index.json").read_text())
    for name in ("../keep-me.json", str(outside), "index.json"):
        index["releases"][name] = {"nfo": True, "mediainfo": False, "at": 0, "none": []}
    (store / "index.json").write_text(json.dumps(index))
    release_docs.forget_all()

    with patch.object(release_docs, "_MAX_RELEASES", 1):
        release_docs.keep("newer", release_docs.NFO, "a.example", "text")
    assert outside.exists() and (store / "index.json").exists()
    assert list(json.loads((store / "index.json").read_text())["releases"]) == [
        release_docs._file_name("newer")
    ]


def test_identical_texts_share_a_tab_that_names_every_indexer():
    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", REPORT)
    release_docs.keep(KEY, release_docs.NFO, "life.example", "another NFO")
    # The same NFO from three indexers: line endings and blank edges differ.
    release_docs.keep(KEY, release_docs.NFO, "c.example", "art\nline  \n")
    release_docs.keep(KEY, release_docs.NFO, "a.example", "\r\nart\r\nline\r\n")
    release_docs.keep(KEY, release_docs.NFO, "b.example", "art\nline")
    names = {"a.example": ["altHUB"], "b.example": ["Zed", "abNZB"]}

    tabs = release_docs.tabs(release_docs.load(KEY), lambda host: names.get(host, ()))

    assert [(tab["kind"], tab["indexers"]) for tab in tabs] == [
        # The NFO most indexers carry leads; a host with no name is its name.
        (release_docs.NFO, ["abNZB", "altHUB", "c.example", "Zed"]),
        (release_docs.NFO, ["life.example"]),
        (release_docs.MEDIAINFO, ["maps.example"]),
    ]
    assert tabs[0]["hosts"] == ["c.example", "a.example", "b.example"]


# --- asking the indexers ----------------------------------------------------

_MEDIAINFO_PATH = "/api/v1/releases/{}/mediainfo".format(_ID)
_NOT_FOUND = json.dumps({"error": {"code": "mediainfo_missing"}}).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    requests = []
    replies = {}

    def do_GET(self):  # pylint: disable=invalid-name
        host = (self.headers.get("Host") or "").split(":")[0]
        path = self.path.split("?")[0]
        type(self).requests.append((host, path))
        replies = type(self).replies
        status, body = replies.get((host, path)) or replies.get(path, (404, b""))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # pylint: disable=arguments-differ
        return


@pytest.fixture(name="indexer")
def _indexer():
    _Handler.requests = []
    _Handler.replies = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _known(**hosts):
    table = dict(indexer_media.KNOWN)
    table.update({name.replace("_", "."): offer for name, offer in hosts.items()})
    return patch.dict(indexer_media.KNOWN, table, clear=True)


def _two_indexers():
    """127.0.0.1 serves MediaInfo and NFOs; localhost serves NFOs only."""
    return _known(
        localhost=Offer(GETNFO, None), **{"127_0_0_1": Offer(GETNFO, REST_V1)}
    )


def _nfo_link(base):
    return "{}/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(base, _ID)


def _reply(report):
    return json.dumps({"guid": _ID, "mediainfo": {"raw": report}}).encode()


def _rows(indexer):
    other = indexer.replace("127.0.0.1", "localhost")
    return [
        {"title": NAME, "indexer": "Maps", "nfo_url": _nfo_link(indexer)},
        {"title": NAME, "indexer": "Hub", "nfo_url": _nfo_link(other)},
        {"title": NAME, "indexer": "Bare"},
    ]


def test_every_indexer_is_asked_for_what_it_has(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (200, _reply(REPORT))
    _Handler.replies["/api"] = (200, OWN_NFO.encode())
    rows = _rows(indexer)
    with _two_indexers():
        assert doc_fetch.gather(rows, KEY, NAME, release_identity) == 2

    # The indexer with both was asked for both, like its page for the release.
    assert sorted(_Handler.requests) == [
        ("127.0.0.1", "/api"),
        ("127.0.0.1", _MEDIAINFO_PATH),
        ("localhost", "/api"),
    ]
    record = release_docs.load(KEY)
    assert sorted((doc["kind"], doc["host"]) for doc in record["docs"]) == [
        ("mediainfo", "127.0.0.1"),
        ("nfo", "127.0.0.1"),
        ("nfo", "localhost"),
    ]
    assert release_docs.kinds_known(KEY) == (True, True)
    # The report is kept as the XML alone, and no link is kept anywhere.
    kept = [doc["text"] for doc in record["docs"] if doc["kind"] == "mediainfo"]
    assert kept == [REPORT]
    assert "SECRET" not in json.dumps(record)


def test_nobody_is_asked_twice(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (404, _NOT_FOUND)
    _Handler.replies[("127.0.0.1", "/api")] = (200, b"")
    _Handler.replies[("localhost", "/api")] = (200, OWN_NFO.encode())
    rows = _rows(indexer)
    with _two_indexers():
        assert doc_fetch.gather(rows, KEY, NAME, release_identity) == 2
        first = list(_Handler.requests)
        assert doc_fetch.plan(rows, release_docs.load(KEY)) == {}
        assert doc_fetch.gather(rows, KEY, NAME, release_identity) == 0

    assert len(first) == 3 and _Handler.requests == first
    assert release_docs.asked_none(KEY) == {"mediainfo 127.0.0.1", "nfo 127.0.0.1"}
    assert release_docs.kinds_known(KEY) == (True, False)


def test_an_indexer_that_refuses_is_left_alone(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (429, b"")
    _Handler.replies["/api"] = (200, OWN_NFO.encode())
    rows = _rows(indexer)
    with _two_indexers():
        doc_fetch.gather(rows, KEY, NAME, release_identity)
        # Refused once: its NFO is not asked for either, now or on the next
        # opening, and it is not written down as having nothing.
        assert ("127.0.0.1", "/api") not in _Handler.requests
        assert not doc_fetch.plan(rows, release_docs.load(KEY))
    assert release_docs.asked_none(KEY) == frozenset()
    assert [doc["host"] for doc in release_docs.load(KEY)["docs"]] == ["localhost"]


def test_another_releases_nfo_is_not_kept(indexer):
    wrong = "Some.Other.Film.2004.1080p.BluRay.x264-OTHER\nAudio: DTS 5.1\n"
    _Handler.replies["/api"] = (200, wrong.encode())
    rows = [{"title": NAME, "indexer": "Hub", "nfo_url": _nfo_link(indexer)}]
    with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
        doc_fetch.gather(rows, KEY, NAME, release_identity)

    assert release_docs.load(KEY)["docs"] == []
    assert release_docs.asked_none(KEY) == {"nfo 127.0.0.1"}


def test_closing_and_opening_again_does_not_ask_the_same_indexer_twice(indexer):
    release = threading.Event()
    replies = _Handler.replies

    class _Slow(dict):
        def get(self, key, default=None):
            release.wait(5)
            return replies.get(key, default)

    replies["/api"] = (200, OWN_NFO.encode())
    _Handler.replies = _Slow()
    rows = [{"title": NAME, "indexer": "Hub", "nfo_url": _nfo_link(indexer)}]
    try:
        with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
            # The viewer opens, is closed before the indexer answers, and
            # opens again: the request in hand is the only one.
            assert doc_fetch.gather(rows, KEY, NAME, release_identity, 0) == 1
            assert doc_fetch.running(KEY) == 1 and doc_fetch.asking(rows, KEY) == 1
            assert doc_fetch.plan(rows, release_docs.load(KEY), KEY) == {}
            assert doc_fetch.gather(rows, KEY, NAME, release_identity, 0) == 0
            # Another release on the same indexer is its own request.
            assert list(doc_fetch.plan(rows, release_docs.load("other"), "other"))
            release.set()
            assert doc_fetch.wait(KEY, 5) == 0
    finally:
        release.set()
        _Handler.replies = replies
    assert len(_Handler.requests) == 1
    assert release_docs.kinds_known(KEY) == (True, False)
    assert doc_fetch.asking(rows, KEY) == 0 and doc_fetch.asking(rows, "") == 0


def test_an_answer_slower_than_the_wait_is_still_kept(indexer):
    release = threading.Event()
    replies = _Handler.replies

    class _Slow(dict):
        def get(self, key, default=None):
            release.wait(5)
            return replies.get(key, default)

    replies["/api"] = (200, OWN_NFO.encode())
    _Handler.replies = _Slow()
    rows = [{"title": NAME, "indexer": "Hub", "nfo_url": _nfo_link(indexer)}]
    try:
        with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
            doc_fetch.gather(rows, KEY, NAME, release_identity, wait_seconds=0.05)
            assert release_docs.load(KEY)["docs"] == []
            assert doc_fetch.wait(KEY, 0.05) == 1
            release.set()
            assert doc_fetch.wait(KEY, 5) == 0
    finally:
        release.set()
        _Handler.replies = replies
    assert [doc["host"] for doc in release_docs.load(KEY)["docs"]] == ["127.0.0.1"]


def test_a_reply_that_repeats_the_key_is_kept_without_it(indexer):
    key = "0123456789abcdef0123456789abcdef"
    link = "{}/api?t=getnfo&id={}&apikey={}&i=7&raw=1".format(indexer, _ID, key)
    echo = "{}\nrequest was /api?t=getnfo&apikey={}&i=7 from user 7\n".format(NAME, key)
    _Handler.replies["/api"] = (200, echo.encode())
    rows = [{"title": NAME, "indexer": "Hub", "nfo_url": link}]
    with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
        doc_fetch.gather(rows, KEY, NAME, release_identity)
        (kept,) = [doc["text"] for doc in release_docs.load(KEY)["docs"]]
        release_docs.forget_all()
        # The search's own read keeps the same text.
        nfo_audio.learn_for_top(rows, name_form_key, lambda _k, default="": default)

    assert key not in kept and "apikey=REDACTED" in kept
    # A short value of a key parameter is a user number: it stays in the text.
    assert "from user 7" in kept and kept.startswith(NAME)
    assert key not in json.dumps(release_docs.load(KEY))
    assert nfo_audio.without_keys(None, link) == ""


def test_a_tersely_named_listing_keeps_the_nfo_that_names_the_film(indexer):
    # The listing has no group in its name; the NFO has the release's full
    # name. Whose encode it is cannot be told, but it is this film's NFO.
    terse = "Metropolis.1927.2160p.DV"
    _Handler.replies["/api"] = (200, NFO_TEXT.encode())
    rows = [{"title": terse, "indexer": "Hub", "nfo_url": _nfo_link(indexer)}]
    with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
        doc_fetch.gather(rows, name_form_key(terse), terse, release_identity)
        # The search's own read keeps it too, and still does not rank by it.
        release_docs.forget_all()
        nfo_audio.learn_for_top(
            rows,
            name_form_key,
            lambda _k, default="": default,
            describe=release_identity,
        )

    assert release_docs.kinds_known(name_form_key(terse)) == (True, False)
    assert nfo_audio.whose_nfo(NFO_TEXT, terse, release_identity) == nfo_audio.WRONG
    assert not nfo_audio._known(name_form_key(terse))["audio"]
    # Another film's NFO, or another group's encode of a named group's
    # release, is still not shown.
    other = "Some.Other.Film.2004.1080p.BluRay.x264-OTHER"
    assert not nfo_audio.shown_for(other, terse, release_identity)
    assert not nfo_audio.shown_for(NFO_TEXT, NAME, release_identity)
    assert nfo_audio.shown_for("greetings", terse, release_identity)


def test_a_release_with_no_key_or_no_links_asks_nobody(indexer):
    assert doc_fetch.gather(_rows(indexer), "", NAME) == 0
    assert doc_fetch.gather([{"title": NAME}, None, {"nfo_url": 7}], KEY, NAME) == 0
    assert not _Handler.requests


def test_what_the_search_reads_is_kept_for_the_viewer(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (200, _reply(REPORT))
    rows = [{"title": NAME, "indexer": "Maps", "nfo_url": _nfo_link(indexer)}]
    with _two_indexers():
        nfo_audio.learn_for_top(rows, name_form_key, lambda _key, default="": default)

    assert release_docs.kinds_known(KEY) == (False, True)


def test_the_picker_knows_what_a_release_has_without_asking():
    rows = [{"title": NAME, "nfo_url": _nfo_link("https://hub.example")}]
    assert doc_fetch.offered([{"title": NAME}], KEY) == (False, False)
    # A listing links to an NFO: there is one to open.
    assert doc_fetch.offered(rows, KEY) == (True, False)
    # That indexer turned out to have none.
    release_docs.mark_none(KEY, release_docs.NFO, "hub.example")
    assert doc_fetch.offered(rows, KEY) == (False, False)
    # Another indexer's is kept: every listing of the release has it.
    release_docs.keep(KEY, release_docs.NFO, "other.example", OWN_NFO)
    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", REPORT)
    assert doc_fetch.offered([{"title": NAME}], KEY) == (True, True)


# --- what a text says -------------------------------------------------------


def _marks(kind, text, title):
    return [
        (item["slot"], item["text"], item["new"])
        for item in doc_attributes.attributes(
            kind, text, title, parse_title_metadata, release_identity
        )
    ]


def test_an_nfo_confirms_what_the_name_says_and_adds_the_rest():
    assert _marks(release_docs.NFO, NFO_TEXT, "4k.Metropolis.1927.DV") == [
        ("resolution", "2160p", False),
        ("hdr", "DV", False),
        ("hdr", "HDR10", True),
        ("codec", "HEVC", True),
        ("audio", "DTS-HD MA 5.1", True),
        ("source", "REMUX", True),
        ("group", "LANTERN", True),
    ]


def test_a_mediainfo_report_is_read_the_same_way():
    assert _marks(release_docs.MEDIAINFO, REPORT, NAME) == [
        ("resolution", "2160p", False),
        ("hdr", "SDR", True),
        ("codec", "HEVC", False),
        ("audio", "DD+ Atmos 5.1", True),
    ]


def test_a_text_that_says_nothing_gives_no_attributes():
    assert not _marks(release_docs.NFO, "greetings to all our friends", NAME)
    assert not _marks(release_docs.MEDIAINFO, "<MediaInfo><media>", NAME)
    assert not _marks(release_docs.MEDIAINFO, "", NAME)


# --- a MediaInfo report as text ---------------------------------------------


def test_a_report_reads_like_mediainfos_own_text():
    assert mediainfo_text.file_name(REPORT) == "Metropolis.1927.2160p.WEB-DL.mkv"
    text = mediainfo_text.as_text(REPORT)
    assert text.split("\n\n", maxsplit=1)[0].splitlines() == [
        "General",
        # The uploader's folder is not shown: the file's name alone.
        "Complete name             : Metropolis.1927.2160p.WEB-DL.mkv",
        "Format                    : Matroska",
        "File size                 : 11.5 GiB",
        "Duration                  : 2 h 28 min",
    ]
    assert "Width                     : 3 840 pixels" in text
    assert "Commercial name           : Dolby Digital Plus with Dolby Atmos" in text
    assert "/data/up" not in text


def test_several_tracks_of_a_kind_are_numbered():
    audio = '<track type="Audio"><Format>{}</Format></track>'
    report = "<MediaInfo><media>{}{}</media></MediaInfo>".format(
        audio.format("TrueHD"), audio.format("AC-3")
    )
    assert mediainfo_text.as_text(report) == (
        "Audio #1\nFormat                    : TrueHD\n\n"
        "Audio #2\nFormat                    : AC-3"
    )


def test_numbers_that_are_not_numbers_are_written_as_they_came():
    report = (
        '<MediaInfo><media><track type="General"><Duration>NaN</Duration>'
        "<FileSize>inf</FileSize><OverallBitRate>1e400</OverallBitRate>"
        '</track><track type="Video"><Width>-inf</Width><Height>abc</Height>'
        "</track></media></MediaInfo>"
    )
    assert mediainfo_text.as_text(report).splitlines() == [
        "General",
        "File size                 : inf",
        "Duration                  : NaN",
        "Overall bit rate          : 1e400",
        "",
        "Video",
        "Width                     : -inf",
        "Height                    : abc",
    ]


def test_a_report_that_cannot_be_read_is_shown_as_it_came():
    for raw in ("<MediaInfo><media>", "not a report", ""):
        assert mediainfo_text.as_text(raw) == raw
        assert mediainfo_text.file_name(raw) == ""
    entity = '<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'
    assert mediainfo_text.as_text(entity) == entity
