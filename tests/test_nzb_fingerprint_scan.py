"""The fast NZB read gives the XML walk's fingerprint, or steps aside."""

import io
from unittest.mock import patch

import pytest
from resources.lib import nzb_fingerprint_scan as scan
from resources.lib import nzbget_fleet_dedup as dedup

_DOCTYPE = (
    b'<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" '
    b'"http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">\n'
)


def _file(ids, subject="movie.mkv"):
    segments = "".join(
        '<segment bytes="716800" number="{}">{}</segment>\n'.format(n + 1, msgid)
        for n, msgid in enumerate(ids)
    )
    return (
        '<file poster="p &lt;p@x&gt;" date="1700000000" subject="{}">\n'
        "<groups><group>alt.binaries.test</group></groups>\n"
        "<segments>\n{}</segments>\n</file>\n".format(subject, segments)
    )


_PROLOG = b'<?xml version="1.0" encoding="UTF-8"?>\n' + _DOCTYPE


def _nzb(files, prolog=_PROLOG, head=""):
    body = '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">\n{}{}</nzb>\n'.format(
        head, "".join(files)
    )
    return prolog + body.encode("utf-8")


def _ids(prefix, count):
    return ["{}{:06d}@news.example".format(prefix, n) for n in range(count)]


def _walk(data):
    """The XML parser's fingerprint, with the fast read switched off."""
    with patch.object(dedup, "_fast_fingerprint", return_value=scan.UNUSUAL):
        return dedup.posting_fingerprint(data)


def _fast(data):
    return scan.scan_fingerprint(io.BytesIO(data))


PLAIN = {
    "one file": _nzb([_file(_ids("a", 40))]),
    "several files": _nzb([_file(_ids("a", 30)), _file(_ids("b", 25), "x.par2")]),
    "no declaration": _nzb([_file(_ids("a", 12))], prolog=b""),
    "declaration only": _nzb([_file(_ids("a", 12))], prolog=b'<?xml version="1.0"?>'),
    "bom": _nzb([_file(_ids("a", 12))], prolog=b"\xef\xbb\xbf<?xml version='1.0'?>"),
    "ascii declared": _nzb(
        [_file(_ids("a", 12))], prolog=b'<?xml version="1.0" encoding="us-ascii"?>'
    ),
    "head and meta": _nzb(
        [_file(_ids("a", 12))],
        head=(
            '<head><meta type="password">p&amp;w</meta>'
            '<meta type="x">y</meta></head>\n'
        ),
    ),
    "angle brackets and case": _nzb(
        [_file(["&lt;Part1Of2.ABC@News.Example&gt;", "  spaced@x  ", "MiXeD@Y"])]
    ),
    "entities": _nzb([_file(["a&amp;b@x", "c&#38;d@x", "e&#x26;f@x", "g&quot;h@x"])]),
    "empty segment": _nzb([_file(["a@x", "", "   ", "b@x"])]),
    "crlf": _nzb([_file(_ids("a", 9))]).replace(b"\n", b"\r\n"),
    # newznab's footer, a disclaimer at the top, and a comment hiding markup.
    "newznab comment": _nzb([_file(_ids("a", 9))]).replace(
        b"</nzb>", b"<!-- newznab 2026-01-17 12:10:18 -->\n</nzb>"
    ),
    "comment before nzb": _nzb(
        [_file(_ids("a", 9))],
        prolog=b'<?xml version="1.0"?>\n<!-- Disclaimer - generated -->\n' + _DOCTYPE,
    ),
    "comment hiding a segment": _nzb([_file(_ids("a", 9))]).replace(
        b"<segments>", b"<segments><!--<segment>h@x</segment></segments>-->"
    ),
    "comment inside an id": _nzb([_file(["a<!-- c -->b@x", "c@x"])]),
    "one line": _nzb([_file(_ids("a", 9))]).replace(b"\n", b""),
}


@pytest.mark.parametrize("name", sorted(PLAIN))
def test_a_plain_nzb_reads_the_same_as_the_xml_walk(name):
    data = PLAIN[name]
    fast = _fast(data)
    assert fast is not scan.UNUSUAL
    assert list(fast) == list(_walk(data))
    # And the public functions return that same fingerprint.
    assert list(dedup.posting_fingerprint(data)) == list(fast)


def test_a_file_on_disk_reads_the_same_as_bytes_in_memory(tmp_path):
    data = _nzb([_file(_ids("a", 500)), _file(_ids("b", 500))])
    path = tmp_path / "x.nzb"
    path.write_bytes(data)
    assert list(dedup.posting_fingerprint_file(str(path))) == list(_walk(data))


@pytest.mark.parametrize("chunk", [1, 7, 64, 333, 4096])
def test_elements_split_across_chunks_are_read_whole(chunk):
    data = _nzb(
        [
            _file(_ids("a", 60)),
            _file(["x&amp;y@z", "&lt;Q@R&gt;"]),
            _file(_ids("c", 9)),
        ],
        head='<head><meta type="t">v</meta></head>',
    )
    with patch.object(scan, "_CHUNK_BYTES", chunk):
        fast = _fast(data)
    assert fast is not scan.UNUSUAL
    assert list(fast) == list(_walk(data))


def _swap(data, old, new):
    assert old in data
    return data.replace(old, new, 1)


_BASE = _nzb([_file(_ids("a", 6)), _file(_ids("b", 6))])

# Each of these is handed to the XML parser; the last column says whether the
# parser then accepts it (a fingerprint) or refuses it (None).
UNUSUAL = {
    "comment with two dashes": (
        _swap(_BASE, b"<segments>", b"<!-- a -- b --><segments>"),
        False,
    ),
    "comment with no end": (_swap(_BASE, b"<segments>", b"<!-- c <segments>"), False),
    "cdata": (_swap(_BASE, b"a000001@news.example", b"<![CDATA[a000001@x]]>"), True),
    "processing instruction": (_swap(_BASE, b"<file ", b"<?pi x?><file "), True),
    "namespace prefix": (
        _BASE.replace(b"<segment ", b"<n:segment ")
        .replace(b"</segment>", b"</n:segment>")
        .replace(b"<nzb ", b'<nzb xmlns:n="http://www.newzbin.com/DTD/2003/nzb" '),
        True,
    ),
    "unknown element": (_swap(_BASE, b"<segments>", b"<extra/><segments>"), True),
    "segments outside a file": (
        _swap(_BASE, b"<file ", b"<segments><segment>s@x</segment></segments><file "),
        True,
    ),
    "segment outside segments": (
        _swap(_BASE, b"<groups>", b"<segment>s@x</segment><groups>"),
        True,
    ),
    "file inside head": (
        _swap(
            _BASE,
            b"<file ",
            b"<head><file><segments><segment>h@x</segment></segments></file></head>"
            b"<file ",
        ),
        True,
    ),
    "segment with a child": (
        _swap(_BASE, b"a000001@news.example", b"a<group>g</group>"),
        True,
    ),
    "self-closing segments": (_swap(_BASE, b"<groups>", b"<segments/><groups>"), True),
    "non-ascii id": (_swap(_BASE, b"a000001@news", "aé@news".encode()), True),
    "latin-1 declared": (
        _swap(_BASE, b'encoding="UTF-8"', b'encoding="iso-8859-1"'),
        True,
    ),
    "numeric reference beyond ascii": (_swap(_BASE, b"a000001@", b"a&#233;@"), True),
    "entity declaration": (
        _swap(_BASE, _DOCTYPE, b'<!DOCTYPE nzb [<!ENTITY e "boom">]>'),
        False,
    ),
    "undeclared entity": (_swap(_BASE, b"a000001@", b"a&bogus;@"), False),
    "bare ampersand": (_swap(_BASE, b"a000001@", b"a&b@"), False),
    "truncated": (_BASE[: len(_BASE) // 2], False),
    "missing closing nzb": (_BASE.replace(b"</nzb>", b""), False),
    "unclosed file": (_swap(_BASE, b"</file>", b""), False),
    "html page": (b"<html><body>rate limited</body></html>", False),
    "html with segments": (
        b"<html><segments><segment>a@x</segment></segments></html>",
        False,
    ),
    "empty": (b"", False),
    "no segments": (_nzb([]), False),
}


@pytest.mark.parametrize("name", sorted(UNUSUAL))
def test_anything_unusual_is_left_to_the_xml_parser(name):
    data, parser_accepts = UNUSUAL[name]
    assert _fast(data) is scan.UNUSUAL
    walked = _walk(data)
    assert (walked is not None) == parser_accepts
    # The public function gives exactly what the parser decides.
    result = dedup.posting_fingerprint(data) if data else None
    assert (list(result) if result else None) == (list(walked) if walked else None)


def test_the_stream_is_rewound_for_the_xml_parser():
    data, _accepts = UNUSUAL["cdata"]
    stream = io.BytesIO(data)
    assert list(dedup.posting_fingerprint_file(stream)) == list(_walk(data))


def test_an_unreadable_path_is_no_fingerprint(tmp_path):
    assert dedup.posting_fingerprint_file(str(tmp_path / "missing.nzb")) is None


def test_no_entity_is_ever_expanded_by_the_fast_read():
    # A billion-laughs document is never scanned for segments at all.
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE nzb [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        b"<nzb><file><segments><segment>&b;@x</segment></segments></file></nzb>"
    )
    assert _fast(bomb) is scan.UNUSUAL
    assert dedup.posting_fingerprint(bomb) is None
