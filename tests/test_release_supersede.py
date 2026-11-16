# SPDX-License-Identifier: GPL-3.0-or-later
"""Which listed releases their own REPACK or PROPER replaces."""

import copy

import pytest
from resources.lib.filter import parse_title_metadata
from resources.lib.filter_tags import detect_revision
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


def test_a_repacked_season_pack_replaces_the_pack_and_no_episode():
    pack = "Show.S02.1080p.WEB-DL.DDP5.1.H.264-GRPB"
    repack = pack.replace(".1080p", ".REPACK.1080p")
    rows = [
        _row(repack, _season_pack={"x": 1}),
        _row(pack, _season_pack={"x": 2}),
        _row(EPISODE),
    ]

    assert _replaced(rows) == {pack: "REPACK"}


def test_a_row_flagged_as_a_pack_and_one_that_is_not_never_match():
    pack = _row(EPISODE.replace(".1080p", ".REPACK.1080p"), _season_pack={"x": 1})

    assert not _replaced([pack, _row(EPISODE)])


def test_a_daily_shows_repack_replaces_that_night_only():
    night = "Show.2026.11.12.1080p.WEB.h264-GRPB"
    other = "Show.2026.11.13.1080p.WEB.h264-GRPB"
    # The tag straight after the date, where scene names put it.
    repack = "Show.2026.11.12.REPACK.1080p.WEB.h264-GRPB"
    proper = "Show.2026.11.12.PROPER.1080p.WEB.h264-GRPB"

    assert _replaced([_row(repack), _row(night), _row(other)]) == {night: "REPACK"}
    assert _replaced([_row(proper), _row(night), _row(other)]) == {night: "PROPER"}


def test_the_repack_is_named_when_a_proper_replaces_the_release_too():
    proper = PLAIN.replace("-GRPA", ".PROPER-GRPA")

    assert _replaced([_row(proper), _row(REPACK), _row(PLAIN)]) == {PLAIN: "REPACK"}


def test_synthetic_and_unreadable_rows_take_no_part():
    synthetic = _row(PLAIN, _display_title="Local season pack")
    rows = [_row(REPACK), synthetic, None, "text", {"title": None, "_meta": {}}]

    assert not superseded_rows(rows)


def test_a_row_without_parsed_metadata_is_parsed_from_its_name():
    plain = {"title": PLAIN, "link": "p"}
    repack = {"title": REPACK, "link": "r"}

    assert superseded_rows([repack, plain]) == {id(plain): "REPACK"}


def test_the_rows_keep_their_own_tags_and_release_profile():
    from resources.lib.release_match import release_profile

    rows = [_row(REPACK), _row(PLAIN)]
    before = copy.deepcopy([row["_meta"] for row in rows])
    profiles = [release_profile(dict(row)) for row in rows]

    assert superseded_rows(rows)

    assert [row["_meta"] for row in rows] == before
    # The profile cached on each row is its own, tags included: the picker's
    # grouping and the NZBGet backups read it after this.
    assert [release_profile(row) for row in rows] == profiles
    assert dict(profiles[0])["repack"] is True


def test_revision_label_follows_the_parsed_flags():
    assert revision_label({"repack": True, "proper": True}) == "REPACK"
    assert revision_label({"proper": True}) == "PROPER"
    assert revision_label({"repack": False}) == ""
    assert revision_label(None) == ""


@pytest.mark.parametrize(
    "title,tag",
    [
        # Straight after an air date, where the parser misses it.
        ("Show.2026.11.12.REPACK.1080p.WEB.h264-GRP", "repack"),
        ("Show.2026.11.12.PROPER.1080p.WEB.h264-GRP", "proper"),
        ("Show.2026.11.12.1080p.WEB.h264.REPACK2-GRP", "repack"),
        ("Show.S01E02.RERIP.720p.HDTV.x264-GRP", "repack"),
        ("Show.S01E02.RERIP2.720p.HDTV.x264-GRP", "repack"),
        ("Show.S01E02.REAL.PROPER.720p.HDTV.x264-GRP", "proper"),
        ("Show.S01E02.Episode.Title.REPACK.1080p.WEB.h264-GRP", "repack"),
        ("Show.S01E02.PROPER.HDTV.x264-GRP", "proper"),
        # Other tags between the tag and the format tags.
        ("Show.S01E02.PROPER.GERMAN.DL.1080p.WEB.h264-GRP", "proper"),
        ("Show.S01E02.REPACK.AMZN.1080p.WEB-DL.DDP5.1.H.264-GRP", "repack"),
        ("Alpha.2024.REPACK.READNFO.2160p.UHD.BluRay.x265-GRP", "repack"),
        ("Alpha.2024.REPACK.Directors.Cut.1080p.BluRay.x264-GRP", "repack"),
        ("Alpha.2024.PROPER.iTA.ENG.1080p.BluRay.x264-GRP", "proper"),
        ("Alpha.2024.REPACK.BluRay.1080p.DTS.x264-GRP", "repack"),
        ("Alpha.2024.PROPER.REPACK.1080p.BluRay.x264-GRP", "repack"),
        # After the format tags begin, in any case.
        ("Alpha 2024 1080p BluRay x264 Proper-GRP", "proper"),
        ("Alpha.2024.1080p.BluRay.REPACK.x264-GROUP-NAME", "repack"),
        # A name with no capitals, no format tags, an extension, no year.
        ("show.s01e02.repack.1080p.web.h264-grp", "repack"),
        ("Show.S01E02.REPACK-GRP", "repack"),
        ("Show.S01E02.REPACK-GRP.mkv", "repack"),
        ("[Group] Show - 05 REPACK (1080p)", "repack"),
        # A word of a title, an episode title or a guest's name.
        ("Proper.Villains.2019.1080p.WEB.h264-GRP", ""),
        ("Show.S03E02.A.Proper.Send.Off.720p.HDTV.x264-GRP", ""),
        ("Show.S01E05.Repack.Day.1080p.WEB.h264-GRP", ""),
        ("The.Late.Show.2026.11.12.Proper.Guest.720p.WEB.h264-GRP", ""),
        ("The.Late.Show.2026.11.12.John.Proper.720p.WEB.h264-GRP", ""),
        ("Show.S01E02.Mr.Proper.720p.HDTV.x264-GRP", ""),
        ("Show.S01E02.Proper.2.720p.HDTV.x264-GRP", ""),
        ("Show.S01E02.A.Proper.Ending-GRP", ""),
        ("Show.S01E02.Mr.Proper-GRP", ""),
        # A title word that reads as a source or an audio format before it.
        ("Show.S01E02.Charlottes.Web.Proper.Guest.720p.HDTV.x264-GRP", ""),
        ("Show.S01E02.Magnum.Opus.Repack.Day.720p.HDTV.x264-GRP", ""),
        ("Show.S01E02.Charlottes.Web.Proper.Guest.HDTV.x264-GRP", ""),
        # A name with no resolution: a title word can read as its source.
        ("Show.S01E05.Charlottes.Web.Proper.HDTV.x264-GRP", ""),
        ("Show.S01E05.Web.of.Lies.Proper.DVDRip.XviD-GRP", ""),
        ("Show.S01E05.Charlottes.Web.PROPER.HDTV.x264-GRP", "proper"),
        # A name whose first marker is its resolution: the title comes first.
        ("[Group] Keeping It Proper - 05 [1080p].mkv", ""),
        ("[Group] Show Repack - 05 (1080p) [12345678].mkv", ""),
        # All in one case, capitals as well as none: judged by position alone.
        ("SHOW.S01E05.REPACK.720P.HDTV.X264-GRP", "repack"),
        ("SHOW.S01E05.A.PROPER.SEND.OFF.720P.HDTV.X264-GRP", ""),
        ("SHOW.S01E05.KEEPING.IT.PROPER.720P.HDTV.X264-GRP", "proper"),
        # Part of another word, or no tag at all.
        ("Alpha.2024.1080p.WEB.h264-REPACKERS", ""),
        ("Show.2026.11.12.1080p.WEB.h264-GRP", ""),
        ("no tags at all", ""),
        ("", ""),
    ],
)
def test_only_a_word_written_as_a_tag_is_the_tag(title, tag):
    assert detect_revision(title) == tag
    assert parse_title_metadata(title)["revision"] == tag


def test_a_tag_the_parser_missed_sets_its_flag_and_no_other_flag_changes():
    after_date = parse_title_metadata("Show.2026.11.12.REPACK.1080p.WEB.h264-GRP")
    guest = parse_title_metadata(
        "The.Late.Show.2026.11.12.Proper.Guest.720p.WEB.h264-GRP"
    )

    assert (after_date["repack"], after_date["proper"]) == (True, False)
    # The parser reads no tag after a date, and the word here is not one.
    assert (guest["repack"], guest["proper"]) == (False, False)


def test_an_episode_title_word_strikes_nothing_and_is_not_struck():
    titled = "Show.S03E02.A.Proper.Send.Off.720p.HDTV.x264-GRPB"
    plain = "Show.S03E02.720p.HDTV.x264-GRPB"
    proper = "Show.S03E02.PROPER.720p.HDTV.x264-GRPB"

    # The parser flags the titled name as a PROPER; the picker is not sure.
    assert _row(titled)["_meta"]["proper"] is True
    assert not _replaced([_row(titled), _row(plain)])
    # A real PROPER replaces the plain listing. The titled one may be the
    # first file or the PROPER itself, so it is left alone.
    assert _replaced([_row(titled), _row(proper), _row(plain)]) == {plain: "PROPER"}


def test_a_tag_written_an_unusual_way_is_never_struck():
    odd = "Alpha.2024.REPACK.Uncommonword.1080p.BluRay.x264-GRPA"
    repack = "Alpha.2024.Uncommonword.REPACK.1080p.BluRay.x264-GRPA"

    assert detect_revision(odd) == "" and _row(odd)["_meta"]["repack"] is True
    assert detect_revision(repack) == "repack"
    # Cleared of the tag the two are one release, so only the flag on the
    # odd one keeps it from being struck.
    assert not _replaced([_row(odd), _row(repack)])
    assert _replaced([_row(repack), _row(repack.replace(".REPACK", ""))])


def test_a_name_stuffed_with_the_word_costs_a_bounded_look():
    import time

    name = "Show.S01E01." + "PROPER." * 5000 + "1080p-GRP"
    started = time.monotonic()

    # Only the head of the name is read, so the answer is the head's.
    assert detect_revision(name) == "proper"
    assert time.monotonic() - started < 1.0


def test_metadata_cached_without_the_tag_is_read_from_the_name():
    plain, repack, titled = (
        _row(PLAIN),
        _row(REPACK),
        _row("Show.S03E02.A.Proper.Send.Off.720p.HDTV.x264-GRPB"),
    )
    for row in (plain, repack, titled):
        del row["_meta"]["revision"]

    assert superseded_rows([repack, plain, titled]) == {id(plain): "REPACK"}


def test_a_bucket_of_many_unrelated_rows_compares_each_fixed_row_once():
    from unittest.mock import patch

    from resources.lib import release_supersede

    def word(i):
        return chr(97 + i // 26) + chr(97 + i % 26) + "story"

    name = "{}.2024.1080p.WEB-DL.DDP5.1.H.264{}-GRPB"
    plain = [_row(name.format(word(i), "")) for i in range(40)]
    repacks = [_row(name.format(word(i), ".REPACK")) for i in range(40, 45)]
    with patch.object(
        release_supersede, "_untagged", wraps=release_supersede._untagged
    ) as copies:
        assert not superseded_rows(plain + repacks)

    assert copies.call_count == len(plain) + len(repacks)
