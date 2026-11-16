# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""Listings of one payload under different names take one parse."""

from email.utils import formatdate

import pytest
from resources.lib import filter as filter_module
from resources.lib import nfo_audio
from resources.lib.filter_payload import ADOPTED_KEY
from resources.lib.results_groups import release_groups

SIZE = 4400443301
FULL = "Show.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264-FLUX"


@pytest.fixture(autouse=True)
def _no_known_audio():
    nfo_audio.forget_all()
    yield
    nfo_audio.forget_all()


def _row(title, link, size=SIZE, at=0, **extra):
    row = {
        "title": title,
        "link": link,
        "size": str(size),
        "pubdate": formatdate(1768286806 + at),
    }
    row.update(extra)
    return row


def _attached(*rows):
    rows = list(rows)
    filter_module.attach_metadata(rows)
    return rows


def _groups(rows):
    return [[row["link"] for row in group] for group in release_groups(rows)]


def test_a_name_cut_short_by_an_indexer_takes_the_full_names_parse():
    cut, full, other = _attached(
        _row(FULL[: -len("-FLUX")], "cut"),
        _row(FULL, "full", at=40),
        _row(FULL[: -len("-FLUX")], "other", size=SIZE + 977),
    )

    assert cut["_meta"]["group"] == "FLUX"
    assert cut["_meta"][ADOPTED_KEY]
    assert ADOPTED_KEY not in full["_meta"]
    # The same cut name at another size is another upload: nothing says
    # whose, so it keeps its own parse.
    assert other["_meta"]["group"] == ""
    assert _groups([cut, full, other]) == [["cut", "full"], ["other"]]


def test_a_cut_that_took_the_title_is_recovered_from_the_same_upload_only():
    name = "The.Long.Title.Of.It.2024.1080p.BluRay.x264-GRP"
    cut, _full = _attached(_row("The.Long.Title", "cut"), _row(name, "full", at=90))
    assert cut["_meta"]["group"] == "GRP"

    # Posted hours apart it is another upload that happens to weigh the same.
    late, _full = _attached(_row("The.Long.Title", "cut"), _row(name, "full", at=9000))
    assert ADOPTED_KEY not in late["_meta"]
    # Told apart by its poster: also another upload.
    apart, _full = _attached(
        _row("The.Long.Title", "cut", poster="a@example.com"),
        _row(name, "full", at=5, poster="b@example.com"),
    )
    assert ADOPTED_KEY not in apart["_meta"]


def test_one_film_at_one_exact_size_is_one_payload_whenever_it_was_posted():
    short = "Film.2024.2160p.UHD.BluRay.DV.x265-GRP"
    long = "Film.2024.2160p.UHD.BluRay.DV.HDR.x265-GRP"
    one, two, three = _attached(
        _row(short, "one"),
        _row(long, "two", at=86400 * 30),
        _row(short, "three", size=SIZE + 1),
    )

    assert one["_meta"]["hdr"] == two["_meta"]["hdr"] == ["Dolby Vision", "HDR10"]
    assert three["_meta"]["hdr"] == ["Dolby Vision"]
    assert _groups([one, two, three]) == [["one", "two"], ["three"]]


@pytest.mark.parametrize(
    "left, right",
    [
        # Another episode, another film, a sequel, another year, another night.
        ("Show.S01E01.1080p.WEB.H264-GRP", "Show.S01E02.1080p.WEB.DDP5.1.H264-GRP"),
        ("Film.2024.1080p.WEB.H264-GRP", "Other.2024.1080p.WEB.DDP5.1.H264-GRP"),
        ("Dune.2021.1080p.WEB.H264-GRP", "Dune.Part.Two.2021.1080p.WEB.DD5.1.H264-GRP"),
        ("Film.1990.1080p.WEB.H264-GRP", "Film.2020.1080p.WEB.DDP5.1.H264-GRP"),
        ("Show.2026.11.12.720p.WEB.H264-GRP", "Show.2026.11.13.720p.WEB.DD.H264-GRP"),
    ],
)
def test_another_item_at_the_same_size_is_left_alone(left, right):
    rows = _attached(_row(left, "l"), _row(right, "r", at=86400))

    assert not any(ADOPTED_KEY in row["_meta"] for row in rows)


def test_a_round_size_or_no_size_proves_nothing():
    short = "Film.2024.2160p.UHD.BluRay.DV.x265-GRP"
    long = "Film.2024.2160p.UHD.BluRay.DV.HDR.x265-GRP"
    for size in (4400000000, 4 * 1048576 * 1024, 0, "", "big"):
        rows = _attached(_row(short, "one", size=size), _row(long, "two", size=size))

        assert not any(ADOPTED_KEY in row["_meta"] for row in rows), size


def test_the_group_the_list_knows_wins_over_a_reposters_tag():
    real = "Show.S01E01.Episode.Name.2160p.HMAX.WEB-DL.DDP5.1.HDR.x265-NTb"
    repost = "Show-S01E01-Episode.Name-WEBDL-2160p-x265-EAC3-5.1-AsmoFuscated"
    junk = "Show.S01E02.1080p.WEB.H264-NTb_mp4"
    rows = _attached(
        _row(repost, "repost"),
        _row(real, "real", at=86400),
        # NTb is on other names of the list; the reposter's tag on this one.
        _row("Show.S01E01.1080p.HMAX.WEB-DL.DDP5.1.H.264-NTb", "hd", size=SIZE + 5),
        _row("Show.S01E02.1080p.WEB.H264-NTb", "next", size=SIZE + 9),
        _row(junk, "junk", size=SIZE + 9, at=30),
    )

    assert rows[0]["_meta"]["group"] == "NTb"
    assert rows[0]["_meta"]["network"] == rows[1]["_meta"]["network"]
    assert ADOPTED_KEY not in rows[1]["_meta"]
    # "-NTb_mp4" is the longer name, and still the one that gives way.
    assert rows[4]["_meta"]["group"] == "NTb"
    assert ADOPTED_KEY not in rows[3]["_meta"]


def test_a_name_with_no_group_takes_the_named_listings_parse():
    bare, named = _attached(
        _row("Film (1982) HQ AC3 DD5 1 (Externe Subs)", "bare"),
        _row("Film.1982.Directors.Cut.720p.BRRip.XviD.AC3-GRP", "named", at=7200),
    )

    assert bare["_meta"]["group"] == "GRP"
    assert bare["_meta"]["resolution"] == "720p"
    assert _groups([bare, named]) == [["bare", "named"]]


def test_listings_that_end_with_no_group_are_still_listed_together():
    cut, full, apart = _attached(
        _row("Film.2024.1080p.BluRay", "cut"),
        _row("Film.2024.1080p.BluRay.x264.DTS", "full", at=20),
        _row("Film.2024.1080p.BluRay", "apart", size=SIZE + 31),
    )

    assert not full["_meta"]["group"]
    assert cut["_meta"]["codec"] == full["_meta"]["codec"] != ""
    # Not the same name, but one payload: one picker row. The cut name at
    # another size is listed on its own.
    assert _groups([cut, full, apart]) == [["cut", "full"], ["apart"]]


def test_a_row_keeps_its_own_name_and_container():
    cut, full = _attached(
        _row(FULL[: -len("-FLUX")] + ".mp4", "cut"), _row(FULL + ".mkv", "full", at=5)
    )

    assert cut["_meta"]["group"] == "FLUX"
    assert cut["_meta"]["container"] == "mp4"
    assert full["_meta"]["container"] == "mkv"
    assert cut["title"].endswith("H.264.mp4")
    assert _groups([cut, full]) == [["cut"], ["full"]]


def test_a_second_pass_changes_nothing_and_does_not_spread_the_parse():
    cut_name = FULL[: -len("-FLUX")]
    rows = _attached(
        _row(cut_name, "cut"),
        _row(FULL, "full", at=40),
        _row(cut_name.replace(".", " "), "spelled", size=SIZE + 977),
    )
    before = [dict(row["_meta"]) for row in rows]

    # A row joins the list: the whole pass runs again over all of them.
    rows.append(_row(cut_name, "new", size=SIZE + 1234))
    filter_module.attach_metadata(rows)

    assert [row["_meta"] for row in rows[:3]] == before
    # Neither the other spelling nor the new row of the cut name took the
    # parse that belongs to the one upload.
    assert rows[2]["_meta"]["group"] == rows[3]["_meta"]["group"] == ""
    assert ADOPTED_KEY not in rows[3]["_meta"]


def test_a_season_pack_row_and_a_crowd_of_names_are_left_alone():
    pack, _full = _attached(
        _row(FULL[: -len("-FLUX")], "pack", _season_pack={"path": "x"}),
        _row(FULL, "full", at=40),
    )
    assert ADOPTED_KEY not in pack["_meta"]

    crowd = _attached(
        *[
            _row("Film.2024.1080p.WEB.H264-GRP{}".format(number), str(number))
            for number in range(13)
        ]
    )
    assert not any(ADOPTED_KEY in row["_meta"] for row in crowd)
