"""Hydra's internal API needs its XSRF token even with auth off."""

import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

from resources.lib import hydra


def _refused(token="tok-123"):
    headers = Message()
    headers["Set-Cookie"] = "HYDRA-XSRF-TOKEN={}; Path=/".format(token)
    return HTTPError("http://h/internalapi/search", 403, "", headers, io.BytesIO())


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def test_a_403_is_retried_once_with_the_xsrf_token():
    seen = []

    def _urlopen(request, timeout=30):
        seen.append(dict(request.header_items()))
        if len(seen) == 1:
            raise _refused()
        return _Response(json.dumps({"searchResults": [{"title": "T"}]}).encode())

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        assert hydra._fetch_hydra_internal_search("http://h", "T") == [{"title": "T"}]
    assert len(seen) == 2
    assert "X-xsrf-token" not in seen[0]
    assert seen[1]["X-xsrf-token"] == "tok-123"
    assert seen[1]["Cookie"] == "HYDRA-XSRF-TOKEN=tok-123"


def test_a_403_without_a_token_or_a_second_refusal_fails_soft():
    def _no_token(request, timeout=30):
        raise HTTPError("http://h", 403, "", Message(), io.BytesIO())

    with patch("urllib.request.urlopen", side_effect=_no_token) as opener:
        assert hydra._fetch_hydra_internal_search("http://h", "T") == []
    assert opener.call_count == 1

    twice = [_refused(), _refused()]
    with patch("urllib.request.urlopen", side_effect=twice) as opener:
        assert hydra._fetch_hydra_internal_search("http://h", "T") == []
    assert opener.call_count == 2


def test_other_http_errors_are_not_retried():
    error = HTTPError("http://h", 500, "", Message(), io.BytesIO())
    with patch("urllib.request.urlopen", side_effect=error) as opener:
        assert hydra._fetch_hydra_internal_search("http://h", "T") == []
    assert opener.call_count == 1
