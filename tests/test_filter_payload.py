# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A listing with no group takes the group of the upload it is."""

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


def test_two_names_of_one_group_for_one_upload_are_one_release():
    short = "Film.2024.2160p.UHD.BluRay.DV.x265-GRP"
    long = "Film.2024.2160p.UHD.BluRay.DV.HDR.x265-GRP"
    one, two, three, four = _attached(
        _row(short, "one"),
        _row(long, "two", at=86400 * 30),
        _row(short, "three", size=SIZE + 1),
        _row(long, "four", size=SIZE + 2),
    )

    # Each name keeps what it says: a listing that states a group never
    # takes another parse.
    assert one["_meta"]["hdr"] == three["_meta"]["hdr"] == ["Dolby Vision"]
    assert two["_meta"]["hdr"] == ["Dolby Vision", "HDR10"]
    assert not any(ADOPTED_KEY in row["_meta"] for row in (one, two, three, four))
    # One listing of each is the same upload, so the two names are one
    # release: every listing of both, not just those two.
    assert _groups([one, two, three, four]) == [["one", "two", "three", "four"]]
    # Without that upload they are two releases, as the rule has it.
    assert _groups([two, three]) == [["two"], ["three"]]


def test_another_groups_name_at_the_same_size_is_left_alone():
    real = "Show.S01E01.Episode.Name.2160p.HMAX.WEB-DL.DDP5.1.HDR.x265-NTb"
    repost = "Show-S01E01-Episode.Name-WEBDL-2160p-x265-EAC3-5.1-AsmoFuscated"
    other = "Show.S01E01.2160p.HMAX.WEBRip.DDP5.1.x264-OTHER"
    rows = _attached(
        _row(real, "real"), _row(repost, "repost", at=40), _row(other, "other", at=80)
    )

    assert [row["_meta"]["group"] for row in rows] == ["NTb", "AsmoFuscated", "OTHER"]
    assert not any(ADOPTED_KEY in row["_meta"] for row in rows)
    assert _groups(rows) == [["real"], ["repost"], ["other"]]


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


def test_a_listing_that_states_a_group_is_never_changed():
    rows = _attached(
        # "-GUA" is "-GUACAMOLE" cut short: same upload, same minute.
        _row("Film.2009.SPECIAL.EDITION.2160p.UHD.BluRay.x265-GUA", "cut"),
        _row("Film.2009.SPECIAL.EDITION.2160p.UHD.BluRay.x265-GUACAMOLE", "full", at=9),
        # The same cut name at another size: the cut name's own release.
        _row(
            "Film.2009.SPECIAL.EDITION.2160p.UHD.BluRay.x265-GUA", "cut2", size=SIZE + 1
        ),
        _row("Show.S01E02.1080p.WEB.H264-NTb", "next", size=SIZE + 9),
        _row("Show.S01E02.1080p.WEB.H264-NTb_mp4", "junk", size=SIZE + 9, at=30),
        # Not the start of the other group: two releases.
        _row("Film.2014.2160p.REMUX.HEVC-FraMeSToR.D", "fra", size=SIZE + 77),
        _row("Film.2014.2160p.REMUX.HEVC-FraMeSToR.DUAL-Rick", "rick", size=SIZE + 77),
    )
    by_link = {row["link"]: row["_meta"] for row in rows}

    # Every row keeps the group its name states.
    assert by_link["cut"]["group"] == by_link["cut2"]["group"] == "GUA"
    assert by_link["full"]["group"] == "GUACAMOLE"
    assert by_link["junk"]["group"] != by_link["next"]["group"] == "NTb"
    assert not any(ADOPTED_KEY in meta for meta in by_link.values())
    # The picker lists a group with its cut: the whole of both releases, so
    # the cut name's other copy is not left on a row of its own.
    assert sorted(sorted(group) for group in _groups(rows)) == [
        ["cut", "cut2", "full"],
        ["fra"],
        ["junk", "next"],
        ["rick"],
    ]


def test_a_cut_name_never_changes_which_episode_a_row_is():
    rows = _attached(
        _row("Show.Name.S01E1", "one"),
        _row("Show.Name.S01E10.1080p.WEB-DL.x264-GRP", "ten", at=5),
        _row("Film.Name.2019.720p.BluRay.H264", "film", size=SIZE + 3),
        _row("Film.Name.2019.720p.BluRay.H264-20-40", "odd", size=SIZE + 3, at=5),
    )

    assert not any(ADOPTED_KEY in row["_meta"] for row in rows)
    identity = rows[0]["_meta"]["ptt_identity"]
    assert identity["episodes"] == [1]
    assert not rows[2]["_meta"]["ptt_identity"]["episodes"]


def test_a_name_with_no_group_between_two_groups_takes_neither():
    bare, _one, _two = _attached(
        _row("Film 2021 1080p", "bare"),
        _row("Film.2021.1080p.BluRay.x264-AAA", "a", at=86400),
        _row("Film.2021.1080p.BluRay.x264-BBB", "b", at=2 * 86400),
    )

    assert bare["_meta"]["group"] == "" and ADOPTED_KEY not in bare["_meta"]


def test_the_same_rows_in_any_order_come_to_the_same_parses():
    import random

    names = [
        "Film.2023.BluRay.2160p.DV.HEVC-GRP",
        "Film.2023.BluRay.2160p.HDR.DTS-HD.AC3.HEVC.REMUX-GRP",
        "Film 2023 BluRay 2160p HEVC",
        "Film.2023.BluRay.2160p.HEVC.DTS",
        "Film.2023.BluRay.2160p.DV.HEVC-OTHER",
    ]

    def outcome(order):
        rows = _attached(*[_row(names[at], str(at), at=at * 5) for at in order])
        parses = {
            row["link"]: (
                row["_meta"]["group"],
                row["_meta"]["quality"],
                tuple(row["_meta"]["hdr"]),
                tuple(row["_meta"]["audio"]),
            )
            for row in rows
        }
        return parses, sorted(sorted(group) for group in _groups(rows))

    expected = outcome(range(len(names)))
    shuffler = random.Random(5)
    for _ in range(20):
        order = list(range(len(names)))
        shuffler.shuffle(order)
        assert outcome(order) == expected


def test_names_with_no_group_that_already_parse_alike_are_still_one_row():
    rows = _attached(
        _row("Film.2020.1080p.WEB-DL.x264", "hyphen"),
        _row("Film.2020.1080p.WEBDL.x264", "joined", at=86400),
    )

    assert not rows[0]["_meta"]["group"] and not rows[1]["_meta"]["group"]
    assert _groups(rows) == [["hyphen", "joined"]]


def test_a_size_shown_in_gigabytes_and_turned_back_proves_nothing():
    short = "Film.2024.2160p.UHD.BluRay.DV.x265-GRP"
    long = "Film.2024.2160p.UHD.BluRay.DV.HDR.x265-GRP"
    cut_name = "Film.2024.2160p.UHD.BluRay.DV.HDR.x265"
    for size in (int(4.37 * 1024**3), int(512.3 * 1024**2), 512300000):
        rows = _attached(
            _row(short, "one", size=size),
            _row(long, "two", size=size),
            _row(cut_name, "cut", size=size),
        )

        assert rows[2]["_meta"]["group"] == "", size
        assert _groups(rows) == [["one"], ["two"], ["cut"]], size
    # To the kilobyte is exact enough.
    rows = _attached(
        _row(short, "one", size=11147613000),
        _row(long, "two", size=11147613000),
        _row(cut_name, "cut", size=11147613000),
    )
    assert rows[2]["_meta"]["group"] == "GRP"
    assert len(_groups(rows)) == 1


def test_a_name_with_no_group_takes_the_group_and_keeps_its_own_tags():
    bare, named = _attached(
        _row(
            "Film.1982.Final.Cut.BluRay.2160p.DV.HDR.TrueHD.HEVC.NL-Subs.REMUX", "bare"
        ),
        _row(
            "Film.1982.Final.Cut.BluRay.2160p.DV.HDR.TrueHD.HEVC-GRP", "named", at=7200
        ),
    )

    assert named["_meta"]["quality"].lower() == "bluray"
    # The group is the upload's; REMUX is what this name says, and stays.
    assert bare["_meta"]["group"] == "GRP"
    assert bare["_meta"]["quality"].lower() == "bluray remux"
    assert bare["_meta"][ADOPTED_KEY]
    assert _groups([bare, named]) == [["bare", "named"]]


def test_a_bare_title_takes_the_year_it_lacks():
    bare, named = _attached(
        _row("Film 1080p BluRay DTS", "bare"),
        _row("Film.1982.1080p.BluRay.DTS.x264-GRP", "named", at=7200),
    )

    assert bare["_meta"]["group"] == "GRP" and bare["_meta"]["year"] == 1982
    assert bare["_meta"]["codec"] == ""
    assert _groups([bare, named]) == [["bare", "named"]]


def test_listings_that_end_with_no_group_are_still_listed_together():
    cut, full, apart = _attached(
        _row("Film.2024.1080p.BluRay", "cut"),
        _row("Film.2024.1080p.BluRay.x264.DTS", "full", at=20),
        _row("Film.2024.1080p.BluRay", "apart", size=SIZE + 31),
    )

    assert not full["_meta"]["group"]
    assert cut["_meta"]["codec"] == full["_meta"]["codec"] != ""
    # Not the same name, but one upload: one picker row. The cut name at
    # another size is the cut name's release too, so it is listed with them.
    assert apart["_meta"]["codec"] == ""
    assert _groups([cut, full, apart]) == [["cut", "full", "apart"]]


def test_a_row_keeps_its_own_name_and_container():
    cut, full = _attached(
        _row(FULL[: -len("-FLUX")] + ".mp4", "cut"), _row(FULL + ".mkv", "full", at=5)
    )

    assert cut["_meta"]["group"] == "FLUX"
    assert cut["_meta"]["container"] == "mp4"
    assert full["_meta"]["container"] == "mkv"
    assert cut["title"].endswith("H.264.mp4")
    # One exact size is one file, whatever extension each name gives it.
    assert _groups([cut, full]) == [["cut", "full"]]


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


def test_what_was_read_for_one_name_reaches_the_uploads_other_names():
    cut_name = FULL[: -len("-FLUX")]
    rows = _attached(_row(cut_name, "cut"), _row(FULL, "full", at=40))
    assert rows[0]["_meta"]["audio"] == rows[1]["_meta"]["audio"] == ["DD+"]

    # MediaInfo read under the full name says the track has Atmos.
    nfo_audio._remember(
        filter_module._name_form_key(FULL), ["Atmos", "DD+"], "5.1", exact=True
    )
    filter_module.attach_metadata(rows)

    assert rows[0]["_meta"]["audio"] == rows[1]["_meta"]["audio"] == ["Atmos", "DD+"]
    assert _groups(rows) == [["cut", "full"]]
    # A further pass changes nothing.
    before = [dict(row["_meta"]) for row in rows]
    nfo_audio._remember("some other name", ["DTS"], "5.1", exact=True)
    filter_module.attach_metadata(rows)
    assert [row["_meta"] for row in rows] == before


def test_a_read_made_under_the_cut_name_reaches_the_full_name():
    cut_name = FULL[: -len("-FLUX")]
    nfo_audio._remember(
        filter_module._name_form_key(cut_name), ["Atmos", "DD+"], "5.1", exact=True
    )
    rows = _attached(_row(cut_name, "cut"), _row(FULL, "full", at=40))

    assert rows[0]["_meta"]["group"] == "FLUX"
    assert rows[0]["_meta"]["audio"] == rows[1]["_meta"]["audio"] == ["Atmos", "DD+"]
    assert _groups(rows) == [["cut", "full"]]
