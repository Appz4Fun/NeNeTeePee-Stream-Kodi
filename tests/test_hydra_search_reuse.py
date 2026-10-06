"""Selection reuses the original Hydra response without more indexer searches."""

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
