# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalized picker names, the HYBRID attribute, and subject cleanup."""

import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest
from resources.lib.filter import parse_title_metadata
from resources.lib.filter_tags import detect_hybrid
from resources.lib.release_display import clean_release_name, display_name
from resources.lib.release_match import release_profile, same_release
from resources.lib.release_subject import is_usenet_subject, release_name_from_subject
from resources.lib.results_dialog import (
    _build_group_item,
    _build_result_item,
    _wrap_display_name,
)

_DIALOG_XML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "repo",
    "plugin.video.nzbdav",
    "resources",
    "skins",
    "Default",
    "1080i",
    "results-dialog.xml",
)

_NAME = "Movie.2023.1080p.WEB-DL.DDP5.1.H.264-GRP"


def _row(title, **extra):
    row = {
        "title": title,
        "link": title,
        "size": "5000000000",
        "age": "3 days",
        "indexer": "idx",
    }
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


@pytest.mark.parametrize(
    "title,shown",
    [
        (
            "Dune.Part.Two.2024.Hybrid.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC."
            "TrueHD.7.1.Atmos-CiNEPHiLES",
            "Dune Part Two (2024) [Remux-2160p][HYBRID][DV HDR10+]"
            "[TrueHD Atmos 7.1][HEVC]-CiNEPHiLES",
        ),
        (
            "Six.Feet.Under.2001.S02E01.1080p.AMZN.WEB-DL.H265.SDR.DDP.5.1."
            "English-HONE.REMASTERED",
            "Six Feet Under (2001) - S02E01 {Remastered} [WEB-DL-1080p][DD+ 5.1][H265]"
            "-HONE",
        ),
        (
            "Six.Feet.Under.S02E01.In.The.Game.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX",
            "Six Feet Under - S02E01 [WEB-DL-1080p][DD+ 5.1][H264]-FLUX",
        ),
        (
            "Show.S01E01E02.720p.HDTV.x264-GRP",
            "Show - S01E01-E02 [HDTV-720p][x264]-GRP",
        ),
        (
            "Show.S03.1080p.BluRay.x265.AAC.5.1-GRP",
            "Show - S03 [Bluray-1080p][AAC 5.1][x265]-GRP",
        ),
        (
            "Movie.2023.REPACK.2160p.NF.WEB-DL.DDPA5.1.DV.HDR.H.265-GRP",
            "Movie (2023) [WEB-DL-2160p REPACK][DV HDR10][DD+ Atmos 5.1][H265]-GRP",
        ),
        (
            "Movie.1999.1080p.BluRay.FLAC.2.0.x264-GRP",
            "Movie (1999) [Bluray-1080p][FLAC 2.0][x264]-GRP",
        ),
        (
            "Movie.2023.MULTi.1080p.WEB.H264-GRP",
            "Movie (2023) [WEB-DL-1080p][MULTi][H264]-GRP",
        ),
        (
            "Movie.2001.DVDRip.XviD-GRP",
            "Movie (2001) [DVD][MPEG-4 ASP]-GRP",
        ),
        (
            "Movie 2023 2160p UHD BluRay REMUX DV HDR HEVC TrueHD 7.1 Atmos-FraMeSToR",
            "Movie (2023) [Remux-2160p][DV HDR10][TrueHD Atmos 7.1][HEVC]-FraMeSToR",
        ),
        (
            "Movie.2023.1080p.WEB-DL.DDP5.1.H.264",
            "Movie (2023) [WEB-DL-1080p][DD+ 5.1][H264]",
        ),
    ],
)
def test_display_name(title, shown):
    assert display_name(_row(title)) == shown


@pytest.mark.parametrize(
    "subject",
    [
        '[1/3] - "{}.mkv" yEnc  4400443301',
        '[01/17] - "{}.part01.rar" yEnc (1/137) 96234123',
        '{0} [1/3] - "{0}.mkv" yEnc 4400443301',
        '(1/3) "{}.mkv" - 4,10 GB - yEnc',
        "[1/3] - {}.mkv yEnc 4400443301",
        "{} yEnc (1/3) 4400443301",
        "{} - [1/3] - yEnc 4400443301",
        '[ {} ] - [01/52] - "abc.part01.rar" yEnc',
        '[PRiVATE]-[WtFnZb]-[{}.mkv]-[1/2] - "" yEnc  4400443301 (1/6140)',
        '[N3wZ] \\6aZWVk131318\\::[PRiVATE]-[WtFnZb]-[{}.mkv]-[1/2] - "" yEnc '
        " 4400443301 (1/6140)",
        "{}.mkv",
        "{}",
    ],
)
def test_every_listing_of_a_release_is_shown_and_grouped_alike(subject):
    listing = _row(subject.format(_NAME))
    named = _row(_NAME)
    assert clean_release_name(listing["title"]) == _NAME
    assert display_name(listing) == "Movie (2023) [WEB-DL-1080p][DD+ 5.1][H264]-GRP"
    assert same_release(named, listing)
    assert release_profile(named) == release_profile(listing)


@pytest.mark.parametrize(
    "title",
    [
        "The Naked Gun 33 1/3",
        "9 1/2 Weeks 1986 1080p BluRay x264-GRP",
        "Movie.2023.1080p.WEB-DL.DDP5.1.H.264-GRP",
    ],
)
def test_a_fraction_in_a_title_is_not_a_file_counter(title):
    assert not is_usenet_subject(title)
    assert release_name_from_subject(title) == title


def test_a_chain_bracket_that_is_no_release_leaves_the_outer_name():
    subject = '{} [foo]-[1/5] - "abc.part01.rar" yEnc'.format(_NAME)
    assert release_name_from_subject(subject).startswith(_NAME)
    assert same_release(_row(_NAME), _row(subject))


@pytest.mark.parametrize(
    "title",
    [
        "a8f3c9e1b2d4f6a7",  # a hash: no title, no tags
        "Just Some Words",  # no source and no resolution
        "",
    ],
)
def test_a_name_with_nothing_to_normalize_is_shown_as_it_is(title):
    assert display_name(_row(title)) == title


def test_display_name_without_parsed_metadata_is_the_cleaned_name():
    row = {"title": '[1/3] - "{}.mkv" yEnc  4400443301'.format(_NAME)}
    assert display_name(row) == _NAME


@pytest.mark.parametrize(
    "title,hybrid",
    [
        ("Movie.2023.Hybrid.2160p.UHD.BluRay.REMUX.DV.HEVC-GRP", True),
        ("Movie.2023.2160p.HYBRID.WEB-DL.DV.HDR.DDP5.1.H.265-GRP", True),
        ("Movie.2023.2160p.UHD.BluRay.REMUX.DV.HEVC.HYBRID-GRP", True),
        ("Show.S01E01.hybrid.1080p.WEB-DL.H.264-GRP", True),
        ("Movie.2023.2160p.UHD.BluRay.REMUX.DV.HEVC-GRP", False),
        ("Hybrid.2007.1080p.BluRay.x264-GRP", False),  # the title
        ("The.Hybrid.Theory.2023.1080p.WEB-DL.H.264-GRP", False),
        ("Movie.2023.1080p.WEB-DL.H.264-HYBRiDS", False),  # a group
    ],
)
def test_hybrid_is_a_parsed_attribute(title, hybrid):
    assert detect_hybrid(title) is hybrid
    assert parse_title_metadata(title)["hybrid"] is hybrid


def test_a_hybrid_is_a_different_release_from_its_plain_twin():
    plain = "Movie.2023.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1-GRP"
    hybrid = "Movie.2023.Hybrid.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1-GRP"
    spaced = "Movie 2023 HYBRID 2160p UHD BluRay REMUX DV HEVC TrueHD 7.1-GRP"
    assert not same_release(_row(plain), _row(hybrid))
    assert same_release(_row(hybrid), _row(spaced))


def test_hybrid_without_the_stored_attribute_is_read_from_the_title():
    hybrid = "Movie.2023.Hybrid.2160p.UHD.BluRay.REMUX.DV.HEVC.TrueHD.7.1-GRP"
    fresh = _row(hybrid)
    stale = _row(hybrid)
    del stale["_meta"]["hybrid"]
    assert same_release(fresh, stale)
    assert same_release(stale, fresh)


def test_a_parenthesized_tag_block_ends_with_the_group():
    title = "Movie (2023) (2160p BluRay x265 HEVC 10bit HDR AAC 7.1 Tigole)"
    assert parse_title_metadata(title)["group"] == "Tigole"
    for no_group in (
        "Movie (2023) (2160p BluRay x265 HEVC 10bit HDR AAC 7.1)",
        "Movie (2023) (1080p BluRay x265 English)",
        "Movie (2023) (Directors Cut)",
    ):
        assert parse_title_metadata(no_group)["group"] == ""


def _props(item):
    return {call.args[0]: call.args[1] for call in item.setProperty.call_args_list}


def _strings(string_id):
    return {30400: "SDR", 30479: "{} copies", 30478: "{} copies on {} indexers"}.get(
        string_id, "s{}".format(string_id)
    )


def _fake_item(label=""):
    item = MagicMock()
    item.label = label
    return item


@patch("resources.lib.results_dialog._string", side_effect=_strings)
@patch("resources.lib.results_dialog.xbmcgui")
def test_a_row_shows_the_normalized_name_audio_label_and_hybrid(mock_gui, _mock_str):
    mock_gui.ListItem.side_effect = _fake_item
    title = (
        '[1/3] - "Movie.2023.Hybrid.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.'
        'TrueHD.7.1.Atmos-CiNEPHiLES.mkv" yEnc  4400443301'
    )
    item = _build_result_item(_row(title), 0)
    assert item.label == (
        "Movie (2023) [Remux-2160p][HYBRID][DV HDR10+][TrueHD Atmos 7.1][HEVC]"
        "-CiNEPHiLES"
    )
    props = _props(item)
    assert props["hybrid"] == "[COLOR FF22D3EE]HYBRID[/COLOR]"
    assert props["audio"] == "[COLOR FFE879A8]TrueHD Atmos 7.1[/COLOR]"
    assert props["hdr"] == "[COLOR FFFBBF24]DV HDR10+[/COLOR]"


@patch("resources.lib.results_dialog._string", side_effect=_strings)
@patch("resources.lib.results_dialog.xbmcgui")
def test_a_plain_row_leaves_the_hybrid_slot_empty(mock_gui, _mock_str):
    mock_gui.ListItem.side_effect = _fake_item
    props = _props(_build_result_item(_row(_NAME), 0))
    assert props["hybrid"] == ""
    assert props["hdr"] == "[COLOR FFFBBF24]SDR[/COLOR]"


@patch("resources.lib.results_dialog._string", side_effect=_strings)
@patch("resources.lib.results_dialog.xbmcgui")
def test_a_synthetic_row_keeps_its_own_label(mock_gui, _mock_str):
    mock_gui.ListItem.side_effect = _fake_item
    row = _row(_NAME, _display_title="Downloaded season pack - Episodes 1-3")
    assert _build_result_item(row, 0).label == "Downloaded season pack - Episodes 1-3"


@patch("resources.lib.results_dialog._fmt", side_effect=lambda i, *a: str(a))
@patch("resources.lib.results_dialog._string", side_effect=_strings)
@patch("resources.lib.results_dialog.xbmcgui")
def test_the_panel_shows_the_listed_name_under_the_normalized_one(
    mock_gui, _mock_str, _mock_fmt
):
    mock_gui.ListItem.side_effect = _fake_item
    subject = '[1/3] - "{}.mkv" yEnc  4400443301'.format(_NAME)
    props = _props(_build_group_item([_row(subject), _row(_NAME)], 0))
    assert props["panel_title"] == "Movie (2023) [WEB-DL-1080p][DD+ 5.1][H264]-GRP"
    assert props["panel_raw"] == "[COLOR FF6B7280]{}[/COLOR]".format(_NAME)
    assert "HYBRID" not in props["panel_tech"]


def test_wrap_display_name_breaks_a_dotted_name_at_its_dots():
    name = "Some.Very.Long.Release.Name.Without.Any.Spaces.2023.1080p.WEB-DL.x264"
    lines = _wrap_display_name(name, width=30).split("[CR]")
    assert "".join(lines) == name
    assert len(lines) > 1 and all(len(line) <= 30 for line in lines)


def test_wrap_display_name_never_breaks_inside_a_bracket_block():
    name = (
        "Dune Part Two (2024) [Remux-2160p][HYBRID][DV HDR10+]"
        "[TrueHD Atmos 7.1][HEVC]-CiNEPHiLES"
    )
    lines = _wrap_display_name(name, width=46).split("[CR]")
    assert "".join(lines).replace(" ", "") == name.replace(" ", "")
    assert all(len(line) <= 46 for line in lines)
    assert all(line.count("[") == line.count("]") for line in lines)


def _layouts(list_id):
    root = ET.parse(_DIALOG_XML_PATH).getroot()
    control = root.find(".//control[@type='list'][@id='{}']".format(list_id))
    return [control.find("itemlayout"), control.find("focusedlayout")]


def _second_line(layout):
    """(left, width, property) of each label on a row's second line."""
    line = []
    for control in layout.findall("control"):
        label = control.findtext("label") or ""
        if control.findtext("top") == "34" and "Property(" in label:
            name = label.split("Property(")[1].split(")")[0]
            line.append(
                (int(control.findtext("left")), int(control.findtext("width")), name)
            )
    return sorted(line)


@pytest.mark.parametrize("list_id", ["50", "51"])
def test_every_row_layout_has_a_hybrid_slot_that_overlaps_nothing(list_id):
    for layout in _layouts(list_id):
        line = _second_line(layout)
        names = [name for _left, _width, name in line]
        assert names[names.index("quality") + 1] == "hybrid"
        for (left, width, name), (next_left, _w, _n) in zip(line, line[1:]):
            assert left + width <= next_left, name


def test_the_panel_has_a_line_for_the_listed_name():
    root = ET.parse(_DIALOG_XML_PATH).getroot()
    labels = [control.findtext("label") or "" for control in root.iter("control")]
    assert "$INFO[Container(51).ListItem.Property(panel_raw)]" in labels
