# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The audio a release's NFO names, read for the top releases of a search."""

import http.server
import json
import threading
from unittest.mock import patch

import pytest
from resources.lib import filter as filter_module
from resources.lib import hydra, newznab_attrs, nfo_audio
from resources.lib.filter_audio import AUDIO_TIER_UNKNOWN, audio_tier
from resources.lib.xml_safety import safe_fromstring

_NS = 'xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"'
PLAIN = "Some.Movie.2004.MULTi.1080p.BluRay.x264-GRP"
OTHER = "Some.Movie.2004.1080p.BluRay.x264-OTHER"
THIRD = "Some.Movie.2004.720p.BluRay.x264-THIRD"
FOURTH = "Some.Movie.2004.720p.BluRay.x264-FOURTH"

SCENE_NFO = """
  Release.......: Some.Movie.2004.MULTi.1080p.BluRay.x264-GRP
  Source........: BluRay @ 27885 kb/s
  Video.........: x264 @ 15371 kb/s - 23.976 FPS
  Audio.........: French DTS-HD MA 5.1 @ 3963 kb/s - 1152 kb/s core
                  English DTS-HD MA 5.1 @ 3739 kb/s - 1152 kb/s core
  Subtitles.....: French, English
  Notes.........: Enjoy this DTS-free AC3 joke in the notes, not an audio line
"""

MEDIAINFO_NFO = """
Video
Format                                   : HEVC
Bit rate                                 : 24.5 Mb/s

Audio
ID                                       : 2
Format                                   : E-AC-3 JOC
Commercial name                          : Dolby Digital Plus with Dolby Atmos
Channel(s)                               : 6 channels
Language                                 : English

Text
Format                                   : UTF-8
"""


def _ttl(key, default=""):
    return default


@pytest.fixture(name="store", autouse=True)
def _store(tmp_path):
    nfo_audio.forget_all()
    path = tmp_path / "nfo_audio.json"
    with patch.object(nfo_audio, "_store_path", return_value=str(path)):
        yield path
    nfo_audio.forget_all()


def _key(title):
    return filter_module._name_form_key(title)


def _rows(*titles, **extra):
    rows = [
        dict(
            {"title": title, "link": "http://hydra/{}".format(n), "size": str(10**10)},
            **extra,
        )
        for n, title in enumerate(titles)
    ]
    filter_module.attach_metadata(rows)
    return rows


# --- reading an NFO ---------------------------------------------------------


def test_a_scene_nfo_gives_the_formats_and_the_channels():
    assert nfo_audio.audio_from_nfo(SCENE_NFO) == {
        "audio": ["DTS-HD MA"],
        "channels": "5.1",
    }


def test_mediainfo_names_are_understood():
    assert nfo_audio.audio_from_nfo(MEDIAINFO_NFO) == {
        "audio": ["Atmos", "DD+"],
        "channels": "5.1",
    }
    cases = {
        "Audio: MLP FBA 16-ch 7.1": ["Atmos", "TrueHD"],
        "Audio: Dolby TrueHD with Dolby Atmos": ["Atmos", "TrueHD"],
        "Audio: DTS XLL 5.1": ["DTS-HD MA"],
        "Audio: DTS-HD Master Audio 7.1": ["DTS-HD MA"],
        "Audio: DTS XLL X 7.1": ["DTS:X", "DTS-HD MA"],
        "Audio: DTS-HD High Resolution Audio": ["DTS-HD HR"],
        "Audio: Dolby Digital Plus 5.1": ["DD+"],
        "Audio: E-AC-3 5.1 @ 640 kb/s": ["DD+"],
        "Audio: Dolby Digital 5.1": ["DD"],
        "AUDiO : AC-3 5.1 640kbps": ["DD"],
        "Sound: AAC 2.0": ["AAC"],
        "Ton: Deutsch DTS 5.1": ["DTS"],
    }
    for line, expected in cases.items():
        assert nfo_audio.audio_from_nfo(line)["audio"] == expected, line


def test_only_lines_about_the_audio_are_read():
    text = "Video: x264 DTS-free encode\nNotes: TrueHD Atmos is on the other release\n"
    assert nfo_audio.audio_from_nfo(text) == {"audio": [], "channels": ""}
    # A labelled line right under the audio line ends it.
    text = "Audio: English DTS 5.1\nSubs: TrueHD\n"
    assert nfo_audio.audio_from_nfo(text)["audio"] == ["DTS"]
    # A blank line ends it too.
    text = "Audio: English DTS 5.1\n\nTrueHD Atmos elsewhere\n"
    assert nfo_audio.audio_from_nfo(text)["audio"] == ["DTS"]


def test_a_second_track_under_the_audio_line_is_read():
    text = (
        "Audio....: English TrueHD 7.1 Atmos\n           French DD 5.1\nSubs....: en\n"
    )
    assert nfo_audio.audio_from_nfo(text) == {
        "audio": ["Atmos", "TrueHD", "DD"],
        "channels": "7.1",
    }


def test_an_nfo_with_no_audio_and_odd_input():
    for text in ("", None, "just a greeting\n", b"bytes".decode(), "Audio: unknown"):
        assert nfo_audio.audio_from_nfo(text) == {"audio": [], "channels": ""}


# --- the link an indexer sends ----------------------------------------------


def _item(attrs):
    extra = "".join(
        '<newznab:attr name="{}" value="{}"/>'.format(name, value)
        for name, value in attrs
    )
    return safe_fromstring("<item {}>{}</item>".format(_NS, extra))


_LINK = "https://indexer.example/api?t=getnfo&amp;id=abc&amp;apikey=SECRET&amp;raw=1"


def test_the_nfo_link_is_kept_when_it_points_at_the_results_indexer():
    item = _item(
        (("nfo", "1"), ("info", _LINK), ("hydraIndexerHost", "indexer.example"))
    )
    assert newznab_attrs.nfo_link(item) == _LINK.replace("&amp;", "&")
    # The API host and the site host of one indexer are the same site.
    for host in ("api.indexer.example", "example"):
        item = _item((("nfo", "1"), ("info", _LINK), ("hydraIndexerHost", host)))
        expected = "" if host == "example" else _LINK.replace("&amp;", "&")
        assert newznab_attrs.nfo_link(item) == expected, host
    sub = _LINK.replace("indexer.example", "api.indexer.example")
    item = _item((("nfo", "1"), ("info", sub), ("hydraIndexerHost", "indexer.example")))
    assert newznab_attrs.nfo_link(item) == sub.replace("&amp;", "&")


def test_the_nfo_link_is_refused_otherwise():
    refused = (
        # no NFO, or no flag
        (("nfo", "0"), ("info", _LINK), ("hydraIndexerHost", "indexer.example")),
        (("info", _LINK), ("hydraIndexerHost", "indexer.example")),
        # no link
        (("nfo", "1"), ("hydraIndexerHost", "indexer.example")),
        # another host, a look-alike host, no host to compare with
        (("nfo", "1"), ("info", _LINK), ("hydraIndexerHost", "other.example")),
        (("nfo", "1"), ("info", _LINK), ("hydraIndexerHost", "xindexer.example")),
        (("nfo", "1"), ("info", _LINK)),
        # not a web link
        (
            ("nfo", "1"),
            ("info", "file:///etc/passwd"),
            ("hydraIndexerHost", "indexer.example"),
        ),
        (
            ("nfo", "1"),
            ("info", "ftp://indexer.example/x"),
            ("hydraIndexerHost", "indexer.example"),
        ),
        (
            ("nfo", "1"),
            ("info", "http://[bad"),
            ("hydraIndexerHost", "indexer.example"),
        ),
        (
            ("nfo", "1"),
            ("info", "https://indexer.example/" + "x" * 3000),
            ("hydraIndexerHost", "indexer.example"),
        ),
    )
    for attrs in refused:
        assert newznab_attrs.nfo_link(_item(attrs)) == "", attrs


def test_a_hydra_result_carries_the_link():
    xml = (
        '<?xml version="1.0"?><rss version="2.0" {}><channel><item>'
        "<title>{}</title><link>http://hydra/getnzb/api/1</link>"
        '<newznab:attr name="size" value="1000"/>'
        '<newznab:attr name="hydraIndexerName" value="SceneNZBs"/>'
        '<newznab:attr name="hydraIndexerHost" value="indexer.example"/>'
        '<newznab:attr name="nfo" value="1"/>'
        '<newznab:attr name="info" value="{}"/>'
        "</item></channel></rss>"
    ).format(_NS, PLAIN, _LINK)
    (row,) = hydra.parse_results(xml)
    assert row["nfo_url"] == _LINK.replace("&amp;", "&")
    (row,) = hydra.parse_results(
        xml.replace('name="nfo" value="1"', 'name="x" value="1"')
    )
    assert "nfo_url" not in row


# --- what is known is put on the rows ---------------------------------------


def test_the_nfos_audio_is_added_to_every_listing_of_the_release():
    rows = _rows(PLAIN, PLAIN.replace(".", " "), OTHER)
    assert rows[0]["_meta"]["audio"] == []
    assert audio_tier(rows[0]["_meta"]["audio"]) == AUDIO_TIER_UNKNOWN

    nfo_audio._remember(_key(PLAIN), ["DTS-HD MA"], "5.1")
    filter_module.attach_metadata(rows)

    for row in rows[:2]:
        assert row["_meta"]["audio"] == ["DTS-HD MA"]
        assert row["_meta"]["channels"] == "5.1"
        assert row["_meta"][nfo_audio.FROM_NFO_KEY] == ["DTS-HD MA"]
    assert audio_tier(rows[0]["_meta"]["audio"]) == 0
    assert rows[2]["_meta"]["audio"] == []
    assert nfo_audio.FROM_NFO_KEY not in rows[2]["_meta"]


def test_it_only_adds_to_what_the_name_says():
    name = "Some.Movie.2004.2160p.UHD.BluRay.REMUX.TrueHD.7.1.Atmos-GRP"
    (row,) = _rows(name)
    before = list(row["_meta"]["audio"])
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "5.1")
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == before + ["DD"]
    # The name's own channel count stays.
    assert row["_meta"]["channels"] == "7.1"
    assert row["_meta"][nfo_audio.FROM_NFO_KEY] == ["DD"]


def test_applying_it_again_changes_nothing():
    rows = _rows(PLAIN)
    nfo_audio._remember(_key(PLAIN), ["DTS-HD MA"], "5.1")
    filter_module.attach_metadata(rows)
    once = json.dumps(rows[0]["_meta"], sort_keys=True)
    filter_module.attach_metadata(rows)
    assert json.dumps(rows[0]["_meta"], sort_keys=True) == once
    assert nfo_audio.apply_known(rows, _key) == 0


def test_an_nfo_that_named_nothing_changes_nothing():
    rows = _rows(PLAIN)
    nfo_audio._remember(_key(PLAIN), [], "")
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == []
    assert nfo_audio.FROM_NFO_KEY not in rows[0]["_meta"]


def test_atmos_read_from_an_nfo_is_not_passed_on_to_other_groups():
    show = "Some.Show.S03E02.2160p.AMZN.WEB-DL."
    from_nfo = show + "DDP5.1.DV.H.265-GRP"
    other = show + "DDP5.1.DV.H.265-NTb"
    rows = _rows(from_nfo, other)
    nfo_audio._remember(_key(from_nfo), ["Atmos"], "")
    filter_module.attach_metadata(rows)
    assert "Atmos" in rows[0]["_meta"]["audio"]
    assert "Atmos" not in rows[1]["_meta"]["audio"]


# --- the store --------------------------------------------------------------


def test_what_was_read_is_kept_between_runs(store):
    nfo_audio._remember("some key", ["DTS-HD MA"], "5.1")
    nfo_audio._save()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert data["releases"]["some key"]["audio"] == ["DTS-HD MA"]

    nfo_audio.forget_all()
    assert nfo_audio._known("some key")["channels"] == "5.1"
    assert nfo_audio._settled("some key")
    assert not nfo_audio._settled("another key")


def test_an_unreadable_store_is_an_empty_one(store):
    for content in ("{not json", '{"v": 99, "releases": {"k": {}}}', "[]", '{"v": 1}'):
        store.write_text(content, encoding="utf-8")
        nfo_audio.forget_all()
        assert nfo_audio._known("k") is None
    # Entries of the wrong shape are left out, the rest are kept.
    store.write_text(
        json.dumps(
            {
                "v": 1,
                "releases": {
                    "good": {"audio": ["DD"], "channels": "5.1", "at": 5},
                    "bad": {"audio": "DD"},
                    "worse": ["x"],
                },
            }
        ),
        encoding="utf-8",
    )
    nfo_audio.forget_all()
    assert nfo_audio._known("good")["audio"] == ["DD"]
    assert nfo_audio._known("bad") is None and nfo_audio._known("worse") is None


def test_a_failed_read_is_tried_again_after_a_day():
    nfo_audio._remember("k", [], "", failed=True)
    assert nfo_audio._settled("k")
    with patch("resources.lib.nfo_audio.time.time", return_value=10**10):
        assert not nfo_audio._settled("k")


def test_the_store_keeps_the_newest_entries(store):
    with patch.object(nfo_audio, "_MAX_STORED", 3):
        for n in range(5):
            with patch("resources.lib.nfo_audio.time.time", return_value=1000 + n):
                nfo_audio._remember("k{}".format(n), ["DD"], "")
        nfo_audio._save()
    assert sorted(json.loads(store.read_text())["releases"]) == ["k2", "k3", "k4"]


def test_a_store_that_cannot_be_written_is_ignored(store):
    nfo_audio._remember("k", ["DD"], "")
    with patch("resources.lib.nfo_audio.os.replace", side_effect=OSError("ro")):
        nfo_audio._save()
    assert not store.exists()
    assert [p.name for p in store.parent.iterdir()] == []


# --- asking the indexer, against a real local server ------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    requests = []
    replies = {}

    def do_GET(self):  # pylint: disable=invalid-name
        type(self).requests.append(self.path)
        status, body = type(self).replies.get(self.path.split("?")[0], (404, b""))
        self.send_response(status)
        self.send_header("Content-Type", "text/x-nfo")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # pylint: disable=arguments-differ
        return


@pytest.fixture(name="indexer")
def _indexer():
    _Handler.requests = []
    _Handler.replies = {
        "/nfo/plain": (200, SCENE_NFO.encode()),
        "/nfo/atmos": (200, MEDIAINFO_NFO.encode()),
        "/nfo/empty": (200, b"greetings to all\n"),
        "/nfo/refused": (403, b"Forbidden"),
    }
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:{}".format(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _offer(row, base, name):
    row["nfo_url"] = "{}/nfo/{}?apikey=SECRET".format(base, name)
    return row


def test_the_top_release_is_read_and_ranks_with_its_audio(indexer):
    rows = _rows(PLAIN, OTHER)
    _offer(rows[0], indexer, "plain")
    filtered, _all = filter_module.filter_results(rows, settings_getter=_ttl)

    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(filtered, _ttl) is True
    assert _Handler.requests == ["/nfo/plain?apikey=SECRET"]
    # The key in the link is never logged.
    assert not any("SECRET" in call.args[0] for call in log.call_args_list)
    assert any("names audio: DTS-HD MA" in call.args[0] for call in log.call_args_list)

    filtered, _all = filter_module.filter_results(rows, settings_getter=_ttl)
    lead = filtered[0]
    assert lead["title"] == PLAIN
    assert (
        lead["_meta"]["audio"] == ["DTS-HD MA"] and lead["_meta"]["channels"] == "5.1"
    )

    # Asked once: the answer is on disk for the next search.
    assert filter_module.learn_nfo_audio(filtered, _ttl) is False
    nfo_audio.forget_all()
    assert filter_module.learn_nfo_audio(filtered, _ttl) is False
    assert len(_Handler.requests) == 1


def test_only_the_first_releases_are_asked_about(indexer):
    rows = _rows(PLAIN, OTHER, THIRD, FOURTH)
    for row, name in zip(rows, ("empty", "plain", "atmos", "plain")):
        _offer(row, indexer, name)

    def two(key, default=""):
        return "2" if key == "nfo_audio_releases" else default

    assert filter_module.learn_nfo_audio(rows, two) is True
    assert sorted(_Handler.requests) == [
        "/nfo/empty?apikey=SECRET",
        "/nfo/plain?apikey=SECRET",
    ]
    # Opening the search again asks for nothing more: the limit is on the
    # releases looked at, not on the fetches still to do.
    assert filter_module.learn_nfo_audio(rows, two) is False
    assert len(_Handler.requests) == 2
    # The default is three; the cap is five.
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert len(_Handler.requests) == 3
    assert nfo_audio._releases_setting(lambda k, d="": "99") == 5
    assert nfo_audio._releases_setting(lambda k, d="": "junk") == 3
    assert nfo_audio._releases_setting(lambda k, d="": "-4") == 0


def test_another_listing_of_the_release_can_supply_the_link(indexer):
    rows = _rows(PLAIN, PLAIN.replace(".", " "))
    _offer(rows[1], indexer, "plain")
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert len(_Handler.requests) == 1


def test_off_and_nothing_offered_ask_for_nothing(indexer):
    rows = _rows(PLAIN, OTHER)
    assert filter_module.learn_nfo_audio(rows, _ttl) is False
    _offer(rows[0], indexer, "plain")
    assert filter_module.learn_nfo_audio(rows, lambda k, d="": "0") is False
    assert filter_module.learn_nfo_audio([], _ttl) is False
    assert filter_module.learn_nfo_audio([None, {"title": 5}], _ttl) is False
    assert not _Handler.requests


def test_a_refused_read_is_remembered_and_not_repeated(indexer):
    rows = _rows(PLAIN)
    _offer(rows[0], indexer, "refused")
    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert not any("SECRET" in call.args[0] for call in log.call_args_list)
    assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert len(_Handler.requests) == 1
    assert rows[0]["_meta"]["audio"] == []


def test_a_slow_indexer_does_not_hold_the_picker():
    rows = _rows(PLAIN)
    rows[0]["nfo_url"] = "http://127.0.0.1:1/never"
    release = threading.Event()

    def slow(_url, **_kwargs):
        release.wait(5)
        return SCENE_NFO

    with patch("resources.lib.nfo_audio.http_get", side_effect=slow):
        assert nfo_audio.learn_for_top(rows, _key, _ttl, wait=0.05) is False
        # It still finishes, and the answer is there for the next search.
        release.set()
        for _ in range(100):
            if nfo_audio._known(_key(PLAIN)):
                break
            threading.Event().wait(0.02)
    assert nfo_audio._known(_key(PLAIN))["audio"] == ["DTS-HD MA"]


def test_the_tmdbhelper_route_filters_again_when_an_nfo_named_audio(indexer):
    from unittest.mock import MagicMock

    from resources.lib.router_scriptplay import _script_play_filter_autoselect_tag

    rows = [
        {"title": OTHER, "link": "http://hydra/1", "size": str(10**10)},
        _offer(
            {"title": PLAIN, "link": "http://hydra/2", "size": str(10**10)},
            indexer,
            "plain",
        ),
    ]
    with patch("resources.lib.router._get_script_setting", side_effect=_ttl), patch(
        "resources.lib.router_scriptplay._script_play_completed_jobs", return_value={}
    ):
        filtered, _all, _count, _jobs = _script_play_filter_autoselect_tag(
            MagicMock(), {}, rows, "Some Movie", MagicMock()
        )
    by_title = {row["title"]: row for row in filtered}
    assert by_title[PLAIN]["_meta"]["audio"] == ["DTS-HD MA"]
    assert len(_Handler.requests) == 1


def test_windows_line_endings_read_the_same():
    for text in (SCENE_NFO, MEDIAINFO_NFO):
        expected = nfo_audio.audio_from_nfo(text)
        assert expected["audio"]
        assert nfo_audio.audio_from_nfo(text.replace("\n", "\r\n")) == expected
        assert nfo_audio.audio_from_nfo(text.replace("\n", "\r")) == expected
    crlf = "Audio\r\nFormat : E-AC-3 JOC\r\nChannel(s) : 6 channels\r\n"
    assert nfo_audio.audio_from_nfo(crlf) == {
        "audio": ["Atmos", "DD+"],
        "channels": "5.1",
    }


def test_the_channel_count_is_the_main_tracks_own():
    text = "Audio....: French DD 5.1\n           English TrueHD 7.1\nSubs....: en\n"
    assert nfo_audio.audio_from_nfo(text) == {
        "audio": ["DD", "TrueHD"],
        "channels": "7.1",
    }
    blocks = (
        "Audio #1\nFormat : AC-3\nChannel(s) : 6 channels\n\n"
        "Audio #2\nFormat : MLP FBA 16-ch\nChannel(s) : 8 channels\n\n"
    )
    assert nfo_audio.audio_from_nfo(blocks) == {
        "audio": ["DD", "Atmos", "TrueHD"],
        "channels": "7.1",
    }
    # The main track names no count: none is borrowed from another track.
    text = "Audio....: French DD 5.1\n           English TrueHD\n"
    assert nfo_audio.audio_from_nfo(text)["channels"] == ""


def test_two_saves_at_once_keep_both_answers(store):
    # The first save is held just before it replaces the file while a second
    # release is remembered and saved: the second must not be lost.
    gate = threading.Event()
    entered = threading.Event()
    real_replace = nfo_audio.os.replace
    calls = []

    def slow_replace(src, dst):
        calls.append(src)
        if len(calls) == 1:
            entered.set()
            gate.wait(5)
        return real_replace(src, dst)

    nfo_audio._remember("first", ["DD"], "")
    with patch("resources.lib.nfo_audio.os.replace", side_effect=slow_replace):
        one = threading.Thread(target=nfo_audio._save)
        one.start()
        assert entered.wait(5)
        nfo_audio._remember("second", ["DTS"], "")
        two = threading.Thread(target=nfo_audio._save)
        two.start()
        gate.set()
        one.join(5)
        two.join(5)
    assert sorted(json.loads(store.read_text())["releases"]) == ["first", "second"]


def _mi(*fields):
    """A MediaInfo audio block: each label padded to its column."""
    lines = ["{:<41}: {}".format(label, value) for label, value in fields]
    return "\n".join(["Audio"] + lines) + "\n"


_ART = "\u258c\u2591\u2590\u2590\u2588\u2588 \u2590   \u2584\u2580\u2588  "
_BOX = "\u25a0\u2588\u25a0\u2588\u25a0         "

# The audio part of real NFOs, as their groups wrote it.
_REAL_LAYOUTS = (
    # MediaInfo: "Enhanced AC-3" is Dolby Digital Plus, not Dolby Digital.
    (
        _mi(
            ("Format", "E-AC-3"),
            ("Format/Info", "Enhanced AC-3"),
            ("Commercial name", "Dolby Digital Plus"),
            ("Codec ID", "A_EAC3"),
            ("Channel(s)", "6 channels"),
        ),
        ["DD+"],
        "5.1",
    ),
    (
        _mi(
            ("Format", "E-AC-3 JOC"),
            ("Format/Info", "Enhanced AC-3 with Joint Object Coding"),
            ("Commercial name", "Dolby Digital Plus with Dolby Atmos"),
            ("Channel(s)", "6 channels"),
        ),
        ["Atmos", "DD+"],
        "5.1",
    ),
    (
        _mi(
            ("Format", "AC-3"),
            ("Format/Info", "Audio Coding 3"),
            ("Commercial name", "Dolby Digital"),
            ("Channel(s)", "6 channels"),
        ),
        ["DD"],
        "5.1",
    ),
    (
        _mi(
            ("Format", "AAC LC"),
            ("Format/Info", "Advanced Audio Codec Low Complexity"),
            ("Channel(s)", "6 channels"),
        ),
        ["AAC"],
        "5.1",
    ),
    # A row of dots for a colon, behind box-drawing art.
    (_ART + "AUDiO 1............... German EAC3 5.1\n", ["DD+"], "5.1"),
    (
        _BOX
        + "Audio 1...............: DE, E-AC3 5.1 @ 640kb/s\n"
        + _BOX
        + "Audio 2...............: ES, E-AC3 5.1 @ 640kb/s\n",
        ["DD+"],
        "5.1",
    ),
    # The codec and the channels on separate audio lines, in two columns.
    (
        "AUDiO BiTRATE....: 224 kb/s       CODEC............: E-AC-3\n"
        "AUDiO CHANNEL....: 2.0            LANGUAGE.........: VFF\n",
        ["DD+"],
        "2.0",
    ),
    ("Audio Language...: Spanish\nAudio Codec......: E-AC-3 5.1\n", ["DD+"], "5.1"),
    (
        "Audio        : English (GB) E-AC-3 640 kb/s @ 6 channels "
        "(Dolby Digital Plus)\n",
        ["DD+"],
        "5.1",
    ),
    ("audio: en AAC 2.0 120.44kb/s\n", ["AAC"], "2.0"),
    ("Audio     : AAC 5.1 @ 384 kbps\n", ["AAC"], "5.1"),
    (
        "  Audio.........: French DTS-HD MA 5.1 @ 3963 kb/s - 1152 kb/s core\n"
        "                  English DTS-HD MA 5.1 @ 3739 kb/s - 1152 kb/s core\n",
        ["DTS-HD MA"],
        "5.1",
    ),
)


def test_layouts_seen_in_real_nfos():
    for text, audio, channels in _REAL_LAYOUTS:
        assert nfo_audio.audio_from_nfo(text) == {
            "audio": audio,
            "channels": channels,
        }, text


def test_an_indexers_error_reply_names_no_audio():
    # Some results are flagged as having an NFO and the call then answers this.
    reply = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<error code="300" description="No such item"/>\n'
    )
    assert nfo_audio.audio_from_nfo(reply) == {"audio": [], "channels": ""}
    # Bytes that are not text (a compressed NFO sent as it is stored).
    assert nfo_audio.audio_from_nfo(
        "; x\u00a3\u03c6W\u2588n\u250c@}G\u0393\u00aao"
    ) == {
        "audio": [],
        "channels": "",
    }


def test_a_sample_rate_or_bitrate_is_not_a_channel_count():
    text = "Audio: English AAC @ 48.0 kHz, 128.0 kb/s, 23.976 fps\n"
    assert nfo_audio.audio_from_nfo(text) == {"audio": ["AAC"], "channels": ""}


def test_the_nfos_count_is_used_when_its_track_becomes_the_main_one():
    name = "Some.Movie.2004.1080p.BluRay.DD5.1.x264-GRP"
    (row,) = _rows(name)
    assert row["_meta"]["channels"] == "5.1"
    nfo_audio._remember(_key(name), ["DD", "TrueHD"], "7.1")
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["DD", "TrueHD"]
    assert row["_meta"]["channels"] == "7.1"
    # And it stays so when applied again.
    filter_module.attach_metadata([row])
    assert row["_meta"]["channels"] == "7.1"

    # The name's main track stays the main one: its own count is kept.
    name = "Some.Movie.2004.2160p.UHD.BluRay.REMUX.TrueHD.7.1.Atmos-GRP"
    (row,) = _rows(name)
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "5.1")
    filter_module.attach_metadata([row])
    assert row["_meta"]["channels"] == "7.1"


def test_an_indexers_error_is_asked_about_again_but_no_such_item_is_not(indexer):
    _Handler.replies["/nfo/badkey"] = (
        200,
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<error code="100" description="Incorrect user credentials"/>\n',
    )
    _Handler.replies["/nfo/gone"] = (
        200,
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<error code="300" description="No such item"/>\n',
    )
    # "No such item" first: a refusal stops the rest of that indexer's reads.
    rows = _rows(OTHER, PLAIN)
    _offer(rows[0], indexer, "gone")
    _offer(rows[1], indexer, "badkey")
    assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert len(_Handler.requests) == 2
    assert nfo_audio._known(_key(PLAIN))["failed"] is True
    assert nfo_audio._known(_key(OTHER))["failed"] is False

    # Neither is asked again today.
    assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert len(_Handler.requests) == 2
    # A day later the refused one is, and "no such item" is settled for good.
    with patch("resources.lib.nfo_audio.time.time", return_value=10**10):
        filter_module.learn_nfo_audio(rows, _ttl)
    assert len(_Handler.requests) == 3
    assert _Handler.requests[-1].startswith("/nfo/badkey")
    assert nfo_audio._error_code('Audio: <error code="1"/> in an NFO') is None


def test_an_indexer_that_refuses_is_left_alone_for_a_day(indexer, store):
    rows = _rows(PLAIN, OTHER, THIRD)
    for row in rows:
        _offer(row, indexer, "refused")
    with patch("resources.lib.nfo_audio.xbmc.log"):
        assert filter_module.learn_nfo_audio(rows, _ttl) is False
    # One request: after the refusal the other two were not sent.
    assert len(_Handler.requests) == 1
    assert "127.0.0.1" in json.loads(store.read_text())["hosts"]

    # A new search, new releases, the same indexer: nothing is sent, in this
    # run or the next.
    more = _rows(FOURTH)
    _offer(more[0], indexer, "plain")
    assert filter_module.learn_nfo_audio(more, _ttl) is False
    nfo_audio.forget_all()
    assert filter_module.learn_nfo_audio(more, _ttl) is False
    assert len(_Handler.requests) == 1

    # A day later it is asked again.
    with patch("resources.lib.nfo_audio.time.time", return_value=10**10):
        assert filter_module.learn_nfo_audio(more, _ttl) is True
    assert len(_Handler.requests) == 2


def test_an_error_reply_that_refuses_pauses_the_indexer_and_no_such_item_does_not(
    indexer,
):
    _Handler.replies["/nfo/limit"] = (
        200,
        b'<error code="500" description="Request limit reached"/>',
    )
    _Handler.replies["/nfo/gone"] = (
        200,
        b'<error code="300" description="No such item"/>',
    )
    rows = _rows(PLAIN, OTHER)
    _offer(rows[0], indexer, "gone")
    _offer(rows[1], indexer, "plain")
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert len(_Handler.requests) == 2

    more = _rows(THIRD, FOURTH)
    _offer(more[0], indexer, "limit")
    _offer(more[1], indexer, "plain")
    assert filter_module.learn_nfo_audio(more, _ttl) is False
    assert len(_Handler.requests) == 3
    assert nfo_audio._host_paused("127.0.0.1")


def test_another_indexers_listing_is_used_when_one_is_paused(indexer):
    rows = _rows(PLAIN, PLAIN.replace(".", " "))
    rows[0]["nfo_url"] = "http://blocked.example/nfo?apikey=SECRET"
    _offer(rows[1], indexer, "plain")
    nfo_audio._pause_host("blocked.example")
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert _Handler.requests == ["/nfo/plain?apikey=SECRET"]


def test_atmos_on_a_second_track_does_not_label_the_main_one():
    # A plain TrueHD 7.1 track and a separate Dolby Digital Plus Atmos track.
    blocks = (
        "Audio #1\nFormat : MLP FBA\nChannel(s) : 8 channels\n\n"
        "Audio #2\nFormat : E-AC-3 JOC\nChannel(s) : 6 channels\n\n"
    )
    assert nfo_audio.audio_from_nfo(blocks) == {
        "audio": ["TrueHD", "DD+"],
        "channels": "7.1",
    }
    lines = "Audio: English TrueHD 7.1\n       English DDP 5.1 Atmos\nSubs: en\n"
    assert nfo_audio.audio_from_nfo(lines)["audio"] == ["TrueHD", "DD+"]
    # Atmos on the main track itself is kept, whatever else is listed.
    blocks = (
        "Audio #1\nFormat : MLP FBA 16-ch\nChannel(s) : 8 channels\n\n"
        "Audio #2\nFormat : AC-3\nChannel(s) : 6 channels\n\n"
    )
    assert nfo_audio.audio_from_nfo(blocks)["audio"] == ["Atmos", "TrueHD", "DD"]
    # The same for DTS:X beside a better plain track.
    lines = "Audio: English TrueHD 7.1\n       English DTS-X DTS-HD MA 7.1\n"
    assert "DTS:X" not in nfo_audio.audio_from_nfo(lines)["audio"]
    # With only the Atmos track, it is the main one.
    assert nfo_audio.audio_from_nfo("Audio: DDP 5.1 Atmos\n")["audio"] == [
        "Atmos",
        "DD+",
    ]
