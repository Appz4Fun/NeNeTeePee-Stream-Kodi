# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""One grouping of a result list into releases (``release_index``)."""

import random
from unittest.mock import patch

import grouping_corpus
from resources.lib import release_index, release_match
from resources.lib.filter import parse_title_metadata

ALPHA = "Alpha.2024.1080p.WEB-DL.DDP5.1.H264-GRPA"


def _row(title, link, **extra):
    row = {"title": title, "link": link, "size": "5000000000", "pubdate": ""}
    row.update(extra)
    row["_meta"] = parse_title_metadata(title)
    return row


def _links(index):
    return [[row["link"] for row in group] for group in index.groups]


def test_group_of_gives_the_same_list_to_every_member():
    rows = [_row(ALPHA, "a1"), _row("Bravo.2024.1080p.WEB-DL.H264-GRPB", "b1")]
    rows.append(_row(ALPHA.replace(".", " "), "a2"))

    index = release_index.build(rows)

    assert _links(index) == [["a1", "a2"], ["b1"]]
    assert index.group_of(rows[0]) is index.group_of(rows[2])
    stranger = _row(ALPHA, "a3")
    assert index.group_of(stranger) == [stranger]


def test_rows_that_cannot_be_read_are_each_their_own_group():
    rows = [None, "name", {"link": "x"}, _row("Alpha.2024.1080p.WEB-DL.H264", "n1")]

    assert [len(group) for group in release_index.build(rows).groups] == [1, 1, 1, 1]


def test_a_season_pack_groups_with_packs_and_never_with_an_episode():
    title = "Show.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP"
    rows = [
        _row(title, "e1"),
        _row(title, "p1", _season_pack={"path": "x"}),
        _row(title, "e2"),
        _row(title, "p2", _season_pack={"path": "y"}),
    ]

    assert _links(release_index.build(rows)) == [["e1", "e2"], ["p1", "p2"]]


def test_the_quick_look_never_refuses_what_the_rule_accepts():
    # Names of one profile that differ in year, part, container, episode
    # title and season: every pair the rule accepts must pass ``_may_match``.
    base = "{title}{year}{episode}.1080p.WEB-DL.DDP5.1.H264-GRP{ext}"
    names = [
        base.format(title=title, year=year, episode=episode, ext=ext)
        for title in ("Show", "Show.Part.1", "Show.Part.2")
        for year in ("", ".2005", ".2023")
        for episode in ("", ".S01", ".S01E01", ".S01E01.Chapter.One")
        for ext in ("", ".mkv", ".mp4")
    ]
    rows = [_row(name, str(number)) for number, name in enumerate(names)]
    accepted = 0
    for left in rows:
        for right in rows:
            if release_match.same_release(left, right):
                accepted += 1
                assert release_index._may_match(
                    release_index._keys(left)[1], release_index._keys(right)[1]
                ), (left["title"], right["title"])
    assert accepted > len(rows)


def test_any_order_of_awkward_names_groups_as_the_rule_does_pair_by_pair():
    names = [
        "Show{year}.S01E01{title}.1080p.WEB-DL.DDP5.1.H264-GRP{ext}".format(
            year=year, title=title, ext=ext
        )
        for year in ("", ".2005", ".2023")
        for title in ("", ".Chapter.One", ".Chapter.Two")
        for ext in ("", ".mkv", ".mp4")
    ]
    names += [
        "Film{year}{part}.1080p.BluRay.DTS.x264-GRP{ext}".format(
            year=year, part=part, ext=ext
        )
        for year in ("", ".1990", ".2020")
        for part in ("", ".Part.1", ".Part.2")
        for ext in ("", ".mkv")
    ]
    shuffler = random.Random(7)
    for _ in range(40):
        picked = [shuffler.choice(names) for _ in range(30)]
        rows = [_row(name, str(number)) for number, name in enumerate(picked)]

        fast = grouping_corpus.partition(release_index.build(rows).groups)

        assert fast == grouping_corpus.partition(grouping_corpus.reference_groups(rows))


def test_the_grouping_is_the_same_for_any_order_of_the_rows():
    # The list is grouped before it is ranked and again after: a name that
    # fits two releases must go to the same one both times.
    names = [
        "Show.2005.S01E01.1080p.WEB.x264-GRP",
        "Show.2023.S01E01.1080p.WEB.x264-GRP",
        "Show.2023.S01E01.1080p.WEB.x264-GRP",
        "Show.S01E01.1080p.WEB.x264-GRP",
        "Show.S01E01.1080p.WEB.x264-GRP.mkv",
        "Show.2005.S01E01.1080p.WEB.x264-GRP.mp4",
        "Show.S01E01.1080p.WEB.x264",
        "Show S01E01 1080p WEB x264",
    ]
    rows = [_row(name, str(number)) for number, name in enumerate(names)]
    expected = grouping_corpus.partition(release_index.build(rows).groups)
    shuffler = random.Random(3)
    for _ in range(30):
        mixed = list(rows)
        shuffler.shuffle(mixed)
        index = release_index.build(mixed)

        assert grouping_corpus.partition(index.groups) == expected
        # Groups and their rows still read in the order the rows came.
        position = {id(row): at for at, row in enumerate(mixed)}
        flat = [position[id(row)] for group in index.groups for row in group[:1]]
        assert flat == sorted(flat)
        for group in index.groups:
            inside = [position[id(row)] for row in group]
            assert inside == sorted(inside)


def test_a_row_is_ranked_by_the_count_of_the_row_the_picker_lists_it_in():
    from resources.lib.filter import _nzb_copy_counts, _sort_results
    from resources.lib.results_groups import release_groups
    from resources.lib.upload_count import count_copies

    rows = [
        _row("Show.2005.S01E01.1080p.WEB.x264-GRP", "b", size="3000000001"),
        _row("Show.2023.S01E01.1080p.WEB.x264-GRP", "c1", size="9000000001"),
        _row("Show.2023.S01E01.1080p.WEB.x264-GRP", "c2", size="2000000001"),
        _row("Show.S01E01.1080p.WEB.x264-GRP", "a", size="5000000001"),
    ]
    before = _nzb_copy_counts(rows)

    ranked = _sort_results(rows, {"sort_order": 0})

    for group in release_groups(ranked):
        assert {before[id(row)] for row in group} == {count_copies(group)}
    assert sorted(len(group) for group in release_groups(ranked)) == [1, 3]


def test_many_years_of_one_title_are_not_put_to_the_rule_pair_by_pair():
    rows = [
        _row("Film.{}.1080p.BluRay.DTS.x264-GRP".format(year), str(year))
        for year in range(1900, 2100)
    ]
    real = release_match.same_release
    with patch.object(release_match, "same_release", side_effect=real) as rule:
        index = release_index.build(rows)

    assert len(index.groups) == 200
    assert rule.call_count == 0


def test_backups_are_the_other_rows_of_the_picks_picker_row():
    from resources.lib.router_play import _same_release_backups

    rows = [
        _row("Show.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP", "y1"),
        _row("Show.2005.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP", "o1"),
        _row("Show.2023.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP", "n1"),
        _row("Show.2023.S01E01.1080p.WEB-DL.DDP5.1.H264-GRP", "n2"),
    ]

    def backups(pick, listed=None):
        listed = rows if listed is None else listed
        return [row["link"] for row in _same_release_backups(pick, listed)]

    # The name with no year fits both shows. It is listed with the one more
    # indexers list, the 2023 show, and the 2005 episode is no backup of it
    # though the rule accepts that pair.
    assert backups(rows[0]) == ["n1", "n2"]
    assert backups(rows[1]) == []
    assert backups(rows[2]) == ["n2", "y1"]
    # A pick that is a copy of a listed row stands where that row does.
    assert backups(dict(rows[2])) == ["n2", "y1"]
    # ...which is then grouped ahead of the list, so the yearless name
    # joins it: what the rule says of that pair, as before the index.
    assert backups(rows[3], rows[:3]) == ["n1", "y1"]
    assert backups(_row("Show.S01E01.1080p.WEB-DL.DDP5.1.H264", "g1")) == []


def test_a_groupless_pick_is_backed_up_by_the_other_listings_of_its_name():
    from resources.lib.router_play import _same_release_backups

    name = "Show.S01E01.1080p.WEB-DL.DDP5.1.H264"
    rows = [
        _row(name, "u1"),
        _row(name + "-GRP", "g1"),
        _row(name.replace(".", " "), "u2"),
        _row(name.replace("1080p", "720p"), "o1"),
        _row(name, "big", size="60000000000"),
        _row(name, "u3"),
    ]
    from resources.lib.filter import _demote_size_outliers

    _demote_size_outliers(rows)

    backups = [row["link"] for row in _same_release_backups(rows[0], rows)]

    # Its own name first, then the other spelling; never the release that
    # has a group, another resolution, or the copy twelve times its size.
    assert backups == ["u3", "u2"]
    # The rule itself still refuses a name with no group, pair by pair.
    assert not release_match.same_release(rows[0], rows[5])
