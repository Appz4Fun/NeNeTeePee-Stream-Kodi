# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""What each indexer offers for a release's NFO and MediaInfo, and how the
add-on asks: the table, the links, direct-indexer mode, and the video facts."""

import http.server
import json
import threading
from unittest.mock import patch

import pytest
from resources.lib import (
    direct_indexers,
    hydra,
    indexer_media,
    mediainfo_video,
    newznab_attrs,
    nfo_audio,
)
from resources.lib import filter as filter_module
from resources.lib.indexer_media import GETNFO, INFO, REST_V1, Offer
from resources.lib.release_display import dynamic_range
from resources.lib.xml_safety import safe_fromstring

_NS = 'xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"'
_ID = "0123456789abcdef0123456789abcdef"
_OTHER_ID = "fedcba9876543210fedcba9876543210"
PLAIN = "Some.Movie.2004.2160p.WEB-DL.H.265-GRP"
OTHER = "Some.Movie.2004.1080p.BluRay.x264-OTHER"


def _ttl(key, default=""):
    return default


@pytest.fixture(name="store", autouse=True)
def _store(tmp_path):
    nfo_audio.forget_all()
    path = tmp_path / "nfo_audio.json"
    with patch.object(nfo_audio, "_store_path", return_value=str(path)):
        yield path
    nfo_audio.forget_all()


def _item(attrs, inner=""):
    extra = "".join(
        '<newznab:attr name="{}" value="{}"/>'.format(name, value)
        for name, value in attrs
    )
    return safe_fromstring("<item {}>{}{}</item>".format(_NS, inner, extra))


def _rows(*titles):
    rows = [
        {"title": title, "link": "http://hydra/{}".format(n), "size": str(10**10)}
        for n, title in enumerate(titles)
    ]
    filter_module.attach_metadata(rows)
    return rows


# --- the table --------------------------------------------------------------


def test_an_indexer_is_found_by_its_site_and_not_by_a_look_alike():
    assert indexer_media.offer_for("treasure-maps.com") == Offer(GETNFO, REST_V1)
    assert indexer_media.offer_for("API.Drunkenslug.com") == Offer(INFO, None)
    assert indexer_media.offer_for("nzbfinder.ws") == Offer(None, None)
    for host in ("eviltreasure-maps.com", "treasure-maps.com.example", "", None):
        assert indexer_media.offer_for(host) is None, host


def test_which_indexers_are_asked_for_mediainfo():
    assert indexer_media.serves_mediainfo("treasure-maps.com") is True
    assert indexer_media.serves_mediainfo("api.althub.co.za") is False
    # Not in the table: the add-on probes it.
    assert indexer_media.serves_mediainfo("indexer.example") is None


def test_every_known_indexer_names_calls_the_add_on_can_make():
    for site, offer in indexer_media.KNOWN.items():
        assert site == site.lower() and "." in site
        assert offer.nfo in (GETNFO, INFO, None), site
        assert offer.mediainfo in (REST_V1, None), site


# --- the links --------------------------------------------------------------


def test_a_key_sent_the_rss_way_is_moved_to_where_the_api_takes_it():
    sent = "https://indexer.example/api?t=info&id={}&i=77&r=SECRET".format(_ID)
    assert indexer_media.usable_nfo_link(sent) == (
        "https://indexer.example/api?t=info&id={}&apikey=SECRET".format(_ID)
    )
    kept = "https://indexer.example/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(_ID)
    assert indexer_media.usable_nfo_link(kept) == kept
    assert indexer_media.usable_nfo_link(None) == ""


def test_a_link_with_no_flag_is_taken_when_it_is_an_nfo_call():
    link = "https://indexer.example/api?t=info&amp;id={}&amp;r=SECRET".format(_ID)
    item = _item((("info", link), ("hydraIndexerHost", "api.indexer.example")))
    assert newznab_attrs.nfo_link(item) == (
        "https://indexer.example/api?t=info&id={}&apikey=SECRET".format(_ID)
    )
    # A direct indexer has no hydraIndexerHost: its own host is compared.
    item = _item((("info", link),))
    assert newznab_attrs.nfo_link(item) == ""
    assert newznab_attrs.nfo_link(item, indexer_host="indexer.example").endswith(
        "apikey=SECRET"
    )
    assert newznab_attrs.nfo_link(item, indexer_host="other.example") == ""


def test_the_release_id_is_read_from_a_details_link():
    cases = {
        "https://indexer.example/details/{}#comments".format(_ID): _ID,
        "https://indexer.example/details/{}".format(_ID): _ID,
        "https://indexer.example/geekseek.php?guid={}".format(_ID): _ID,
        "https://indexer.example/details/0a1b2c3d-4e5f-6789-abcd-ef0123456789": (
            "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
        ),
        "https://indexer.example/browse/{}".format(_ID): "",
        "https://indexer.example/details/a b": "",
        "ftp://indexer.example/details/{}".format(_ID): "",
        "": "",
    }
    for link, expected in cases.items():
        assert indexer_media.id_from_details_link(link) == expected, link


def test_a_reference_takes_its_key_from_the_same_indexer_only():
    ref = indexer_media.media_ref("https", "indexer.example", _ID)
    assert ref == "https://indexer.example/api?id={}".format(_ID)
    assert not indexer_media.has_key(ref)
    keyed = "https://indexer.example/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(
        _OTHER_ID
    )
    full = indexer_media.with_key_from(ref, keyed)
    assert full == "https://indexer.example/api?id={}&apikey=SECRET".format(_ID)
    url, headers = indexer_media.mediainfo_request(full)
    assert url == "https://indexer.example/api/v1/releases/{}/mediainfo".format(_ID)
    assert headers["X-API-Key"] == "SECRET" and "SECRET" not in url
    # Another host, another scheme or port, or no key to give: nothing.
    for other in (
        keyed.replace("indexer.example", "other.example"),
        keyed.replace("indexer.example", "api.indexer.example"),
        keyed.replace("https://", "http://"),
        keyed.replace("indexer.example", "indexer.example:8443"),
        "https://indexer.example/api?t=getnfo&id={}".format(_OTHER_ID),
        "",
    ):
        assert indexer_media.with_key_from(ref, other) == "", other
    assert indexer_media.media_ref("ftp", "indexer.example", _ID) == ""
    assert indexer_media.media_ref("https", "indexer.example", "no id") == ""


# --- NZBHydra2 results ------------------------------------------------------


def _hydra_item(host, flagged):
    inner = (
        "<title>{}</title><link>http://hydra/getnzb/api/1</link>"
        "<guid>1234567890123</guid>"
        "<comments>https://{}/details/{}#comments</comments>"
    ).format(PLAIN, host, _ID)
    attrs = [
        ("size", "1000"),
        ("guid", "1234567890123"),
        ("hydraIndexerName", "Indexer"),
        ("hydraIndexerHost", host),
    ]
    if flagged:
        link = "https://{}/api?t=getnfo&amp;id={}&amp;apikey=SECRET&amp;raw=1"
        attrs += [("nfo", "1"), ("info", link.format(host, _ID))]
    else:
        attrs.append(("nfo", "0"))
    return _item(attrs, inner)


def test_a_hydra_result_without_a_link_is_referenced_where_mediainfo_is_served():
    row = hydra._build_result(_hydra_item("treasure-maps.com", False))
    assert row["media_ref"] == "https://treasure-maps.com/api?id={}".format(_ID)
    assert "nfo_url" not in row
    # With a link of its own, the link is all it needs.
    row = hydra._build_result(_hydra_item("treasure-maps.com", True))
    assert "media_ref" not in row and row["nfo_url"].endswith("raw=1")
    # An indexer without the call, or one not in the table: nothing to ask.
    for host in ("abnzb.com", "indexer.example"):
        row = hydra._build_result(_hydra_item(host, False))
        assert "media_ref" not in row and "nfo_url" not in row, host


def test_a_details_link_on_another_site_gives_no_reference():
    item = _item(
        (("hydraIndexerHost", "treasure-maps.com"),),
        "<comments>https://other.example/details/{}</comments>".format(_ID),
    )
    assert newznab_attrs.media_ref_link(item) == ""
    # NZBHydra2's own number is not the indexer's id.
    item = _item(
        (("hydraIndexerHost", "treasure-maps.com"), ("guid", "1234567890123")),
        "<comments>https://treasure-maps.com/</comments>",
    )
    assert newznab_attrs.media_ref_link(item) == ""


# --- direct indexers --------------------------------------------------------


def _direct_item(host, *attrs):
    inner = "<guid>https://{}/details/{}</guid>".format(host, _ID)
    return _item(attrs, inner)


def test_a_direct_indexers_own_link_is_used_when_it_points_at_that_indexer():
    link = "https://abnzb.com/api?t=getnfo&amp;id={}&amp;apikey=SENT&amp;raw=1"
    item = _direct_item("abnzb.com", ("nfo", "1"), ("info", link.format(_ID)))
    links = newznab_attrs.direct_media_links(item, "https://abnzb.com/api", "MINE")
    assert links == {"nfo_url": link.format(_ID).replace("&amp;", "&")}
    # Sent by a result of another indexer: built from the add-on's own key.
    links = newznab_attrs.direct_media_links(item, "https://squareeyed.org/api", "MINE")
    assert links == {
        "nfo_url": "https://squareeyed.org/api?t=getnfo&id={}&apikey=MINE&raw=1".format(
            _ID
        )
    }


def test_a_direct_indexer_is_asked_with_the_call_it_has():
    flagged = (("nfo", "1"),)
    cases = (
        # host, attrs, expected links
        ("abnzb.com", flagged, {"nfo_url": "t=getnfo&id={}&apikey=MINE&raw=1"}),
        ("drunkenslug.com", flagged, {"nfo_url": "t=info&id={}&apikey=MINE"}),
        # No NFO call there, and no MediaInfo call.
        ("nzbfinder.ws", flagged, {}),
        # Not flagged: no NFO to ask for, and no MediaInfo call there.
        ("abnzb.com", (("nfo", "0"),), {}),
        ("abnzb.com", (), {}),
        # MediaInfo is served: a keyed reference, flagged or not.
        ("treasure-maps.com", (), {"media_ref": "id={}&apikey=MINE"}),
        # Not in the table: newznab's getnfo when flagged, else the probe.
        ("indexer.example", flagged, {"nfo_url": "t=getnfo&id={}&apikey=MINE&raw=1"}),
        ("indexer.example", (), {"media_ref": "id={}&apikey=MINE"}),
    )
    for host, attrs, expected in cases:
        api_url = "https://api.{}/api".format(host)
        links = newznab_attrs.direct_media_links(
            _direct_item(host, *attrs), api_url, "MINE"
        )
        wanted = {
            name: "{}?{}".format(api_url, query.format(_ID))
            for name, query in expected.items()
        }
        assert links == wanted, (host, attrs)


def test_a_direct_result_with_no_id_or_no_key_is_asked_about_nothing():
    item = _item((("nfo", "1"),), "<guid>not-a-link</guid>")
    assert not newznab_attrs.direct_media_links(item, "https://abnzb.com/api", "K")
    item = _direct_item("abnzb.com", ("nfo", "1"))
    assert not newznab_attrs.direct_media_links(item, "https://abnzb.com/api", "")
    assert not newznab_attrs.direct_media_links(item, "not a url", "K")
    # The guid attribute serves when the guid element is not a link.
    item = _item((("nfo", "1"), ("guid", _ID)), "<guid>{}</guid>".format(_ID))
    links = newznab_attrs.direct_media_links(item, "https://abnzb.com/api", "K")
    assert links["nfo_url"].startswith("https://abnzb.com/api?t=getnfo&id=" + _ID)


_DIRECT_RSS = (
    '<?xml version="1.0"?><rss version="2.0" {ns}><channel><item>'
    "<title>{title}</title>"
    "<guid>https://indexer.example/details/{id}</guid>"
    "<link>https://indexer.example/api?t=get&amp;id={id}&amp;apikey=MINE</link>"
    '<newznab:attr name="size" value="1000"/>'
    '<newznab:attr name="nfo" value="1"/>'
    "</item></channel></rss>"
).format(ns=_NS, title=PLAIN, id=_ID)


def test_direct_results_carry_the_calls_only_with_the_configured_indexer():
    configured = {
        "id": "x",
        "label": "Indexer",
        "api_url": "https://indexer.example",
        "api_key": "MINE",
    }
    (row,), error = direct_indexers.parse_results(_DIRECT_RSS, "Indexer", configured)
    assert error is None
    assert row["nfo_url"] == (
        "https://indexer.example/api?t=getnfo&id={}&apikey=MINE&raw=1".format(_ID)
    )
    (row,), error = direct_indexers.parse_results(_DIRECT_RSS, "Indexer")
    assert error is None and "nfo_url" not in row and "media_ref" not in row


# --- MediaInfo's video facts ------------------------------------------------


def test_the_dynamic_range_mediainfo_names():
    cases = (
        # HDR format text, transfer, bit depth -> facts
        (
            "Dolby Vision / SMPTE ST 2086 / dvhe.08 / HDR10 / HDR10",
            "PQ",
            "10",
            {
                "hdr": ["Dolby Vision", "HDR10"],
                "dv_profile": "8",
                "bit_depth": "10",
                "hdr_exact": True,
            },
        ),
        (
            "Dolby Vision, Version 1.0, dvhe.05.06, BL+RPU",
            "",
            "10 bits",
            {
                "hdr": ["Dolby Vision"],
                "dv_profile": "5",
                "bit_depth": "10",
                "hdr_exact": True,
            },
        ),
        (
            "SMPTE ST 2094 App 4, Version 1, HDR10+ Profile B compatible",
            "PQ",
            "10",
            {"hdr": ["HDR10+"], "bit_depth": "10", "hdr_exact": True},
        ),
        (
            "SMPTE ST 2086, HDR10 compatible",
            "PQ",
            "10",
            {"hdr": ["HDR10"], "bit_depth": "10", "hdr_exact": True},
        ),
        # No HDR format named: a PQ transfer says HDR10 at least, not exactly.
        ("", "PQ", "10", {"hdr": ["HDR10"], "bit_depth": "10"}),
        ("", "HLG", "10", {"hdr": ["HLG"], "bit_depth": "10"}),
        # Standard dynamic range: said by the transfer, or by eight bits.
        ("", "BT.709", "10", {"hdr": ["SDR"], "bit_depth": "10", "hdr_exact": True}),
        ("", "", "8 bits", {"hdr": ["SDR"], "bit_depth": "8", "hdr_exact": True}),
        # Nothing to go by.
        ("", "", "10", {"bit_depth": "10"}),
        ("", "", "", {}),
        (None, None, None, {}),
    )
    for hdr_text, transfer, depth, expected in cases:
        assert mediainfo_video.facts(hdr_text, transfer, depth) == expected, hdr_text


def _video_track(**fields):
    return '<track type="Video">{}</track>'.format(
        "".join("<{0}>{1}</{0}>".format(name, value) for name, value in fields.items())
    )


_AUDIO_TRACK = (
    '<track type="Audio"><Format>E-AC-3</Format>'
    "<Format_Commercial_IfAny>Dolby Digital Plus</Format_Commercial_IfAny>"
    "<Channels>6</Channels></track>"
)


def _report(video, audio=_AUDIO_TRACK):
    return (
        '﻿<?xml version="1.0" encoding="UTF-8"?>\n'
        '<MediaInfo xmlns="https://mediaarea.net/mediainfo" version="2.0">\n'
        '<media ref="Some.Movie.mkv"><track type="General"><Format>Matroska'
        "</Format></track>" + video + audio + "</media></MediaInfo>\n"
    )


def _reply(report):
    return json.dumps({"guid": _ID, "mediainfo": {"raw": report, "utf8": report}})


_DV_VIDEO = _video_track(
    Format="HEVC",
    BitDepth="10",
    HDR_Format="Dolby Vision / SMPTE ST 2086",
    HDR_Format_Profile="dvhe.08 / ",
    HDR_Format_Compatibility="HDR10 / HDR10",
    transfer_characteristics="PQ",
)
_SDR_VIDEO = _video_track(
    Format="HEVC", BitDepth="8", transfer_characteristics="BT.709"
)


def test_the_video_track_of_a_report_and_of_a_reply():
    expected = {
        "hdr": ["Dolby Vision", "HDR10"],
        "dv_profile": "8",
        "bit_depth": "10",
        "hdr_exact": True,
    }
    assert mediainfo_video.from_xml(_report(_DV_VIDEO)) == expected
    assert mediainfo_video.from_reply(_reply(_report(_DV_VIDEO))) == expected
    assert mediainfo_video.from_xml(_report(_SDR_VIDEO)) == {
        "hdr": ["SDR"],
        "bit_depth": "8",
        "hdr_exact": True,
    }
    # No video track, an unreadable or unsafe report, a reply that is not one.
    assert not mediainfo_video.from_xml(_report(""))
    assert not mediainfo_video.from_xml("<MediaInfo><media>")
    assert not mediainfo_video.from_xml('<!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>')
    for body in ("", "not json", "[]", '{"mediainfo": null}', None):
        assert not mediainfo_video.from_reply(body), body


_MEDIAINFO_TEXT = """
General
Complete name                            : Some.Movie.2004.2160p.WEB-DL.H.265-GRP.mkv
Format                                   : Matroska

Video
ID                                       : 1
Format                                   : HEVC
HDR format                               : Dolby Vision, Version 1.0, dvhe.05.06, BL+RPU
Bit depth                                : 10 bits
Transfer characteristics                 : PQ

Audio
ID                                       : 2
Format                                   : E-AC-3
Commercial name                          : Dolby Digital Plus
Channel(s)                               : 6 channels
Bit depth                                : 32 bits
"""


def test_the_video_block_of_an_nfo_that_is_mediainfo_text():
    assert mediainfo_video.from_nfo(_MEDIAINFO_TEXT) == {
        "hdr": ["Dolby Vision"],
        "dv_profile": "5",
        "bit_depth": "10",
        "hdr_exact": True,
    }
    # A hand-written NFO's video line names no dynamic range.
    scene = "Video.........: x264 @ 15371 kb/s\nAudio.........: DTS 5.1\n"
    assert not mediainfo_video.from_nfo(scene)
    assert not mediainfo_video.from_nfo("Video\nBit depth : 10 bits\n")
    assert not mediainfo_video.from_nfo(None)


# --- the facts on the rows --------------------------------------------------


def test_what_mediainfo_says_of_the_video_replaces_the_names_range():
    name = "Some.Movie.2004.2160p.WEB-DL.DV.HDR10Plus.H.265-GRP"
    rows = _rows(name, OTHER)
    assert rows[0]["_meta"]["hdr"] == ["Dolby Vision", "HDR10+"]
    video = {
        "hdr": ["Dolby Vision", "HDR10"],
        "dv_profile": "8",
        "bit_depth": "10",
        "hdr_exact": True,
    }
    nfo_audio._remember(
        filter_module._name_form_key(name), ["DD+"], "5.1", exact=True, video=video
    )
    filter_module.attach_metadata(rows)
    meta = rows[0]["_meta"]
    assert meta["hdr"] == ["Dolby Vision", "HDR10"]
    assert meta["dv_profile"] == "8" and meta["bit_depth"] == "10"
    assert meta[nfo_audio.NAME_HDR_KEY] == ["Dolby Vision", "HDR10+"]
    assert dynamic_range(meta) == "DV P8 HDR10"
    # The other release is untouched, and a second pass changes nothing.
    assert rows[1]["_meta"]["hdr"] == ["SDR"] and "dv_profile" not in rows[1]["_meta"]
    assert nfo_audio.apply_known(rows, filter_module._name_form_key) == 0
    assert meta["hdr"] == ["Dolby Vision", "HDR10"]


def test_a_name_that_claims_hdr_is_corrected_by_an_sdr_report():
    name = "Some.Movie.2004.2160p.WEB-DL.HDR.H.265-GRP"
    rows = _rows(name)
    nfo_audio._remember(
        filter_module._name_form_key(name),
        [],
        "",
        exact=True,
        video={"hdr": ["SDR"], "bit_depth": "8", "hdr_exact": True},
    )
    assert nfo_audio.apply_known(rows, filter_module._name_form_key) == 1
    assert rows[0]["_meta"]["hdr"] == ["SDR"]
    assert dynamic_range(rows[0]["_meta"]) == ""


def test_a_range_that_is_not_exact_only_adds_to_the_name():
    name = "Some.Movie.2004.2160p.WEB-DL.DV.H.265-GRP"
    rows = _rows(name, PLAIN)
    for title in (name, PLAIN):
        nfo_audio._remember(
            filter_module._name_form_key(title),
            [],
            "",
            exact=True,
            video={"hdr": ["HDR10"], "bit_depth": "10"},
        )
    nfo_audio.apply_known(rows, filter_module._name_form_key)
    # The name's Dolby Vision stays: an old MediaInfo cannot rule it out.
    assert rows[0]["_meta"]["hdr"] == ["Dolby Vision", "HDR10"]
    assert rows[1]["_meta"]["hdr"] == ["HDR10"]
    assert dynamic_range(rows[0]["_meta"]) == "DV HDR10"


def test_odd_video_records_in_the_store_change_nothing():
    rows = _rows(PLAIN)
    key = filter_module._name_form_key(PLAIN)
    before = dict(rows[0]["_meta"])
    for video in (
        {"hdr": "Dolby Vision"},
        {"hdr": ["Made Up"]},
        {"hdr": []},
        {"dv_profile": 8, "bit_depth": None},
        "video",
    ):
        nfo_audio._remember(key, [], "", exact=True, video=None)
        with nfo_audio._LOCK:
            nfo_audio._STORE[key]["video"] = video
        assert nfo_audio.apply_known(rows, filter_module._name_form_key) == 0, video
        assert rows[0]["_meta"] == before, video


def test_the_profile_is_shown_only_when_it_is_a_number():
    assert dynamic_range({"hdr": ["Dolby Vision"], "dv_profile": "5"}) == "DV P5"
    assert dynamic_range({"hdr": ["Dolby Vision"]}) == "DV"
    assert dynamic_range({"hdr": ["Dolby Vision"], "dv_profile": "x"}) == "DV"
    assert dynamic_range({"hdr": ["HDR10"], "dv_profile": "8"}) == "HDR10"
    # The picker's narrow column leaves the profile to the row's name.
    meta = {"hdr": ["Dolby Vision", "HDR10"], "dv_profile": "8"}
    assert dynamic_range(meta, profile=False) == "DV HDR10"


# --- asking, against a real local server ------------------------------------

_MEDIAINFO_PATH = "/api/v1/releases/{}/mediainfo".format(_ID)
_NOT_FOUND = json.dumps({"error": {"code": "mediainfo_missing"}}).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    requests = []
    keys = []
    hosts = []
    replies = {}

    def do_GET(self):  # pylint: disable=invalid-name
        type(self).requests.append(self.path)
        type(self).keys.append(self.headers.get("X-API-Key"))
        type(self).hosts.append((self.headers.get("Host") or "").split(":")[0])
        status, body = type(self).replies.get(self.path.split("?")[0], (404, b""))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # pylint: disable=arguments-differ
        return


@pytest.fixture(name="indexer")
def _indexer():
    _Handler.requests = []
    _Handler.keys = []
    _Handler.hosts = []
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
    """The table, with the local test server's names in it."""
    table = dict(indexer_media.KNOWN)
    table.update({name.replace("_", "."): offer for name, offer in hosts.items()})
    return patch.dict(indexer_media.KNOWN, table, clear=True)


def _nfo_link(base, release_id=_ID):
    return "{}/api?t=getnfo&id={}&apikey=SECRET&raw=1".format(base, release_id)


def test_a_result_without_a_link_borrows_the_key_of_the_same_indexer(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (200, _reply(_report(_DV_VIDEO)).encode())
    rows = _rows(PLAIN, OTHER)
    rows[0]["media_ref"] = "{}/api?id={}".format(indexer, _ID)
    # Further down the list, the same indexer's link carries the key.
    rows[1]["nfo_url"] = _nfo_link(indexer, _OTHER_ID)

    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        with patch("resources.lib.nfo_audio.xbmc.log") as log:
            assert nfo_audio.learn_for_top(rows[:1] + rows[1:], _key, _one) is True
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert _Handler.keys == ["SECRET"]
    logged = " ".join(call.args[0] for call in log.call_args_list)
    assert "SECRET" not in logged
    assert "names video: Dolby Vision (profile 8), HDR10, 10 bit" in logged
    assert "SECRET" not in store.read_text()

    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["hdr"] == ["Dolby Vision", "HDR10"]
    assert rows[0]["_meta"]["audio"] == ["DD+"]
    assert rows[0]["_meta"]["audio_source"] == "mediainfo"


def _key(title):
    return filter_module._name_form_key(title)


def _one(key, default=""):
    return "1" if key == nfo_audio.SETTING else default


def test_a_reference_with_no_key_to_borrow_asks_nothing(indexer):
    rows = _rows(PLAIN, OTHER)
    rows[0]["media_ref"] = "{}/api?id={}".format(indexer, _ID)
    # The only key in the search belongs to another indexer.
    rows[1]["nfo_url"] = _nfo_link(indexer.replace("127.0.0.1", "localhost"))
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _one) is False
    assert not _Handler.requests
    # Nothing was asked, so nothing is settled: a later search may ask.
    assert nfo_audio._known(_key(PLAIN)) is None


def test_an_indexer_known_to_have_no_mediainfo_call_is_not_probed(indexer, store):
    _Handler.replies["/api"] = (200, _MEDIAINFO_TEXT.encode())
    rows = _rows(PLAIN)
    rows[0]["nfo_url"] = _nfo_link(indexer)
    with _known(**{"127_0_0_1": Offer(GETNFO, None)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
    # One request, for the NFO: no probe of the REST path.
    assert [path.split("?")[0] for path in _Handler.requests] == ["/api"]
    assert not json.loads(store.read_text())["rest"]
    # The NFO is MediaInfo text and names the release: its video block counts.
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["hdr"] == ["Dolby Vision"]
    assert rows[0]["_meta"]["dv_profile"] == "5"
    assert rows[0]["_meta"]["audio"] == ["DD+"]


def test_a_refusal_from_an_indexer_in_the_table_is_a_refusal(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (401, b"{}")
    _Handler.replies["/api"] = (200, _MEDIAINFO_TEXT.encode())
    rows = _rows(PLAIN)
    rows[0]["nfo_url"] = _nfo_link(indexer)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    # Not taken for "no such call": the NFO is not read, the indexer rests.
    assert _Handler.requests == [_MEDIAINFO_PATH]
    saved = json.loads(store.read_text())
    assert "127.0.0.1" in saved["hosts"] and not saved["rest"]


def test_an_odd_answer_from_an_indexer_in_the_table_decides_nothing(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (200, b"<html>maintenance</html>")
    _Handler.replies["/api"] = (200, _MEDIAINFO_TEXT.encode())
    rows = _rows(PLAIN)
    rows[0]["nfo_url"] = _nfo_link(indexer)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
    # The NFO was read, and the indexer is not written down as lacking the call.
    assert len(_Handler.requests) == 2
    assert not json.loads(store.read_text())["rest"]


def test_the_listing_on_an_indexer_with_mediainfo_is_the_one_asked(indexer):
    other = indexer.replace("127.0.0.1", "localhost")
    _Handler.replies[_MEDIAINFO_PATH] = (200, _reply(_report(_SDR_VIDEO)).encode())
    _Handler.replies["/api"] = (200, _MEDIAINFO_TEXT.encode())
    # The same release, listed first by an indexer with NFOs only.
    rows = _rows(PLAIN, PLAIN)
    rows[0]["nfo_url"] = _nfo_link(other, _OTHER_ID)
    rows[1]["nfo_url"] = _nfo_link(indexer)
    with _known(localhost=Offer(GETNFO, None), **{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert _Handler.hosts == ["127.0.0.1"]


def test_a_report_with_video_and_no_known_audio_keeps_the_video(indexer, store):
    silent = (
        '<track type="Audio"><Format>Musepack SV8</Format>'
        "<Channels>2</Channels></track>"
    )
    _Handler.replies[_MEDIAINFO_PATH] = (
        200,
        _reply(_report(_DV_VIDEO, silent)).encode(),
    )
    rows = _rows(PLAIN)
    rows[0]["media_ref"] = "{}/api?id={}&apikey=SECRET".format(indexer, _ID)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
        # Settled: not asked again.
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    assert _Handler.requests == [_MEDIAINFO_PATH]
    entry = json.loads(store.read_text())["releases"][_key(PLAIN)]
    assert entry["audio"] == [] and entry["failed"] is False
    assert entry["video"]["dv_profile"] == "8"
    filter_module.attach_metadata(rows)
    assert rows[0]["_meta"]["hdr"] == ["Dolby Vision", "HDR10"]


def test_a_reference_that_gets_no_answer_is_asked_again_another_day(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (500, b"")
    rows = _rows(PLAIN)
    rows[0]["media_ref"] = "{}/api?id={}&apikey=SECRET".format(indexer, _ID)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    entry = json.loads(store.read_text())["releases"][_key(PLAIN)]
    assert entry["failed"] is True and "video" not in entry


def test_an_unknown_direct_indexer_is_probed_once(indexer, store):
    rows = _rows(PLAIN, OTHER)
    rows[0]["media_ref"] = "{}/api?id={}&apikey=SECRET".format(indexer, _ID)
    rows[1]["media_ref"] = "{}/api?id={}&apikey=SECRET".format(indexer, _OTHER_ID)
    # Not in the table, and it has no such call: a plain 404.
    assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    assert _Handler.requests == [_MEDIAINFO_PATH]
    assert json.loads(store.read_text())["rest"]["127.0.0.1"]["ok"] is False
    # Found to have none: its results are not asked about again.
    nfo_audio.forget_all()
    assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    assert len(_Handler.requests) == 1
    # Nothing was learned about the releases, so nothing is settled: another
    # indexer's listing of one, with an NFO link, is still read.
    assert not json.loads(store.read_text())["releases"]
    _Handler.replies["/api"] = (200, _MEDIAINFO_TEXT.encode())
    rows[0]["nfo_url"] = _nfo_link(indexer.replace("127.0.0.1", "localhost"))
    with _known(localhost=Offer(GETNFO, None)):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
    assert nfo_audio._known(_key(PLAIN))["audio"] == ["DD+"]


def test_a_release_with_no_mediainfo_and_no_nfo_link_is_settled(indexer, store):
    _Handler.replies[_MEDIAINFO_PATH] = (404, _NOT_FOUND)
    rows = _rows(PLAIN)
    rows[0]["media_ref"] = "{}/api?id={}&apikey=SECRET".format(indexer, _ID)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    # Asked once: the indexer answered that this release has none.
    assert _Handler.requests == [_MEDIAINFO_PATH]
    entry = json.loads(store.read_text())["releases"][_key(PLAIN)]
    assert entry["failed"] is False and entry["audio"] == []


def test_a_reference_whose_key_the_call_cannot_use_asks_nothing(indexer):
    rows = _rows(PLAIN)
    # The key under a name the MediaInfo call does not take.
    rows[0]["media_ref"] = "{}/api?id={}&r=SECRET".format(indexer, _ID)
    with _known(**{"127_0_0_1": Offer(GETNFO, REST_V1)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is False
    assert not _Handler.requests
    assert nfo_audio._known(_key(PLAIN)) is None


def test_the_host_nzbhydra2_names_is_the_one_compared():
    link = "https://evil.example/api?t=getnfo&amp;id={}&amp;apikey=SECRET".format(_ID)
    # An indexer sends its own hydraIndexerHost; NZBHydra2 adds the real one
    # after it.
    item = _item(
        (
            ("hydraIndexerHost", "evil.example"),
            ("nfo", "1"),
            ("info", link),
            ("hydraIndexerHost", "indexer.example"),
        )
    )
    assert newznab_attrs.nfo_link(item) == ""


def test_html_line_breaks_in_an_nfo_are_not_read_as_text(indexer):
    nfo = (
        "Title           : Some Movie 2004 2160p WEB-DL H 265-GRP<br />\n"
        "<br />\n"
        "Audio:<br />\n"
        "    Codec       : E-AC-3 JOC<br />\n"
        "    Bitrate     : 576 kb/s<br />\n"
    )
    _Handler.replies["/api"] = (200, nfo.encode())
    rows = _rows(PLAIN)
    rows[0]["nfo_url"] = "{}/api?t=info&id={}&apikey=SECRET".format(indexer, _ID)
    with _known(**{"127_0_0_1": Offer(INFO, None)}):
        assert nfo_audio.learn_for_top(rows, _key, _ttl) is True
    entry = nfo_audio._known(_key(PLAIN))
    assert sorted(entry["audio"]) == ["Atmos", "DD+"]
