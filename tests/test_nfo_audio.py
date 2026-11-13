# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The audio a release's NFO names, read for the top releases of a search."""

import http.server
import json
import threading
from unittest.mock import patch

import pytest
from resources.lib import filter as filter_module
from resources.lib import hydra, mediainfo_audio, newznab_attrs, nfo_audio
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

# The same NFO without the line that says which release it is about.
NAMELESS_NFO = "\n".join(
    line for line in SCENE_NFO.splitlines() if "Release" not in line
)

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


def test_what_was_read_replaces_what_the_name_says():
    # The name says DTS; the NFO says the track is DTS-HD MA 7.1.
    name = "Some.Movie.2004.1080p.BluRay.DTS.5.1.x264-GRP"
    (row,) = _rows(name)
    assert row["_meta"]["audio"] == ["DTS"] and row["_meta"]["channels"] == "5.1"
    nfo_audio._remember(_key(name), ["DTS-HD MA", "DD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["DTS-HD MA", "DD"]
    assert row["_meta"]["channels"] == "7.1"
    assert row["_meta"][nfo_audio.FROM_NFO_KEY] == ["DTS-HD MA", "DD"]
    assert row["_meta"][nfo_audio.SOURCE_KEY] == "nfo"
    # What the name itself tags is kept beside it.
    assert row["_meta"][nfo_audio.NAME_AUDIO_KEY] == {
        "audio": ["DTS"],
        "channels": "5.1",
    }

    # And the other way: the name oversells, the NFO says plain DD 5.1.
    name = "Some.Movie.2005.1080p.BluRay.DTS-HD.MA.7.1.x264-GRP"
    (row,) = _rows(name)
    nfo_audio._remember(_key(name), ["DD"], "5.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["DD"] and row["_meta"]["channels"] == "5.1"
    assert row["_meta"][nfo_audio.FROM_NFO_KEY] == ["DD"]


def test_a_hand_written_nfo_keeps_the_names_atmos_on_the_same_track():
    # "Audio: English TrueHD 7.1" for an Atmos track is how NFOs are written.
    name = "Some.Movie.2004.2160p.UHD.BluRay.REMUX.TrueHD.7.1.Atmos-GRP"
    (row,) = _rows(name)
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["TrueHD", "DD", "Atmos"]
    assert row["_meta"][nfo_audio.FROM_NFO_KEY] == ["DD"]
    # MediaInfo marks Atmos on the track: without the marker there is none.
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "7.1", exact=True)
    (row,) = _rows(name)
    assert row["_meta"]["audio"] == ["TrueHD", "DD"]
    assert row["_meta"][nfo_audio.SOURCE_KEY] == "mediainfo"
    # A row already given the NFO's answer takes the newer one.
    nfo_audio._remember(_key(name), ["TrueHD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["TrueHD", "Atmos"]
    assert row["_meta"][nfo_audio.SOURCE_KEY] == "nfo"

    # A name that tags the object audio alone keeps it on a bed it can ride.
    alone = "Some.Movie.2007.2160p.UHD.BluRay.Atmos.7.1.x265-GRP"
    (row,) = _rows(alone)
    assert row["_meta"]["audio"] == ["Atmos"]
    nfo_audio._remember(_key(alone), ["TrueHD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert sorted(row["_meta"]["audio"]) == ["Atmos", "TrueHD"]
    # DTS:X does not ride on TrueHD.
    dtsx = "Some.Movie.2008.2160p.UHD.BluRay.DTS-X.7.1.x265-GRP"
    (row,) = _rows(dtsx)
    nfo_audio._remember(_key(dtsx), ["TrueHD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["TrueHD"]
    nfo_audio._remember(_key(dtsx), ["DTS-HD MA"], "7.1", sure=True)
    (row,) = _rows(dtsx)
    assert sorted(row["_meta"]["audio"]) == ["DTS-HD MA", "DTS:X"]


def test_a_guessed_atmos_gives_way_to_what_was_read():
    from resources.lib.filter_atmos import INFERRED_KEY

    show = "Some.Show.S03E02.2160p.AMZN.WEB-DL."
    labelled = show + "DD+5.1.Atmos.DoVi.H.265-playWEB"
    plain = show + "DDP5.1.DV.H.265-NTb"
    rows = _rows(labelled, plain)
    assert rows[1]["_meta"].get(INFERRED_KEY) is True
    # MediaInfo for the unlabelled copy: Dolby Digital Plus, no Atmos.
    nfo_audio._remember(_key(plain), ["DD+"], "5.1", exact=True)
    filter_module.attach_metadata(rows)
    assert rows[1]["_meta"]["audio"] == ["DD+"]
    assert INFERRED_KEY not in rows[1]["_meta"]
    # And it is not guessed again on the next pass, or on a fresh parse.
    filter_module.attach_metadata(rows)
    assert rows[1]["_meta"]["audio"] == ["DD+"]
    fresh = _rows(labelled, plain)
    assert fresh[1]["_meta"]["audio"] == ["DD+"]
    assert INFERRED_KEY not in fresh[1]["_meta"]
    # The labelled copy is as its name says.
    assert sorted(fresh[0]["_meta"]["audio"]) == ["Atmos", "DD+"]


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
    keys = []
    replies = {}

    def do_GET(self):  # pylint: disable=invalid-name
        type(self).requests.append(self.path)
        type(self).keys.append(self.headers.get("X-API-Key"))
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
    _Handler.keys = []
    _Handler.replies = {
        "/nfo/plain": (200, NAMELESS_NFO.encode()),
        "/nfo/named": (200, SCENE_NFO.encode()),
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
    nfo_audio._remember(_key(name), ["DD", "TrueHD"], "7.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["audio"] == ["DD", "TrueHD"]
    assert row["_meta"]["channels"] == "7.1"
    # And it stays so when applied again.
    filter_module.attach_metadata([row])
    assert row["_meta"]["channels"] == "7.1"

    # What was read gives the count, also for the name's own main track.
    name = "Some.Movie.2004.2160p.UHD.BluRay.REMUX.TrueHD.7.1.Atmos-GRP"
    (row,) = _rows(name)
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "5.1", sure=True)
    filter_module.attach_metadata([row])
    assert row["_meta"]["channels"] == "5.1"
    # With no count read, the name's stays.
    nfo_audio._remember(_key(name), ["TrueHD", "DD"], "", sure=True)
    (row,) = _rows(name)
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


# --- MediaInfo first --------------------------------------------------------

_GUID = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
_MEDIAINFO_PATH = "/api/v1/releases/{}/mediainfo".format(_GUID)
_NOT_FOUND = json.dumps(
    {"error": {"code": "mediainfo_missing", "message": "No MediaInfo"}}
).encode()


def _track(kind, **fields):
    return '<track type="{}">{}</track>'.format(
        kind,
        "".join("<{0}>{1}</{0}>".format(name, value) for name, value in fields.items()),
    )


def _report(*tracks):
    """A MediaInfo XML report, laid out as an indexer returned real ones."""
    return (
        '\ufeff<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MediaInfo xmlns="https://mediaarea.net/mediainfo" version="2.0">\n'
        '<creatingLibrary version="26.01">MediaInfoLib</creatingLibrary>\n'
        '<media ref="Some.Movie.mkv">\n'
        + _track("General", AudioCount=len(tracks), Format="Matroska")
        + _track("Video", Format="HEVC", Channels="8")
        + "".join(tracks)
        + _track("Text", Format="UTF-8")
        + "</media></MediaInfo>\n"
    )


_ATMOS_TRACK = _track(
    "Audio",
    Format="E-AC-3",
    Format_Commercial_IfAny="Dolby Digital Plus with Dolby Atmos",
    Format_AdditionalFeatures="JOC",
    Channels="6",
    Language="en",
)
_STEREO_TRACK = _track(
    "Audio",
    Format="E-AC-3",
    Format_Commercial_IfAny="Dolby Digital Plus",
    Channels="2",
)


def _mediainfo_reply(*tracks):
    report = _report(*tracks)
    return json.dumps(
        {"guid": _GUID, "mediainfo": {"raw": report, "utf8": report}}
    ).encode()


def _offer_api(row, base, guid=_GUID):
    row["nfo_url"] = "{}/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(base, guid)
    return row


def test_the_tracks_of_a_mediainfo_report():
    tracks = mediainfo_audio.audio_tracks(_report(_STEREO_TRACK, _ATMOS_TRACK))
    assert tracks == [
        {"audio": ["DD+"], "channels": "2.0"},
        {"audio": ["Atmos", "DD+"], "channels": "5.1"},
    ]
    assert mediainfo_audio.merge_tracks(tracks) == {
        "audio": ["DD+", "Atmos"],
        "channels": "5.1",
    }
    truehd = _track(
        "Audio",
        Format="MLP FBA",
        Format_AdditionalFeatures="16-ch",
        Format_Commercial_IfAny="Dolby TrueHD with Dolby Atmos",
        Channels="8",
    )
    dts = _track(
        "Audio",
        Format="DTS",
        Format_AdditionalFeatures="XLL",
        Format_Commercial_IfAny="DTS-HD Master Audio",
        Channels="6",
    )
    assert mediainfo_audio.audio_tracks(_report(truehd, dts)) == [
        {"audio": ["Atmos", "TrueHD"], "channels": "7.1"},
        {"audio": ["DTS-HD MA"], "channels": "5.1"},
    ]
    # A report with no audio track is a report: an empty list.
    assert mediainfo_audio.audio_tracks(_report()) == []
    # A track this add-on has no name for is a track with no formats.
    opus = _track("Audio", Format="MPEG Audio", Channels="2")
    assert mediainfo_audio.audio_tracks(_report(opus)) == [
        {"audio": [], "channels": "2.0"}
    ]


def test_an_unreadable_or_unsafe_report_is_no_report():
    unsafe = (
        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY a "aaaa">]>'
        '<MediaInfo><media><track type="Audio"><Format>&a;</Format></track>'
        "</media></MediaInfo>"
    )
    for bad in (unsafe, "", "not xml", "<MediaInfo><media>", None, 7):
        assert mediainfo_audio.audio_tracks(bad) is None


def test_the_request_is_built_from_the_nfo_link():
    request = mediainfo_audio.request_for
    url, headers = request(
        "https://indexer.example/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(_GUID)
    )
    assert url == "https://indexer.example" + _MEDIAINFO_PATH
    assert headers["X-API-Key"] == "SECRET" and "SECRET" not in url
    for bad in (
        "https://indexer.example/api?t=getnfo&apikey=SECRET",
        "https://indexer.example/api?t=getnfo&id={}".format(_GUID),
        "https://indexer.example/api?id=../../user&apikey=SECRET",
        "https://indexer.example/api?id=a%2Fb%3Fc%3Dd1234&apikey=SECRET",
        "https://indexer.example/api?id=short&apikey=SECRET",
        "ftp://indexer.example/api?id={}&apikey=SECRET".format(_GUID),
        "https:///api?id={}&apikey=SECRET".format(_GUID),
        "https://indexer.example/api?id={}&apikey={}".format(_GUID, "k" * 200),
        "",
        None,
        5,
    ):
        assert request(bad) is None, bad


def test_what_a_reply_means():
    read = mediainfo_audio.read_reply
    what, tracks = read(200, _mediainfo_reply(_ATMOS_TRACK).decode())
    assert what == mediainfo_audio.TRACKS and tracks[0]["audio"] == ["Atmos", "DD+"]
    assert read(404, _NOT_FOUND.decode()) == (mediainfo_audio.MISSING, None)
    empty = json.dumps({"guid": _GUID, "mediainfo": {"raw": "", "utf8": ""}})
    assert read(200, empty) == (mediainfo_audio.MISSING, None)
    for status, body in (
        (404, "<html>Not Found</html>"),
        (404, ""),
        (200, "<html>Welcome</html>"),
        (200, "{}"),
        (200, json.dumps({"mediainfo": "text"})),
        (200, "[1, 2]"),
        (400, json.dumps({"error": {"code": "bad_request"}})),
        (301, ""),
    ):
        assert read(status, body) == (mediainfo_audio.NO_API, None), (status, body)
    for status in (401, 403, 429):
        assert read(status, "") == (mediainfo_audio.REFUSED, None)
    for status in (None, 500, 503):
        assert read(status, "") == (mediainfo_audio.FAILED, None)


def test_mediainfo_is_asked_for_first_and_the_nfo_is_not_read(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (
        200,
        _mediainfo_reply(_ATMOS_TRACK, _STEREO_TRACK),
    )
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    name = "Some.Show.S01E01.2160p.AMZN.WEB-DL.DDP5.1.H.265-GRP"
    rows = _rows(name)
    _offer_api(rows[0], indexer)

    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(rows, _ttl) is True
    # One request: the key went in the header and is not in the path.
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert _Handler.keys == ["SECRET"]
    assert not any("SECRET" in call.args[0] for call in log.call_args_list)
    assert any("MediaInfo for" in call.args[0] for call in log.call_args_list)

    filter_module.attach_metadata(rows)
    assert sorted(rows[0]["_meta"]["audio"]) == ["Atmos", "DD+"]
    assert rows[0]["_meta"]["channels"] == "5.1"
    saved = json.loads(store.read_text())
    assert saved["rest"]["127.0.0.1"]["ok"] is True
    assert "SECRET" not in store.read_text()


def test_a_release_with_no_mediainfo_has_its_nfo_read(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (404, _NOT_FOUND)
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN)
    _offer_api(rows[0], indexer)
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert [path.split("?")[0] for path in _Handler.requests] == [
        _MEDIAINFO_PATH,
        "/api",
    ]
    assert nfo_audio._known(_key(PLAIN))["audio"] == ["DTS-HD MA"]
    # The indexer has the call: the next release is asked the same way.
    assert nfo_audio._has_mediainfo_call("127.0.0.1")


def test_a_report_that_names_no_known_audio_falls_back_to_the_nfo(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (
        200,
        _mediainfo_reply(_track("Audio", Format="MPEG Audio", Channels="2")),
    )
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN)
    _offer_api(rows[0], indexer)
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert len(_Handler.requests) == 2
    assert nfo_audio._known(_key(PLAIN))["audio"] == ["DTS-HD MA"]


def test_an_indexer_without_the_call_is_not_asked_for_it_again(indexer, store):
    # No reply set for the MediaInfo path: the stand-in answers a bare 404.
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN, OTHER)
    _offer_api(rows[0], indexer)
    _offer_api(rows[1], indexer, guid="ffffffff-0000-1111-2222-333333333333")
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    paths = [path.split("?")[0] for path in _Handler.requests]
    # Asked once for MediaInfo, then only for NFOs.
    assert paths == [_MEDIAINFO_PATH, "/api", "/api"]
    assert json.loads(store.read_text())["rest"]["127.0.0.1"]["ok"] is False

    # The next run remembers, and a month later it asks again.
    nfo_audio.forget_all()
    more = _rows(THIRD)
    _offer_api(more[0], indexer)
    assert filter_module.learn_nfo_audio(more, _ttl) is True
    assert len(_Handler.requests) == 4 and _Handler.requests[-1].startswith("/api?")
    another = _rows(FOURTH)
    _offer_api(another[0], indexer)
    with patch("resources.lib.nfo_audio.time.time", return_value=10**10):
        filter_module.learn_nfo_audio(another, _ttl)
    assert _Handler.requests[4] == _MEDIAINFO_PATH


def test_a_refusal_from_an_indexer_never_seen_to_have_the_call_reads_the_nfo(
    indexer,
):
    _Handler.replies[_MEDIAINFO_PATH] = (403, b"Forbidden")
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN)
    _offer_api(rows[0], indexer)
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert not nfo_audio._host_paused("127.0.0.1")
    assert not nfo_audio._may_ask_mediainfo("127.0.0.1")


def test_a_refusal_from_an_indexer_with_the_call_pauses_it(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (403, b"Forbidden")
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    nfo_audio._note_rest("127.0.0.1", True)
    rows = _rows(PLAIN, OTHER)
    _offer_api(rows[0], indexer)
    _offer_api(rows[1], indexer, guid="ffffffff-0000-1111-2222-333333333333")
    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(rows, _ttl) is False
    # One request: no NFO read, and the second release was not asked about.
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert nfo_audio._host_paused("127.0.0.1")
    assert nfo_audio._known(_key(PLAIN))["failed"] is True
    assert not any("SECRET" in call.args[0] for call in log.call_args_list)


def test_too_many_requests_pauses_any_indexer(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (429, b"slow down")
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN)
    _offer_api(rows[0], indexer)
    with patch("resources.lib.nfo_audio.xbmc.log"):
        assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert nfo_audio._host_paused("127.0.0.1")


def test_a_server_error_on_the_call_reads_the_nfo_and_decides_nothing(indexer):
    _Handler.replies[_MEDIAINFO_PATH] = (503, b"busy")
    _Handler.replies["/api"] = (200, NAMELESS_NFO.encode())
    rows = _rows(PLAIN)
    _offer_api(rows[0], indexer)
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert len(_Handler.requests) == 2
    assert nfo_audio._may_ask_mediainfo("127.0.0.1")
    assert not nfo_audio._has_mediainfo_call("127.0.0.1")


def test_an_odd_rest_record_in_the_store_is_ignored(store):
    store.write_text(
        json.dumps(
            {
                "v": 1,
                "releases": {},
                "hosts": {},
                "rest": {
                    "good.example": {"ok": False, "at": 10**10},
                    "bad.example": {"ok": "no", "at": 1},
                    "worse.example": "junk",
                    "5": {"ok": True},
                },
            }
        )
    )
    nfo_audio.forget_all()
    assert not nfo_audio._may_ask_mediainfo("good.example")
    for host in ("bad.example", "worse.example", "5", "new.example"):
        assert nfo_audio._may_ask_mediainfo(host)


def test_the_names_atmos_does_not_move_onto_a_new_main_track():
    from resources.lib.release_display import display_name

    # The name tags the Dolby Digital Plus track; the NFO lists a plain
    # TrueHD 7.1 track beside it.
    name = "Some.Movie.2004.2160p.UHD.BluRay.DDP.5.1.Atmos.x265-GRP"
    (row,) = _rows(name)
    assert sorted(row["_meta"]["audio"]) == ["Atmos", "DD+"]
    found = nfo_audio.audio_from_nfo(
        "Audio #1\nFormat : MLP FBA\nChannel(s) : 8 channels\n\n"
        "Audio #2\nFormat : E-AC-3 JOC\nChannel(s) : 6 channels\n\n"
    )
    assert found == {"audio": ["TrueHD", "DD+"], "channels": "7.1"}
    nfo_audio._remember(_key(name), found["audio"], found["channels"], sure=True)
    filter_module.attach_metadata([row])
    assert sorted(row["_meta"]["audio"]) == ["DD+", "TrueHD"]
    assert row["_meta"]["channels"] == "7.1"
    assert "Atmos" not in display_name(row)
    # A second pass, and a fresh parse of the name, end the same way.
    filter_module.attach_metadata([row])
    assert sorted(row["_meta"]["audio"]) == ["DD+", "TrueHD"]
    (again,) = _rows(name)
    assert sorted(again["_meta"]["audio"]) == ["DD+", "TrueHD"]

    # When the new main track carries the Atmos, it stays.
    other = "Some.Movie.2005.2160p.UHD.BluRay.DDP.5.1.Atmos.x265-GRP"
    nfo_audio._remember(_key(other), ["Atmos", "TrueHD", "DD+"], "7.1", sure=True)
    (row,) = _rows(other)
    assert sorted(row["_meta"]["audio"]) == ["Atmos", "DD+", "TrueHD"]


# --- is the NFO about this release? -----------------------------------------


def _whose(text, title):
    return nfo_audio.whose_nfo(text, title, filter_module.release_identity)


def test_an_nfo_that_names_the_release_is_confirmed():
    assert _whose(SCENE_NFO, PLAIN) == nfo_audio.CONFIRMED
    # Written with spaces, in other case, or as the file inside the release.
    spaced = "Some Movie 2004 MULTi 1080p BluRay x264-GRP\nAudio: DTS-HD MA 5.1\n"
    assert _whose(spaced, PLAIN) == nfo_audio.CONFIRMED
    assert _whose(SCENE_NFO.lower(), PLAIN + ".mkv") == nfo_audio.CONFIRMED
    # Another spelling of the same film from the same group.
    other = "Complete name : Some.Movie.2004.FRENCH.1080p.BluRay.x264-GRP.mkv\n"
    assert _whose(other, PLAIN) == nfo_audio.CONFIRMED


def test_an_nfo_about_another_release_is_wrong():
    wrong = nfo_audio.WRONG
    # Another film.
    assert _whose(SCENE_NFO, "Other.Film.2011.1080p.BluRay.x264-GRP") == wrong
    # The same film from another group: another encode, other audio.
    assert _whose(SCENE_NFO, OTHER) == wrong
    # Another year, another episode.
    assert _whose(SCENE_NFO, PLAIN.replace("2004", "1975")) == wrong
    episode = "Release: Some.Show.S01E02.1080p.WEB-DL.DDP5.1.H.264-GRP\n"
    assert _whose(episode, "Some.Show.S01E03.1080p.WEB-DL.H.264-GRP") == wrong
    assert _whose(episode, "Some.Show.2019.1080p.WEB-DL.H.264-GRP") == wrong
    same = "Some.Show.S01E02.1080p.WEB-DL.DDP5.1.H.264-GRP"
    assert _whose(episode, same) == nfo_audio.CONFIRMED


def test_an_nfo_that_names_no_release_is_unconfirmed():
    unconfirmed = nfo_audio.UNCONFIRMED
    assert _whose(NAMELESS_NFO, PLAIN) == unconfirmed
    assert _whose(MEDIAINFO_NFO, PLAIN) == unconfirmed
    assert _whose("", PLAIN) == unconfirmed
    # A site address or a version number is not a release name.
    assert _whose("visit www.some.site.example\nv1.2.3.4\n", PLAIN) == unconfirmed
    # The same film named without a group decides nothing.
    assert _whose("File: Some.Movie.2004.1080p.mkv\n", PLAIN) == unconfirmed
    # Nothing to compare with, or a parser that fails.
    assert nfo_audio.whose_nfo(SCENE_NFO, OTHER, None) == unconfirmed

    def broken(_name):
        raise ValueError("no")

    assert nfo_audio.whose_nfo(SCENE_NFO, OTHER, broken) == unconfirmed
    # A very short name is not looked for inside the text.
    assert _whose("x 2004 y", "X.2004") == unconfirmed


def test_a_confirmed_nfo_replaces_and_an_unconfirmed_one_only_adds(indexer):
    name = "Some.Movie.2004.MULTi.1080p.BluRay.DTS.5.1.x264-GRP"
    _Handler.replies["/nfo/own"] = (
        200,
        ("Release: " + name + "\nAudio: English DTS-HD MA 7.1\n").encode(),
    )
    rows = _rows(name)
    _offer(rows[0], indexer, "own")
    assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert nfo_audio._known(_key(name))["sure"] is True
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == ["DTS-HD MA"]
    assert rows[0]["_meta"]["channels"] == "7.1"
    assert rows[0]["_meta"][nfo_audio.SOURCE_KEY] == "nfo"

    # The same text without the name: the name's DTS stays, the rest is added.
    nfo_audio.forget_all()
    _Handler.replies["/nfo/bare"] = (200, b"Audio: English DTS-HD MA 7.1\n")
    other = name.replace("-GRP", "-OTHER")
    rows = _rows(other)
    _offer(rows[0], indexer, "bare")
    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(rows, _ttl) is True
    assert any("does not name the release" in c.args[0] for c in log.call_args_list)
    assert nfo_audio._known(_key(other))["sure"] is False
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == ["DTS", "DTS-HD MA"]
    assert rows[0]["_meta"][nfo_audio.SOURCE_KEY] == nfo_audio.UNCONFIRMED
    assert rows[0]["_meta"][nfo_audio.FROM_NFO_KEY] == ["DTS-HD MA"]
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == ["DTS", "DTS-HD MA"]


def test_another_releases_nfo_is_not_used(indexer):
    # The indexer attached the NFO of another group's release.
    rows = _rows(OTHER)
    _offer(rows[0], indexer, "named")
    with patch("resources.lib.nfo_audio.xbmc.log") as log:
        assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert any("about another release" in c.args[0] for c in log.call_args_list)
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == []
    assert nfo_audio.SOURCE_KEY not in rows[0]["_meta"]
    # It is settled: not asked for again.
    assert filter_module.learn_nfo_audio(rows, _ttl) is False
    assert len(_Handler.requests) == 1


def test_an_unconfirmed_nfo_leaves_a_guessed_atmos_alone():
    from resources.lib.filter_atmos import INFERRED_KEY

    show = "Some.Show.S03E02.2160p.AMZN.WEB-DL."
    labelled = show + "DD+5.1.Atmos.DoVi.H.265-playWEB"
    plain = show + "DDP5.1.DV.H.265-NTb"
    nfo_audio._remember(_key(plain), ["DD+", "AAC"], "5.1")
    rows = _rows(labelled, plain)
    assert rows[1]["_meta"].get(INFERRED_KEY) is True
    assert sorted(rows[1]["_meta"]["audio"]) == ["AAC", "Atmos", "DD+"]
    once = json.dumps(rows[1]["_meta"], sort_keys=True)
    filter_module.attach_metadata(rows)
    assert json.dumps(rows[1]["_meta"], sort_keys=True) == once


def test_a_guess_is_taken_back_when_its_labelled_copy_was_read_and_has_none():
    from resources.lib.filter_atmos import INFERRED_KEY

    show = "Some.Show.S03E02.2160p.AMZN.WEB-DL."
    labelled = show + "DD+5.1.Atmos.DoVi.H.265-playWEB"
    plain = show + "DDP5.1.DV.H.265-NTb"
    rows = _rows(labelled, plain)
    assert rows[1]["_meta"].get(INFERRED_KEY) is True
    # MediaInfo for the copy labelled Atmos: Dolby Digital Plus, no Atmos.
    nfo_audio._remember(_key(labelled), ["DD+"], "5.1", exact=True)
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["audio"] == ["DD+"]
    assert rows[1]["_meta"]["audio"] == ["DD+"]
    assert INFERRED_KEY not in rows[1]["_meta"]
    # Stable, and the same on a fresh parse.
    once = json.dumps([row["_meta"] for row in rows], sort_keys=True)
    filter_module.attach_metadata(rows)
    assert json.dumps([row["_meta"] for row in rows], sort_keys=True) == once
    fresh = _rows(labelled, plain)
    assert [row["_meta"]["audio"] for row in fresh] == [["DD+"], ["DD+"]]

    # Another copy labelled by its name still gives.
    third = show + "DDP5.1.Atmos.DV.H.265-FLUX"
    rows = _rows(labelled, plain, third)
    assert rows[1]["_meta"].get(INFERRED_KEY) is True
    assert rows[0]["_meta"]["audio"] == ["DD+"]


def test_a_bare_dts_x_names_no_lossless_bed():
    assert nfo_audio.audio_from_nfo("Audio: English DTS:X 7.1\n") == {
        "audio": ["DTS:X"],
        "channels": "7.1",
    }
    assert nfo_audio.audio_from_nfo("Audio: DTS-X 7.1\n")["audio"] == ["DTS:X"]
    # The lossless bed is named when the report identifies it.
    block = "Audio\nFormat : DTS XLL X\nChannel(s) : 8 channels\n\n"
    assert sorted(nfo_audio.audio_from_nfo(block)["audio"]) == ["DTS-HD MA", "DTS:X"]
    lines = "Audio: English DTS-HD MA 7.1 DTS:X\n"
    assert sorted(nfo_audio.audio_from_nfo(lines)["audio"]) == ["DTS-HD MA", "DTS:X"]
    tracks = mediainfo_audio.audio_tracks(
        _report(
            _track(
                "Audio",
                Format="DTS",
                Format_AdditionalFeatures="XLL X",
                Format_Commercial_IfAny="DTS-HD MA + DTS:X",
                Channels="8",
            ),
            _track("Audio", Format="DTS", Format_AdditionalFeatures="X", Channels="6"),
        )
    )
    assert sorted(tracks[0]["audio"]) == ["DTS-HD MA", "DTS:X"]
    assert "DTS-HD MA" not in tracks[1]["audio"]
