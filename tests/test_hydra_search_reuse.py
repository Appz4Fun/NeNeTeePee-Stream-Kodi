"""Selection reuses the original Hydra response without more indexer searches."""

import json
from io import BytesIO
from unittest.mock import patch

import pytest
from resources.lib import hydra
from resources.lib.router_fallback import _fetch_fallback_extra_uploads


def _settings(key, default=""):
    return {
        "hydra_url": "http://hydra:5076",
        "hydra_api_key": "test-key",
        "nzbhydra_enabled": "true",
        "max_results": "2500",
    }.get(key, default)


def _response(peer="peer"):
    return (
        "<rss><channel><item><title>Moonlight.2016.REMUX-GRP</title>"
        "<link>http://hydra/pick</link></item>"
        "<item><title>Moonlight.2016.REMUX-GRP</title>"
        "<link>http://hydra/{}</link><pubDate>Mon, 28 Sep 2026 12:00:00 +0000</pubDate>"
        '<enclosure length="12345"/></item>'
        "<item><title>Other.2016.REMUX-GRP</title>"
        "<link>http://hydra/other</link></item></channel></rss>"
    ).format(peer)


def _search(response):
    caps = {
        "nzbhydra2": {
            "base_url": "http://hydra:5076",
            "caps": {
                "search_types": ["movie"],
                "supported_params": {"movie": ["imdbid", "q"]},
            },
        }
    }
    with patch.object(hydra, "load_provider_caps", return_value=caps), patch.object(
        hydra, "_http_get", return_value=response
    ) as request:
        rows, error = hydra.search_hydra(
            "movie", "Moonlight", imdb="tt4975722", settings_getter=_settings
        )
    assert error is None
    request.assert_called_once()
    return rows


def test_selection_reuses_initial_response_for_both_backup_consumers():
    rows = _search(_response())
    selected = dict(rows[0])  # Picker/filter paths may copy result dicts.
    rows[1]["link"] = "http://hydra/mutated"
    with patch(
        "urllib.request.urlopen",
        side_effect=lambda *_a, **_kw: BytesIO(b'{"searchResults": []}'),
    ) as network:
        # NZBGet's uploads loader and the streaming fallback loader share this.
        first = _fetch_fallback_extra_uploads(selected, _settings)
        second = _fetch_fallback_extra_uploads(selected, _settings)
    assert [row["link"] for row in first] == ["http://hydra/peer"]
    assert second == first
    assert first[0]["size"] == "12345"
    assert first[0]["pubdate"] == "Mon, 28 Sep 2026 12:00:00 +0000"
    network.assert_not_called()


def test_search_snapshots_do_not_leak_between_searches_or_callers():
    first = _search(_response("first"))[0]
    second = _search(_response("second"))[0]
    with patch(
        "urllib.request.urlopen",
        side_effect=lambda *_a, **_kw: BytesIO(b'{"searchResults": []}'),
    ) as network:
        peers = hydra.fetch_release_duplicate_uploads(first, _settings)
        assert [row["link"] for row in peers] == ["http://hydra/first"]
        peers[0]["link"] = "http://hydra/mutated"
        assert [
            row["link"]
            for row in hydra.fetch_release_duplicate_uploads(first, _settings)
        ] == ["http://hydra/first"]
        assert [
            row["link"]
            for row in hydra.fetch_release_duplicate_uploads(second, _settings)
        ] == ["http://hydra/second"]
    network.assert_not_called()


@pytest.mark.parametrize("from_search", [False, True])
def test_missing_peers_never_trigger_another_search(from_search):
    selected = {"title": "Moonlight.2016.REMUX-GRP", "link": "http://hydra/pick"}
    if from_search:
        selected = _search(
            "<rss><channel><item><title>Moonlight.2016.REMUX-GRP</title>"
            "<link>http://hydra/pick</link></item></channel></rss>"
        )[0]
    with patch(
        "urllib.request.urlopen",
        side_effect=lambda *_a, **_kw: BytesIO(b'{"searchResults": []}'),
    ) as network:
        assert _fetch_fallback_extra_uploads(selected, _settings) == []
        assert _fetch_fallback_extra_uploads(selected, _settings) == []
    network.assert_not_called()


@pytest.mark.parametrize("provider", ["Prowlarr", "Direct indexer"])
@pytest.mark.parametrize("hydra_enabled", [True, False, None])
def test_other_provider_selection_retains_hydra_peers_after_filtering(
    provider, hydra_enabled
):
    from resources.lib.router import _collect_provider_outcomes

    hydra_rows = _search(_response())
    picked = {"title": "Moonlight.2016.REMUX-GRP", "link": "http://other/pick"}
    combined, error = _collect_provider_outcomes(
        [(provider, ([picked], None)), ("NZBHydra2", (hydra_rows, None))]
    )
    assert error is None
    # The picker can remove every Hydra row while preserving another provider.
    filtered = [dict(row) for row in combined if row["link"] == picked["link"]]

    # /play forces Hydra for the query even when its stored switch is off.
    def settings(key, default=""):
        if key == "nzbhydra_enabled":
            return "true" if hydra_enabled else "false"
        return _settings(key, default)

    getter = None if hydra_enabled is None else settings
    with patch("urllib.request.urlopen") as network:
        peers = _fetch_fallback_extra_uploads(filtered[0], getter)
    assert [row["link"] for row in peers] == ["http://hydra/pick", "http://hydra/peer"]
    network.assert_not_called()


def test_disk_cache_stays_linear_and_restores_shared_search_snapshots(tmp_path):
    from resources.lib import cache

    count = 200
    xml = "<rss><channel>{}</channel></rss>".format(
        "".join(
            "<item><title>Same.Release</title><link>http://hydra/{}</link></item>".format(
                i
            )
            for i in range(count)
        )
    )
    rows = _search(xml)
    plain_size = len(json.dumps(hydra.parse_results(xml)).encode("utf-8"))
    with patch.object(
        cache, "_get_cache_dir", return_value=str(tmp_path)
    ), patch.object(cache, "_get_cache_ttl_seconds", return_value=60):
        cache.set_cached("movie", "Same", rows)
        path = next(tmp_path.glob("*.json"))
        assert path.stat().st_size < plain_size * 4
        restored = cache.get_cached("movie", "Same")
    assert len(restored) == count
    with patch("urllib.request.urlopen") as network:
        assert len(hydra.fetch_release_duplicate_uploads(restored[0])) == count - 1
        assert len(hydra.fetch_release_duplicate_uploads(rows[0])) == count - 1
    # Loading a disk cache must not allocate a separate N-row group for each row.
    assert restored[0]["_hydra_search_uploads"] is restored[-1]["_hydra_search_uploads"]
    network.assert_not_called()


def test_disk_cache_keeps_hydra_peers_for_non_hydra_selection(tmp_path):
    from resources.lib import cache
    from resources.lib.router import _collect_provider_outcomes

    rows, error = _collect_provider_outcomes(
        [
            ("NZBHydra2", (_search(_response()), None)),
            (
                "Prowlarr",
                (
                    [
                        {
                            "title": "Moonlight.2016.REMUX-GRP",
                            "link": "http://other/pick",
                        }
                    ],
                    None,
                ),
            ),
        ]
    )
    assert error is None
    with patch.object(
        cache, "_get_cache_dir", return_value=str(tmp_path)
    ), patch.object(cache, "_get_cache_ttl_seconds", return_value=60):
        cache.set_cached("movie", "Moonlight", rows)
        restored = cache.get_cached("movie", "Moonlight")
    selected = next(row for row in restored if row["link"] == "http://other/pick")
    with patch("urllib.request.urlopen") as network:
        peers = _fetch_fallback_extra_uploads(dict(selected), _settings)
    assert [row["link"] for row in peers] == ["http://hydra/pick", "http://hydra/peer"]
    network.assert_not_called()
