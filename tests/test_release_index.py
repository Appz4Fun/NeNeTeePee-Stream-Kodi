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

    def backups(pick, listed=rows):
        return [row["link"] for row in _same_release_backups(pick, listed)]

    # The yearless name is listed with the 2005 show, the first it meets: a
    # 2023 episode is no backup of it, though the rule accepts the pair.
    assert backups(rows[0]) == ["o1"]
    assert backups(rows[1]) == ["y1"]
    assert backups(rows[2]) == ["n2"]
    # A pick that is a copy of a listed row stands where that row does.
    assert backups(dict(rows[2])) == ["n2"]
    # ...which is then grouped ahead of the list, so the yearless name
    # joins it: what the rule says of that pair, as before the index.
    assert backups(rows[3], rows[:3]) == ["n1", "y1"]
    assert backups(_row("Show.S01E01.1080p.WEB-DL.DDP5.1.H264", "g1")) == []
