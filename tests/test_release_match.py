# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared same-release rule for NZBGet backups and the picker's grouping."""

import pytest
from resources.lib.release_match import release_profile, same_release

_PICK = "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP"


def _row(title, size="4000000000"):
    return {"title": title, "link": title, "size": size}


@pytest.mark.parametrize(
    "title",
    [
        "Show S01E02 1080p AMZN WEB-DL DDP5 1 H 264-GRP",  # other spelling
        "Show.S01E02.Episode.Title.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP",
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264.SDR-GRP",  # SDR adds nothing
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264.ENG.SUBBED-GRP",  # subs, language
        '[1/3] - "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP.mkv" yEnc 1234',
    ],
)
def test_same_release_groups_rows_whose_parsed_fields_all_match(title):
    assert same_release(_row(_PICK), _row(title, size="3500000000"))


@pytest.mark.parametrize(
    "title",
    [
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.265-GRP",  # codec
        "Show.S01E02.1080p.AMZN.WEBRip.DDP5.1.H.264-GRP",  # quality
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP2.0.H.264-GRP",  # channels
        "Show.S01E02.1080p.AMZN.WEB-DL.AAC2.0.H.264-GRP",  # audio
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.HDR.H.264-GRP",  # HDR
        "Show.S01E02.2160p.AMZN.WEB-DL.DDP5.1.H.264-GRP",  # resolution
        "Show.S01E02.REPACK.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP",  # REPACK
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-OTHER",  # group
        "Show.S01E03.1080p.AMZN.WEB-DL.DDP5.1.H.264-GRP",  # episode
        "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1-GRP",  # codec unknown vs known
    ],
)
def test_same_release_splits_on_any_parsed_difference(title):
    assert not same_release(_row(_PICK), _row(title))


def test_rows_without_a_group_are_never_grouped():
    groupless = "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264"
    assert not same_release(_row(groupless), _row(groupless))


@pytest.mark.parametrize("group", ["NOGRP", "P2P"])
def test_a_literal_placeholder_group_is_a_group(group):
    title = "Show.S01E02.1080p.AMZN.WEB-DL.DDP5.1.H.264-" + group
    assert same_release(_row(title), _row(title.replace(".", " ")))


def test_release_profile_is_hashable_and_ignores_languages():
    english = release_profile(_row(_PICK.replace("-GRP", ".ENGLISH-GRP")))
    plain = release_profile(_row(_PICK))
    assert hash(english) == hash(plain)
    assert english == plain


def test_same_release_reuses_parsed_metadata_instead_of_reparsing():
    """Sorting compares a row many times; PTT must not run on each compare."""
    from unittest.mock import patch

    from resources.lib.filter import parse_title_metadata

    rows = [_row(_PICK), _row(_PICK.replace(".", " "))]
    for row in rows:
        row["_meta"] = parse_title_metadata(row["title"])
    assert rows[0]["_meta"]["variant"] == []
    assert same_release(rows[0], rows[1])  # warms the per-row caches
    with patch("resources.lib.ptt.parse_title", side_effect=AssertionError):
        for _ in range(3):
            assert same_release(rows[0], rows[1])
            assert same_release(rows[1], rows[0])


def test_variant_tags_are_stored_in_the_metadata():
    from resources.lib.filter import parse_title_metadata

    meta = parse_title_metadata("Movie.2020.3D.HSBS.1080p.BluRay.x264-GRP")
    assert meta["variant"] == ["3d"]
