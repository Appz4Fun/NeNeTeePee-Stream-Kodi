# SPDX-License-Identifier: GPL-3.0-or-later
"""Which listed releases their own REPACK or PROPER replaces."""

import copy

import pytest
from resources.lib.filter import parse_title_metadata
from resources.lib.release_supersede import revision_label, superseded_rows

PLAIN = "Alpha.2024.2160p.UHD.BluRay.REMUX.DV.HDR.TrueHD.7.1.Atmos.HEVC.HYBRID-GRPA"
REPACK = PLAIN.replace("-GRPA", ".REPACK-GRPA")
EPISODE = "Show.S02E03.1080p.WEB-DL.DDP5.1.H.264-GRPB"


def _row(title, **extra):
    row = {"title": title, "link": title, "size": "5000000000", "pubdate": ""}
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _replaced(rows):
    """``{title: tag}`` for the rows ``superseded_rows`` marks."""
    marks = superseded_rows(rows)
    return {row["title"]: marks[id(row)] for row in rows if id(row) in marks}


def test_a_repack_replaces_the_same_release_without_the_tag():
    assert _replaced([_row(REPACK), _row(PLAIN)]) == {PLAIN: "REPACK"}


def test_the_order_in_the_list_does_not_matter():
    assert _replaced([_row(PLAIN), _row(REPACK)]) == {PLAIN: "REPACK"}


def test_every_listing_of_the_replaced_release_is_marked():
    rows = [_row(PLAIN), _row(REPACK), _row(PLAIN.replace(".", " "))]

    assert len(superseded_rows(rows)) == 2


def test_a_proper_from_the_same_group_replaces_it_too():
    plain = "Alpha.2024.1080p.WEB-DL.DDP5.1.H.264-GRPB"
    proper = plain.replace("-GRPB", ".PROPER-GRPB")

    assert _replaced([_row(proper), _row(plain)]) == {plain: "PROPER"}


def test_a_rerip_reads_as_a_repack():
    plain = "Alpha.2024.720p.WEB-DL.DDP5.1.H.264-GRPE"
    rerip = plain.replace("-GRPE", ".RERIP-GRPE")

    assert _replaced([_row(rerip), _row(plain)]) == {plain: "REPACK"}


def test_a_repacked_episode_replaces_that_episode_only():
    repack = EPISODE.replace(".1080p", ".REPACK.1080p")
    other = EPISODE.replace("S02E03", "S02E04")

    assert _replaced([_row(repack), _row(EPISODE), _row(other)]) == {EPISODE: "REPACK"}


@pytest.mark.parametrize(
    "other",
    [
        PLAIN.replace("-GRPA", "-GRPZ"),  # another group's file
        PLAIN.replace("2160p", "1080p"),
        PLAIN.replace("TrueHD.7.1.Atmos", "DTS-HD.MA.5.1"),
        PLAIN.replace("TrueHD.7.1", "TrueHD.5.1"),
        PLAIN.replace(".DV.HDR", ".HDR"),
        PLAIN.replace("HEVC", "AVC"),
        PLAIN.replace("UHD.BluRay.REMUX", "WEB-DL"),
        PLAIN.replace(".HYBRID", ""),
        PLAIN.replace("Alpha.2024", "Alpha.2019"),
        PLAIN.replace("Alpha.2024", "Bravo.2024"),
        PLAIN.replace("Alpha.2024", "Alpha.2024.Extended"),
        PLAIN.replace("-GRPA", ""),  # no group: never matched with anything
    ],
)
def test_a_repack_leaves_a_different_release_alone(other):
    assert not _replaced([_row(REPACK), _row(other)])


def test_a_repack_with_no_group_replaces_nothing():
    assert not _replaced([_row(REPACK.replace("-GRPA", "")), _row(PLAIN)])


def test_a_repack_and_a_proper_do_not_replace_each_other():
    proper = PLAIN.replace("-GRPA", ".PROPER-GRPA")

    assert _replaced([_row(REPACK), _row(proper), _row(PLAIN)]) == {PLAIN: "REPACK"}
    assert not _replaced([_row(REPACK), _row(proper)])


def test_nothing_is_replaced_without_a_repack_in_the_list():
    assert not superseded_rows([_row(PLAIN), _row(PLAIN), _row(EPISODE)])
    assert not superseded_rows([_row(REPACK), _row(REPACK)])
    assert not superseded_rows([])


def test_a_season_pack_and_an_episode_do_not_replace_each_other():
    pack = _row(EPISODE.replace(".1080p", ".REPACK.1080p"), _season_pack={"x": 1})

    assert not _replaced([pack, _row(EPISODE)])


def test_synthetic_and_unreadable_rows_take_no_part():
    synthetic = _row(PLAIN, _display_title="Local season pack")
    rows = [_row(REPACK), synthetic, None, "text", {"title": None, "_meta": {}}]

    assert not superseded_rows(rows)


def test_a_row_without_parsed_metadata_is_parsed_from_its_name():
    plain = {"title": PLAIN, "link": "p"}
    repack = {"title": REPACK, "link": "r"}

    assert superseded_rows([repack, plain]) == {id(plain): "REPACK"}


def test_the_rows_are_left_as_they_were():
    rows = [_row(REPACK), _row(PLAIN)]
    before = copy.deepcopy([row["_meta"] for row in rows])

    superseded_rows(rows)

    assert [row["_meta"] for row in rows] == before
    assert rows[0]["_meta"]["repack"] is True


def test_revision_label():
    assert revision_label({"repack": True, "proper": True}) == "REPACK"
    assert revision_label({"proper": True}) == "PROPER"
    assert revision_label({"repack": False}) == ""
    assert revision_label(None) == ""
