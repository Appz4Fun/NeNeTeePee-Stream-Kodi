# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A WEB-DL whose name leaves Atmos out takes it from a labelled copy."""

from resources.lib import filter as filter_module
from resources.lib.filter_atmos import INFERRED_KEY, share_atmos
from resources.lib.filter_audio import audio_tier

_SHOW = "Some.Show.S03E02.The.Gift"
LABELLED = _SHOW + ".2160p.AMZN.WEB-DL.DD+5.1.Atmos.DoVi.H.265-playWEB"
PLAIN = _SHOW + ".2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-NTb"


def _rows(*titles):
    rows = [
        {"title": title, "link": "http://hydra/{}".format(n), "size": str(10**10)}
        for n, title in enumerate(titles)
    ]
    filter_module.attach_metadata(rows)
    return rows


def _took(row):
    return row["_meta"].get(INFERRED_KEY) is True


def _getter(key, default=""):
    return default


def test_an_unlabelled_copy_of_the_same_stream_takes_atmos():
    labelled, plain = _rows(LABELLED, PLAIN)

    assert _took(plain)
    assert sorted(plain["_meta"]["audio"]) == ["Atmos", "DD+"]
    assert audio_tier(plain["_meta"]["audio"], "WEB-DL") == 1
    # The labelled row is as it was parsed.
    assert not _took(labelled)
    assert INFERRED_KEY not in labelled["_meta"]


def test_it_shows_and_ranks_as_atmos():
    from resources.lib.release_display import display_name

    other = _SHOW + ".2160p.AMZN.WEB-DL.DDP5.1.H.265-FLUX"
    alone = _rows(other)[0]
    shared = _rows(LABELLED, other)[1]
    assert "Atmos" not in display_name(alone)
    assert "DD+ Atmos 5.1" in display_name(shared)

    # Without a labelled copy the preferred group leads; with one, the two
    # are the same audio tier and the group decides, as between labelled rows.
    rows = _rows(PLAIN, LABELLED)
    filtered, _all = filter_module.filter_results(rows, settings_getter=_getter)
    assert [audio_tier(r["_meta"]["audio"], "WEB-DL") for r in filtered] == [1, 1]


def test_nothing_is_taken_without_a_labelled_copy():
    plain, other = _rows(PLAIN, _SHOW + ".2160p.AMZN.WEB-DL.DDP5.1.H.265-FLUX")
    assert not _took(plain) and not _took(other)
    assert plain["_meta"]["audio"] == ["DD+"]


def test_a_hybrid_neither_gives_nor_takes():
    hybrid_labelled = _SHOW + ".2160p.AMZN.WEB-DL.HYBRID.DD+5.1.Atmos.DoVi.H.265-GRP"
    plain = _rows(hybrid_labelled, PLAIN)[1]
    assert not _took(plain)

    hybrid_plain = _SHOW + ".2160p.AMZN.WEB-DL.Hybrid.DDP5.1.DV.H.265-GRP"
    rows = _rows(LABELLED, hybrid_plain, PLAIN)
    assert not _took(rows[1])
    assert rows[1]["_meta"]["audio"] == ["DD+"]
    assert _took(rows[2])


def _plain_variant(old, new):
    assert old in PLAIN
    return PLAIN.replace(old, new)


def test_only_the_same_stream_takes_it():
    different = [
        # another episode
        _plain_variant("S03E02", "S03E03"),
        # another service, and no service named
        _plain_variant("AMZN", "ATVP"),
        _plain_variant(".AMZN", ""),
        # another resolution, video codec, channel count
        _plain_variant("2160p", "1080p"),
        _plain_variant("H.265", "H.264"),
        _plain_variant("DDP5.1", "DDP2.0"),
        # re-encoded, or from a disc
        _plain_variant("WEB-DL", "WEBRip"),
        _plain_variant("AMZN.WEB-DL", "BluRay"),
        # plain Dolby Digital and AAC are other tracks
        _plain_variant("DDP5.1", "DD5.1"),
        _plain_variant("DDP5.1", "AAC5.1"),
        # a dub, and a multi-language release: other audio tracks
        _plain_variant("WEB-DL", "WEB-DL.German"),
        _plain_variant("WEB-DL", "WEB-DL.MULTi"),
        # another show
        PLAIN.replace("Some.Show", "Other.Show"),
    ]
    rows = _rows(LABELLED, *different)
    for row in rows[1:]:
        assert not _took(row), row["title"]
        assert "Atmos" not in row["_meta"]["audio"], row["title"]


def test_the_same_stream_under_other_spellings_takes_it():
    same = [
        PLAIN.replace(".", " "),
        _plain_variant("DDP5.1", "DD+5.1"),
        _plain_variant("H.265", "HEVC"),
        _plain_variant("-NTb", "-FLUX"),
        _plain_variant("2160p", "iNTERNAL.2160p"),
        # HDR is not part of what the audio track is.
        _plain_variant(".DV", ""),
        _plain_variant("WEB-DL", "WEB"),
    ]
    rows = _rows(LABELLED, *same)
    for row in rows[1:]:
        assert _took(row), row["title"]


def test_a_movie_takes_it_only_for_the_same_year_and_cut():
    labelled = "Some.Movie.2024.2160p.AMZN.WEB-DL.DDP5.1.Atmos.DV.H.265-FLUX"
    rows = _rows(
        labelled,
        "Some.Movie.2024.2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-NTb",
        "Some.Movie.1999.2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-NTb",
        "Some.Movie.2024.Extended.2160p.AMZN.WEB-DL.DDP5.1.DV.H.265-NTb",
    )
    assert [_took(row) for row in rows] == [False, True, False, False]


def test_a_truehd_atmos_disc_gives_nothing_to_a_web_dl():
    rows = _rows(
        _SHOW + ".2160p.UHD.BluRay.REMUX.DV.TrueHD.7.1.Atmos-FraMeSToR",
        PLAIN,
    )
    assert not _took(rows[1])


def test_a_row_that_took_atmos_does_not_pass_it_on():
    labelled, plain = _rows(LABELLED, PLAIN)
    assert _took(plain)
    # A later pass sees only rows that took it: nothing new is marked.
    other = {
        "title": _plain_variant("-NTb", "-FLUX"),
        "link": "http://hydra/9",
        "size": "1",
    }
    later = [plain, other]
    filter_module.attach_metadata(later)
    assert not _took(other)
    assert _took(plain)
    assert labelled["_meta"]["audio"].count("Atmos") == 1


def test_a_second_pass_changes_nothing():
    rows = _rows(LABELLED, PLAIN, PLAIN.replace(".", " "))
    before = [dict(row["_meta"]) for row in rows]
    assert share_atmos(rows) == 0
    filter_module.attach_metadata(rows)
    assert [row["_meta"] for row in rows] == before
    assert rows[1]["_meta"]["audio"].count("Atmos") == 1


def test_per_row_caches_built_from_the_old_audio_are_dropped():
    from resources.lib.release_match import release_profile

    rows = [
        {"title": LABELLED, "link": "a"},
        {"title": PLAIN, "link": "b"},
    ]
    for row in rows:
        row["_meta"] = filter_module.parse_title_metadata(row["title"])
    stale = release_profile(rows[1])
    assert ("audio", ("dd+",)) in stale

    assert share_atmos(rows, filter_module._META_DERIVED_ROW_KEYS) == 1
    assert ("audio", ("atmos", "dd+")) in release_profile(rows[1])


def test_two_listings_of_one_release_still_group_as_copies():
    from resources.lib.release_match import same_release

    rows = _rows(LABELLED, PLAIN, PLAIN.replace(".", " "))
    assert same_release(rows[1], rows[2])
    assert not same_release(rows[0], rows[1])


def test_the_stored_parse_keeps_what_was_taken(tmp_path):
    from unittest.mock import patch

    from resources.lib import cache

    def ttl(key, default=""):
        return "1440" if key == "cache_ttl_minutes" else default

    rows = _rows(LABELLED, PLAIN)
    with patch("resources.lib.cache._get_cache_dir", return_value=str(tmp_path)):
        cache.set_cached("episode", "Some Show", rows, settings_getter=ttl)
        cached = cache.get_cached("episode", "Some Show", settings_getter=ttl)
    assert [row["_meta"] for row in cached] == [row["_meta"] for row in rows]
    assert _took(cached[1])
    filter_module.attach_metadata(cached)
    assert cached[1]["_meta"]["audio"].count("Atmos") == 1


def test_rows_without_usable_metadata_are_skipped():
    rows = [
        None,
        {"title": "x"},
        {"title": "y", "_meta": "junk"},
        {"title": "z", "_meta": {"audio": "DD+"}},
        {"title": LABELLED, "_meta": {"audio": ["DD+", "Atmos"]}},
    ]
    assert share_atmos(rows) == 0


def test_copies_tagged_with_the_same_language_share_it():
    labelled = LABELLED.replace("WEB-DL", "WEB-DL.German")
    plain = PLAIN.replace("WEB-DL", "WEB-DL.German")
    rows = _rows(labelled, plain, PLAIN)
    assert rows[0]["_meta"]["languages"] == rows[1]["_meta"]["languages"] != []
    assert _took(rows[1])
    # The untagged copy has no labelled copy in its own language.
    assert not _took(rows[2])
