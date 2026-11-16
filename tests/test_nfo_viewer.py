# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""The picker's NFO and MediaInfo viewer: the text drawn with the add-on's
font, the page count for the screen, the tabs, the keys, and the picker's
NFO and MEDIAINFO tags and its Left and Right keys."""

import os
import struct
import zlib
from unittest.mock import MagicMock, patch

import pytest
from resources.lib import doc_fetch, nfo_audio, nfo_viewer, release_docs, text_image
from resources.lib.filter import parse_title_metadata
from resources.lib.release_subject import name_form_key
from resources.lib.results_dialog import (
    ACTION_MOVE_LEFT,
    ACTION_MOVE_RIGHT,
    COMPACT_LIST_ID,
    LIST_ID,
    VIEW_COMPACT,
    ResultsDialog,
    _build_group_item,
    _build_result_item,
)
from resources.lib.xml_safety import safe_fromstring

NAME = "Metropolis.1927.2160p.WEB-DL.H.265-GRP"
KEY = name_form_key(NAME)
OWN_NFO = NAME + "\n\n  Audio....: DD+ 5.1\n"
REPORT = (
    '<MediaInfo><media ref="/up/Metropolis.mkv">'
    '<track type="General"><Format>Matroska</Format></track>'
    '<track type="Video"><Format>HEVC</Format></track></media></MediaInfo>'
)
_SKINS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "skins",
    "Default",
    "1080i",
)


def _font():
    """A stand-in font: every glyph is its code point, repeated on 16 rows."""
    glyphs = {code: bytes([code & 0xFF]) * 16 for code in range(0x20, 0x7F)}
    glyphs[0x20] = bytes(16)
    glyphs[0x2713] = bytes([0x81]) * 16
    return glyphs


def _decode(png):
    """``(width, height, depth, palette, rows)`` of an indexed PNG."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    chunks = {}
    at = 8
    while at < len(png):
        (length,) = struct.unpack(">I", png[at : at + 4])
        kind = png[at + 4 : at + 8]
        body = png[at + 8 : at + 8 + length]
        (crc,) = struct.unpack(">I", png[at + 8 + length : at + 12 + length])
        assert crc == zlib.crc32(kind + body)
        chunks[kind] = body
        at += 12 + length
    width, height, depth, color, _c, _f, _i = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    assert color == 3 and chunks[b"tRNS"] == b"\x00"
    raw = zlib.decompress(chunks[b"IDAT"])
    stride = (width * depth + 7) // 8 + 1
    rows = [raw[i + 1 : i + stride] for i in range(0, len(raw), stride)]
    assert len(rows) == height and all(raw[i] == 0 for i in range(0, len(raw), stride))
    palette = chunks[b"PLTE"]
    return (
        width,
        height,
        depth,
        [tuple(palette[i : i + 3]) for i in range(0, len(palette), 3)],
        rows,
    )


@pytest.fixture(name="store", autouse=True)
def _store(tmp_path):
    release_docs.forget_all()
    nfo_audio.forget_all()
    with patch.object(
        release_docs, "_dir", return_value=str(tmp_path / "release_docs")
    ), patch.object(
        nfo_audio, "_store_path", return_value=str(tmp_path / "nfo_audio.json")
    ):
        yield tmp_path
    release_docs.forget_all()
    nfo_audio.forget_all()


# --- text drawn to an image -------------------------------------------------


def test_a_font_file_is_read_back_as_it_was_packed(tmp_path):
    path = tmp_path / "font.bin"
    path.write_bytes(text_image.pack_font(_font()))
    assert text_image.load_font(str(path)) == _font()


def test_a_missing_or_damaged_font_is_no_font(tmp_path):
    assert text_image.load_font(str(tmp_path / "none.bin")) is None
    packed = text_image.pack_font(_font())
    without_space = text_image.pack_font({0x41: bytes(16)})
    for index, data in enumerate(
        (b"", b"NZF1", b"XXXX" + packed[4:], packed[:-3], without_space)
    ):
        path = tmp_path / "font{}.bin".format(index)
        path.write_bytes(data)
        assert text_image.load_font(str(path)) is None
    with pytest.raises(ValueError):
        text_image.pack_font({0x20: bytes(15)})


def test_text_is_cut_to_the_columns_and_never_reflowed():
    text = "ab\tc\r\n" + "x" * 7 + "\n\n\x07bell\x7f  \n\n\n"
    # The tab runs to the eighth column; a long line is cut, not re-flowed.
    assert text_image.wrap(text, 5) == ["ab   ", "   c", "xxxxx", "xx", "", "bell"]
    assert not text_image.wrap(None, 5)
    assert text_image.paginate(["1", "2", "3"], 2) == [["1", "2"], ["3"]]
    assert text_image.paginate([], 2) == [[]]


def test_a_page_is_drawn_glyph_by_glyph_at_the_scale_asked():
    font = _font()
    width, height, depth, palette, rows = _decode(
        text_image.page_png(["A", ""], font, 2, 3, "FF112233", scale=1)
    )
    assert (width, height, depth) == (16, 48, 1)
    assert palette == [(0, 0, 0), (0x11, 0x22, 0x33)]
    # "A" is 0x41 on every row; the cell beside it and the other lines are empty.
    assert rows[:16] == [b"\x41\x00"] * 16
    assert rows[16:] == [b"\x00\x00"] * 32

    width, height, _depth, _palette, rows = _decode(
        text_image.page_png(["A"], font, 1, 1, scale=3)
    )
    assert (width, height) == (24, 48)
    # 0100 0001, every pixel three wide and three tall.
    assert rows == [b"\x1c\x00\x07"] * 48


def test_a_character_the_font_lacks_is_drawn_as_a_question_mark():
    _w, _h, _d, _p, rows = _decode(text_image.page_png(["☃"], _font(), 1, 1, scale=1))
    assert rows[0] == bytes([ord("?")])


def test_words_are_drawn_in_their_colors_with_small_ones_on_the_baseline():
    font = _font()
    words = [("A", "FF0000", False), ("B", "00FF00", True), ("A", "FF0000", False)]
    width, height, depth, palette, rows = _decode(
        text_image.words_png(words, font, 200, scale=2)
    )
    assert (width, height, depth) == (200, 32, 8)
    assert palette == [(0, 0, 0), (255, 0, 0), (0, 255, 0)]
    # "A" at double size: 16 pixels wide from the left edge, on every row.
    assert rows[0][:16] == bytes([0, 0, 1, 1] + [0] * 10 + [1, 1])
    # The small word: one character cell on, half size, on the same baseline.
    assert set(rows[0][16:40]) == {0}
    assert rows[12][24:32] == bytes([0, 2, 0, 0, 0, 0, 2, 0])
    assert set(rows[28][24:32]) == {0}
    # The next word is three double cells on from the small one's end.
    assert rows[0][80:96] == rows[0][:16]


def test_words_that_do_not_fit_are_left_out():
    _w, _h, _d, palette, _rows = _decode(
        text_image.words_png(
            [("AAAA", "FF0000", False), ("BBBB", "00FF00", False)], _font(), 70
        )
    )
    assert palette == [(0, 0, 0), (255, 0, 0)]
    assert text_image.check_mark(_font()) == "✓"
    assert text_image.check_mark({0x20: bytes(16)}) == "v"


# --- the layout for the screen ----------------------------------------------


def test_the_page_follows_the_screen():
    assert nfo_viewer.geometry(1920, 1080) == {
        "scale": 2,
        "columns": 80,
        "rows": 23,
        "pixels": 1.0,
    }
    # Twice the pixels: the same page, drawn twice as fine.
    four_k = nfo_viewer.geometry(3840, 2160)
    assert (four_k["scale"], four_k["columns"], four_k["rows"]) == (4, 80, 23)
    # In between and below: the largest text that still shows 80 columns.
    assert nfo_viewer.geometry(2560, 1440)["rows"] == 30
    small = nfo_viewer.geometry(1280, 720)
    assert (small["scale"], small["columns"], small["rows"]) == (1, 106, 30)
    for odd in ((None, None), (0, 0), ("wide", "tall")):
        assert nfo_viewer.geometry(*odd)["rows"] == 23


def test_a_tab_is_as_tall_as_its_list_of_indexers():
    one, seven = nfo_viewer.rail_layout([1, 7], 0)
    assert one == {"top": 0, "height": 99, "line": 33, "shown": True}
    assert seven == {"top": 114, "height": 297, "line": 33, "shown": True}
    assert not nfo_viewer.rail_layout([], 0)


def test_a_long_list_is_set_closer_and_never_cut():
    # Twenty indexers on one tab and two more tabs: the lines move closer.
    tabs = nfo_viewer.rail_layout([20, 1, 1], 0)
    assert tabs[0]["line"] < 33 and all(tab["shown"] for tab in tabs)
    assert tabs[-1]["top"] + tabs[-1]["height"] <= 930

    # More than fits at any spacing: the selected tab is whole, and a tab the
    # edge would cut is left out rather than cut.
    tabs = nfo_viewer.rail_layout([30, 10, 2], 1)
    assert tabs[1]["shown"] and tabs[1]["top"] >= 0
    assert tabs[1]["top"] + tabs[1]["height"] <= 930
    assert tabs[0]["shown"] is False and tabs[0]["line"] == 24


# --- the window ---------------------------------------------------------------


def _rows():
    return [
        {"title": NAME, "indexer": "Hub", "nfo_url": "https://hub.example/api?id=1"},
        {"title": NAME, "indexer": "Slug", "nfo_url": "https://slug.example/api?id=1"},
        {
            "title": NAME,
            "indexer": "Maps",
            "media_ref": "https://maps.example/api?id=1",
        },
    ]


def _keep_three():
    release_docs.keep(KEY, release_docs.NFO, "hub.example", OWN_NFO)
    release_docs.keep(KEY, release_docs.NFO, "slug.example", OWN_NFO)
    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", REPORT)


def _viewer(tmp_path, font=True, view=None):
    return nfo_viewer.NfoViewer(
        "nfo-viewer.xml",
        "",
        "Default",
        "1080i",
        rows=_rows(),
        key=KEY,
        title=NAME,
        font=_font() if font else None,
        geometry=view or nfo_viewer.geometry(1920, 1080),
        white="white.png",
        cache=str(tmp_path / "pages"),
    )


class _Action:  # pylint: disable=too-few-public-methods
    def __init__(self, action_id):
        self._id = action_id

    def getId(self):
        return self._id


def _press(viewer, action_id):
    viewer.onAction(_Action(action_id))


def _labels(viewer):
    """The text of every label the viewer added for its tabs."""
    return [
        call.args[4]
        for call in nfo_viewer.xbmcgui.ControlLabel.call_args_list[-len(viewer._rail) :]
        if len(call.args) > 4
    ]


def test_identical_nfos_are_one_tab_listing_their_indexers(tmp_path):
    _keep_three()
    viewer = _viewer(tmp_path)
    nfo_viewer.xbmcgui.ControlLabel.reset_mock()
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()

    assert [(tab["kind"], tab["indexers"]) for tab in viewer._tabs] == [
        ("nfo", ["Hub", "Slug"]),
        ("mediainfo", ["Maps"]),
    ]
    drawn = [call.args[4] for call in nfo_viewer.xbmcgui.ControlLabel.call_args_list]
    assert drawn == ["[B]NFO[/B]", "Hub", "Slug", "[B]MEDIAINFO[/B]", "Maps"]
    assert viewer._added == viewer._rail and viewer._rail
    # The header is the release's name and the page; no status when there is text.
    assert viewer.getProperty("name") == NAME
    assert viewer.getProperty("page") == "Page 1 / 1"
    assert viewer.getProperty("mode") == "image"
    assert viewer.getProperty("status") == ""
    assert viewer.getProperty("hints") == "[Left/Right] Other tab"
    assert viewer.getProperty("close_hint") == "[Back] Close"


def test_the_page_and_the_attributes_are_drawn_as_images(tmp_path):
    _keep_three()
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()

    image = viewer.getControl(20)
    (path, use_cache), _kwargs = image.setImage.call_args
    assert use_cache is False and os.path.dirname(path) == str(tmp_path / "pages")
    with open(path, "rb") as handle:
        width, height, depth, _palette, _rows_ = _decode(handle.read())
    assert (width, height, depth) == (1280, 736, 1)
    image.setWidth.assert_called_with(1280)
    image.setHeight.assert_called_with(736)

    strip = viewer.getControl(30)
    with open(strip.setImage.call_args.args[0], "rb") as handle:
        width, height, _depth, palette, _rows_ = _decode(handle.read())
    assert (width, height) == (1275, 32)
    # Resolution purple, the check grey, audio pink with its NEW in the same
    # pink: no color is there for NEW alone.
    assert (0xA7, 0x8B, 0xFA) in palette and (0xE8, 0x79, 0xA8) in palette
    assert (0x6B, 0x72, 0x80) in palette and (0xFF, 0xFF, 0xFF) not in palette


def test_what_is_drawn_is_as_large_as_the_screen_asks(tmp_path):
    _keep_three()
    viewer = _viewer(tmp_path, view=nfo_viewer.geometry(3840, 2160))
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()
    image = viewer.getControl(20)
    with open(image.setImage.call_args.args[0], "rb") as handle:
        width, height, _depth, _palette, _rows_ = _decode(handle.read())
    # Four screen pixels per font pixel, shown in the same place and size.
    assert (width, height) == (2560, 1472)
    image.setWidth.assert_called_with(1280)
    image.setHeight.assert_called_with(736)


def test_left_and_right_change_the_tab_and_wrap(tmp_path):
    _keep_three()
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()
    first = viewer.getControl(20).setImage.call_args.args[0]

    _press(viewer, nfo_viewer.ACTION_MOVE_RIGHT)
    assert viewer._tab == 1
    # A report's header is the file it is about, without the uploader's folder.
    assert viewer.getProperty("name") == "Metropolis.mkv"
    assert viewer.getControl(20).setImage.call_args.args[0] != first
    _press(viewer, nfo_viewer.ACTION_MOVE_RIGHT)
    assert viewer._tab == 0 and viewer.getProperty("name") == NAME
    _press(viewer, nfo_viewer.ACTION_MOVE_LEFT)
    assert viewer._tab == 1
    # The tabs were drawn afresh each time, and the old controls removed.
    assert viewer._added == viewer._rail


def test_up_and_down_turn_the_page_and_stop_at_the_ends(tmp_path):
    long_nfo = NAME + "\n" + "\n".join("line {}".format(n) for n in range(50))
    release_docs.keep(KEY, release_docs.NFO, "hub.example", long_nfo)
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()

    # 51 lines at 23 to the page: Down twice shows it all.
    assert viewer.getProperty("page") == "Page 1 / 3"
    assert viewer.getProperty("hints") == "[Up/Down] Page"
    _press(viewer, nfo_viewer.ACTION_MOVE_UP)
    assert viewer.getProperty("page") == "Page 1 / 3"
    _press(viewer, nfo_viewer.ACTION_MOVE_DOWN)
    _press(viewer, nfo_viewer.ACTION_PAGE_DOWN)
    assert viewer.getProperty("page") == "Page 3 / 3"
    _press(viewer, nfo_viewer.ACTION_MOUSE_WHEEL_DOWN)
    assert viewer.getProperty("page") == "Page 3 / 3"
    _press(viewer, nfo_viewer.ACTION_PAGE_UP)
    assert viewer.getProperty("page") == "Page 2 / 3"
    assert len(os.listdir(str(tmp_path / "pages"))) == 4  # three pages, one strip


def test_back_closes_and_other_keys_do_nothing(tmp_path):
    _keep_three()
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()
    _press(viewer, 7)
    assert viewer._closed is False
    _press(viewer, nfo_viewer.ACTION_NAV_BACK)
    assert viewer._closed is True


def _answering(viewer):
    """Patches under which the viewer's two indexers answer when ``_ask`` runs."""

    def answer(rows, key, title, describe):
        assert (rows, key, title) == (viewer._rows, KEY, NAME) and describe
        release_docs.keep(key, release_docs.NFO, "hub.example", OWN_NFO)

    return (
        patch.object(doc_fetch, "plan", return_value={"a": {}, "b": {}}),
        patch.object(nfo_viewer.threading, "Thread"),
        patch.object(doc_fetch, "gather", side_effect=answer),
    )


def test_indexers_not_asked_yet_are_asked_in_the_background(tmp_path):
    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", REPORT)
    viewer = _viewer(tmp_path)
    plan, thread_class, gather = _answering(viewer)
    with plan, thread_class as thread, gather:
        viewer.onInit()
        # What is kept is shown at once, with a note that more is coming.
        assert [tab["kind"] for tab in viewer._tabs] == ["mediainfo"]
        assert viewer.getProperty("hints") == (
            "Asking 2 indexers for the NFO and MediaInfo..."
        )
        thread.assert_called_once_with(target=viewer._ask, daemon=True)
        viewer.onInit()  # Kodi may call it again: nothing is asked twice
        thread.assert_called_once()
        viewer._ask()

    # The NFO came: it is the first tab, and the one shown.
    assert [tab["kind"] for tab in viewer._tabs] == ["nfo", "mediainfo"]
    assert viewer._tab == 0 and viewer.getProperty("name") == NAME
    assert viewer.getProperty("hints") == "[Left/Right] Other tab"


def test_the_place_is_kept_when_more_texts_arrive_after_a_key(tmp_path):
    long_report = REPORT.replace(
        "</media>",
        "".join('<track type="Text"><Format>PGS</Format></track>' for _ in range(20))
        + "</media>",
    )
    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", long_report)
    viewer = _viewer(tmp_path)
    plan, thread_class, gather = _answering(viewer)
    with plan, thread_class, gather:
        viewer.onInit()
        _press(viewer, nfo_viewer.ACTION_MOVE_DOWN)
        assert viewer.getProperty("page").startswith("Page 2 /")
        viewer._ask()

    assert [tab["kind"] for tab in viewer._tabs] == ["nfo", "mediainfo"]
    assert viewer._tab == 1 and viewer.getProperty("page").startswith("Page 2 /")


def test_the_window_says_so_while_it_waits_and_when_nothing_came(tmp_path):
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={"a": {}}), patch.object(
        nfo_viewer.threading, "Thread"
    ), patch.object(doc_fetch, "gather", side_effect=RuntimeError("down")):
        viewer.onInit()
        assert viewer.getProperty("status") == (
            "Asking 1 indexer for the NFO and MediaInfo..."
        )
        assert viewer.getProperty("mode") == "" and viewer.getProperty("page") == ""
        viewer._ask()
    assert viewer.getProperty("status") == (
        "No indexer has an NFO or MediaInfo for this release."
    )
    # Nothing to turn or switch: the keys are harmless.
    _press(viewer, nfo_viewer.ACTION_MOVE_DOWN)
    _press(viewer, nfo_viewer.ACTION_MOVE_RIGHT)


def test_an_answer_that_comes_after_the_window_closed_is_not_drawn(tmp_path):
    viewer = _viewer(tmp_path)
    with patch.object(doc_fetch, "plan", return_value={"a": {}}), patch.object(
        nfo_viewer.threading, "Thread"
    ), patch.object(doc_fetch, "gather"):
        viewer.onInit()
        viewer.close()
        _keep_three()
        viewer._ask()
    assert not viewer._tabs


def test_without_the_font_the_skins_text_box_shows_the_page(tmp_path):
    art = "\u2554\u2550\u2557 \u2502 \u2500\u2500 \u2591\u2592\u2593\u2588\n"
    release_docs.keep(
        KEY, release_docs.NFO, "hub.example", OWN_NFO + "[B]bold[/b] [x]\n" + art
    )
    viewer = _viewer(tmp_path, font=False)
    with patch.object(doc_fetch, "plan", return_value={}):
        viewer.onInit()

    assert viewer.getProperty("mode") == "text"
    # Nothing in an NFO is read as Kodi's own markup, and its art is drawn
    # with characters any skin font has.
    assert viewer.getProperty("text") == (
        NAME + "[CR][CR]  Audio....: DD+ 5.1[CR][ B]bold[ /b] [x][CR]+=+ | -- .:##"
    )
    assert viewer.getProperty("attributes") == (
        "[COLOR FFA78BFA]2160p[/COLOR]     [COLOR FF94A3B8]H265[/COLOR]     "
        "[COLOR FFE879A8]DD+ 5.1[/COLOR] [COLOR FFE879A8]new[/COLOR]     "
        "[COLOR FFC084FC]WEB-DL[/COLOR]     [COLOR FF34D399]GRP[/COLOR]"
    )
    viewer.getControl(20).setImage.assert_not_called()


def test_every_listing_under_the_same_name_is_one_release():
    lead, other, pack, different = (
        {"title": NAME, "indexer": "Hub"},
        {"title": NAME.replace(".", " "), "indexer": "Slug"},
        {"title": NAME, "_season_pack": True},
        {"title": "Another.Film.2004.1080p.BluRay.x264-GRP"},
    )
    active = [different, lead, other, pack, None]
    assert nfo_viewer.release_rows([lead], active) == [lead, other]
    assert nfo_viewer.release_rows([], active) == []
    rows = _rows() + [{"title": NAME, "indexer": 7, "nfo_url": "https://x.example/a"}]
    assert nfo_viewer.indexer_names(rows) == {
        "hub.example": ["Hub"],
        "slug.example": ["Slug"],
        "maps.example": ["Maps"],
    }


def test_show_opens_the_window_only_when_there_is_something(tmp_path):
    dialog = MagicMock()
    with patch.object(nfo_viewer.xbmcgui, "Dialog", return_value=dialog), patch.object(
        nfo_viewer, "NfoViewer"
    ) as window:
        assert nfo_viewer.show([{"title": NAME, "indexer": "Bare"}], []) is False
        assert nfo_viewer.show([], []) is False
        assert nfo_viewer.show([{"title": None}], []) is False
    window.assert_not_called()
    assert dialog.notification.call_count == 3
    assert dialog.notification.call_args.args[1] == (
        "No indexer has an NFO or MediaInfo for this release."
    )

    _keep_three()
    stale = tmp_path / "pages"
    stale.mkdir()
    (stale / "old.png").write_bytes(b"x")
    with patch.object(nfo_viewer, "NfoViewer") as window, patch.object(
        nfo_viewer.xbmcvfs, "translatePath", return_value=str(stale)
    ), patch.object(nfo_viewer.text_image, "load_font", return_value=None):
        assert nfo_viewer.show([_rows()[0]], _rows()) is True
    kwargs = window.call_args.kwargs
    assert kwargs["key"] == KEY and kwargs["title"] == NAME
    assert kwargs["geometry"] == nfo_viewer.geometry(1920, 1080)
    window.return_value.doModal.assert_called_once_with()
    # The page images of the viewing before are gone.
    assert not list(stale.iterdir())


# --- the picker -----------------------------------------------------------------


def _row(title=NAME, **extra):
    row = {"title": title, "link": "http://x/1", "size": "5", "indexer": "Hub"}
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _props(item):
    return {call.args[0]: call.args[1] for call in item.setProperty.call_args_list}


_NFO_TAG = "[COLOR FFFDE047]NFO[/COLOR]"
_MEDIAINFO_TAG = "[COLOR FFA3E635]MEDIAINFO[/COLOR]"


def test_a_row_is_tagged_for_what_the_viewer_can_show():
    plain = _props(_build_result_item(_row(), 0))
    linked = _props(_build_result_item(_row(nfo_url="https://hub.example/api?id=1"), 0))
    assert plain["tags"] == ""
    assert linked["tags"] == _NFO_TAG

    release_docs.keep(KEY, release_docs.MEDIAINFO, "maps.example", REPORT)
    assert _props(_build_result_item(_row(), 0))["tags"] == _MEDIAINFO_TAG
    both = _props(_build_result_item(_row(nfo_url="https://hub.example/api?id=1"), 0))
    assert both["tags"] == _NFO_TAG + "  " + _MEDIAINFO_TAG
    assert _props(_build_result_item({"title": None, "_meta": {}}, 0))["tags"] == ""


def test_the_tags_of_a_row_share_one_label():
    name = "Metropolis.1927.REPACK.2160p.WEB-DL.H.265-GRP"
    group = [
        _row(name),
        _row(name, indexer="Slug", nfo_url="https://slug.example/a?id=1"),
    ]
    repack = "[COLOR FFFB923C]REPACK[/COLOR]"
    assert _props(_build_group_item(group, 0))["tags"] == repack + "  " + _NFO_TAG
    assert _props(_build_group_item([_row()], 0))["tags"] == ""
    # The plain list counts each listing's own link.
    assert _props(_build_result_item(group[0], 0))["tags"] == repack
    assert _props(_build_result_item(group[1], 0))["tags"] == repack + "  " + _NFO_TAG


def _picker(rows, view=None):
    dialog = ResultsDialog(
        "results-dialog.xml", "", "Default", "1080i", results=rows, view=view
    )
    dialog.onInit()
    list_id = COMPACT_LIST_ID if view == VIEW_COMPACT else LIST_ID
    dialog.getControl(list_id).getSelectedPosition.return_value = 0
    return dialog, dialog.getControl(list_id)


@pytest.mark.parametrize("key", [ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT])
def test_left_or_right_opens_the_viewer_on_the_focused_release(key):
    rows = [_row(), _row("Another.Film.2004.1080p.BluRay.x264-GRP")]
    dialog, control = _picker(rows)
    assert dialog.getProperty("footer_hints").startswith(
        "[Left/Right] NFO and MediaInfo"
    )

    def viewed(entry, active):
        assert entry == [rows[0]] and active == rows
        assert dialog._viewing is True
        dialog.onAction(_Action(key))  # a key still held does not open a second
        release_docs.keep(KEY, release_docs.NFO, "hub.example", OWN_NFO)

    with patch.object(nfo_viewer, "show", side_effect=viewed) as show:
        dialog.onAction(_Action(key))
    show.assert_called_once()
    # The row is tagged for what was read, and the picker is still open.
    control.getSelectedItem.return_value.setProperty.assert_called_with(
        "tags", _NFO_TAG
    )
    assert dialog._viewing is False and dialog._closed is False
    assert dialog.get_selected_result() is None


def test_the_compacted_row_keeps_its_other_tags_when_the_viewer_closes():
    name = "Metropolis.1927.REPACK.2160p.WEB-DL.H.265-GRP"
    dialog, control = _picker([_row(name)], view=VIEW_COMPACT)

    def viewed(_entry, _active):
        release_docs.keep(
            name_form_key(name), release_docs.MEDIAINFO, "m.example", REPORT
        )

    with patch.object(nfo_viewer, "show", side_effect=viewed):
        dialog.onAction(_Action(ACTION_MOVE_RIGHT))
    control.getSelectedItem.return_value.setProperty.assert_called_with(
        "tags", "[COLOR FFFB923C]REPACK[/COLOR]  " + _MEDIAINFO_TAG
    )


def test_the_list_is_asked_to_take_the_focus_until_it_has_it():
    # Kodi shows the active view's list a frame after the view is set; until
    # then the list refuses the focus, and no key would reach it.
    dialog = ResultsDialog(
        "results-dialog.xml", "", "Default", "1080i", results=[_row()]
    )
    answers = iter([RuntimeError("no control has focus"), 0, LIST_ID])

    def focused():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    with patch.object(dialog, "getFocusId", side_effect=focused), patch.object(
        dialog, "setFocusId"
    ) as ask, patch("resources.lib.results_dialog.xbmc.sleep") as wait:
        dialog.onInit()
    assert ask.call_count == 3 and wait.call_count == 2


def test_the_keys_do_nothing_off_the_list_or_once_the_picker_closes():
    dialog, _control = _picker([_row()])
    with patch.object(nfo_viewer, "show") as show:
        dialog.setFocusId(60)
        dialog.onAction(_Action(ACTION_MOVE_LEFT))
        dialog.setFocusId(LIST_ID)
        dialog.close()
        dialog.onAction(_Action(ACTION_MOVE_LEFT))
    show.assert_not_called()


# --- the skin files -------------------------------------------------------------


def _controls(name):
    with open(os.path.join(_SKINS, name), encoding="utf-8") as handle:
        return list(safe_fromstring(handle.read()).iter("control"))


def test_the_viewers_window_has_what_the_code_sets():
    controls = {control.get("id"): control for control in _controls("nfo-viewer.xml")}
    for control_id, visible in (
        ("20", "image"),
        ("30", "image"),
        ("21", "text"),
        ("31", "text"),
    ):
        assert controls[control_id].findtext("visible") == (
            "String.IsEqual(Window.Property(mode),{})".format(visible)
        )
    assert (
        controls["20"].get("type") == "image"
        and controls["21"].get("type") == "textbox"
    )
    labels = {control.findtext("label") for control in controls.values()}
    for name in ("name", "page", "status", "hints", "close_hint", "text", "attributes"):
        assert "$INFO[Window.Property({})]".format(name) in labels
    # The text's place in the window is the one the code draws for.
    place = [controls["20"].findtext(tag) for tag in ("left", "top", "width", "height")]
    assert place == ["528", "135", "1280", "736"]


def test_the_pickers_lists_keep_the_focus_on_left_and_right():
    lists = {
        control.get("id"): control
        for control in _controls("results-dialog.xml")
        if control.get("type") == "list"
    }
    for list_id in ("50", "51"):
        assert lists[list_id].findtext("onleft") == list_id
        assert lists[list_id].findtext("onright") == list_id
    labels = [
        control.findtext("label") or "" for control in _controls("results-dialog.xml")
    ]
    assert (
        sum(label.startswith("$INFO[ListItem.Property(tags)]") for label in labels) == 4
    )
