# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors
"""A fast read of an NZB's article Message-IDs, for plainly written NZBs.

``nzbget_fleet_dedup.posting_fingerprint_file`` walks an NZB with the XML
parser to hash every article's Message-ID. For a large release that is a 10 MB
document with 130,000 ``<segment>`` elements, and the per-element Python work
takes about 1.5 seconds on a CoreELEC box; four being fingerprinted at once
take about 4 seconds each, because that work cannot run in parallel. It was
the slowest part of collecting a release's NZBs.

This module produces the same fingerprint with one regular-expression pass
over the bytes, about five times faster, for an NZB that is written the plain
way every indexer writes them: an optional XML declaration and DOCTYPE, then
``<nzb>`` holding ``<head>`` and ``<file>`` elements, each file holding
``<groups>`` and ``<segments>``, each segment holding its Message-ID as text.

Comments are skipped, as the parser skips them: newznab writes one into
nearly every NZB it serves.

It never decides on anything unusual. CDATA, processing instructions, entity
declarations, namespace prefixes, an element outside the NZB vocabulary,
unexpected nesting, an attribute it cannot read, an encoding other than UTF-8,
non-ASCII or unrecognized-reference Message-ID text, a segment it cannot read
whole, or a document that does not end cleanly all return ``UNUSUAL``, and the
caller falls back to the XML parser.

It is not a validator either. It only says where the Message-IDs are in a
document shaped like an NZB; whether the document is well-formed XML is for
the caller to ask the safe XML reader (``xml_safety.check_well_formed``)
before it accepts the result. No entity is ever expanded here: the five
predefined ones and printable-ASCII character references are replaced by
table, nothing else.
"""

import operator
import re
import zlib
from array import array

# Returned for a document the fast read does not handle: use the XML parser.
UNUSUAL = object()

_CHUNK_BYTES = 1 << 20
# The declaration and DOCTYPE are judged on at least this much of the start.
_PROLOG_BYTES = 4096

# The start of a plain NZB: BOM, XML declaration and a DOCTYPE with no
# internal subset are optional; the first element must be <nzb>.
_PROLOG = re.compile(
    rb"\A(?:\xef\xbb\xbf)?\s*(?:<\?xml\b([^<>]*)\?>)?\s*"
    rb"(?:<!DOCTYPE\b[^<>\[\]]*>)?\s*(?=<nzb[\s>])"
)
_ENCODING = re.compile(rb"""encoding\s*=\s*["']([^"']*)["']""")
_PLAIN_ENCODINGS = frozenset((b"utf-8", b"utf8", b"us-ascii", b"ascii"))

# Attributes as every NZB writes them: name="value", with no "<" or ">"
# inside the quotes. A tag written any other way is not read here.
_ATTRS = rb"""(?:\s+[A-Za-z_:][\w:.-]*\s*=\s*(?:"[^"<>]*"|'[^'<>]*'))*\s*"""
# Between <segments> blocks: any tag of the NZB vocabulary.
_TAG = re.compile(
    rb"<(/?)(nzb|head|meta|file|groups|group|segments|segment)" + _ATTRS + rb"(/?)>"
)
# Inside a block: a whole segment whose text is plain ASCII with no
# reference, which is every Message-ID in practice; and any whole segment.
_PLAIN_SEGMENT = re.compile(rb"<segment" + _ATTRS + rb">([^<&\x80-\xff]*)</segment\s*>")
_ANY_SEGMENT = re.compile(rb"<segment" + _ATTRS + rb">([^<]*)</segment\s*>")
# Where each element may open: its parent in the stack of open elements.
_PARENT = {
    b"nzb": None,
    b"head": b"nzb",
    b"meta": b"head",
    b"file": b"nzb",
    b"groups": b"file",
    b"group": b"groups",
    b"segments": b"file",
}
_IN_BLOCK = [b"nzb", b"file", b"segments"]


def scan_fingerprint(stream):
    """The CRC32 of every article Message-ID in ``stream``, or ``UNUSUAL``.

    ``stream`` is a binary file object positioned at the start. The result is
    the ``array('I')`` the XML walk would build, in document order. Memory is
    one chunk plus the unfinished element carried over, never the document.
    """
    hashes = array("I")
    stack = []
    # Enough of the start to hold any declaration and DOCTYPE.
    tail = b""
    while len(tail) < _PROLOG_BYTES:
        chunk = stream.read(_CHUNK_BYTES)
        if not chunk:
            break
        tail += chunk
    tail = _after_prolog(tail)
    if tail is None:
        return UNUSUAL
    closed = False
    while True:
        cut = _complete_upto(tail)
        if closed and tail[:cut].strip():
            return UNUSUAL  # something follows </nzb>
        if not _scan(tail[:cut], hashes, stack):
            return UNUSUAL
        closed = closed or (cut > 0 and not stack)
        tail = tail[cut:]
        chunk = stream.read(_CHUNK_BYTES)
        if not chunk:
            break
        tail += chunk
    # Only white space and comments may be left, with every element closed.
    rest = _without_comments(tail)
    if rest is None or rest.strip() or stack or not closed or not hashes:
        return UNUSUAL
    return hashes


def _after_prolog(head):
    """``head`` from its ``<nzb`` on, comments removed; None if not plain."""
    head = _without_comments(head)
    if head is None:
        return None
    match = _PROLOG.match(head)
    if match is None:
        return None
    encoding = _ENCODING.search(match.group(1) or b"")
    if encoding and encoding.group(1).strip().lower() not in _PLAIN_ENCODINGS:
        return None
    return head[match.end() :]


def _complete_upto(buffer):
    """The offset after the last closing tag in ``buffer``.

    Everything before it is made of whole elements, so a tag or a segment's
    text split across two chunks is carried over and read in one piece. A
    comment still waiting for its end is carried over too, and a closing tag
    written inside a comment is not a boundary.
    """
    limit = _unfinished_comment(buffer, len(buffer))
    cut = 0
    for closing in _CLOSING_TAGS:
        found = buffer.rfind(closing, 0, limit)
        if found >= 0:
            cut = max(cut, found + len(closing))
    return _unfinished_comment(buffer, cut)


def _unfinished_comment(buffer, end):
    """Where a comment still open at ``end`` starts, or ``end`` if none is."""
    opened = buffer.rfind(b"<!--", 0, end)
    if opened < 0 or buffer.find(b"-->", opened + 4, end) >= 0:
        return end
    return opened


_CLOSING_TAGS = (b"</segment>", b"</segments>", b"</file>", b"</head>", b"</nzb>")


def _scan(data, hashes, stack):
    """Hash ``data``'s segments into ``hashes``; False for anything unusual.

    ``data`` is made of whole elements. It is read as alternating stretches:
    the inside of a ``<segments>`` block, where nearly all of an NZB's bytes
    are and which is hashed in bulk, and everything between blocks, a few
    tags that are checked one by one.
    """
    data = _without_comments(data)
    if data is None:
        return False
    position = 0
    size = len(data)
    while position < size:
        if stack == _IN_BLOCK:
            closing = data.find(b"</segments", position)
            end = size if closing < 0 else closing
            if not _hash_block(data, position, end, hashes):
                return False
            position = end
            if closing < 0:
                break
        opening = data.find(b"<segments", position)
        if opening < 0:
            end = size
        else:
            end = data.find(b">", opening) + 1
            if end <= 0:
                return False
        if not _outer(data[position:end], stack):
            return False
        position = end
    return True


def _outer(region, stack):
    """Apply the tags between two segment blocks to ``stack``.

    Every "<" in the stretch must start a tag that was read, so a comment
    with no end, a CDATA section, a processing instruction, an element
    outside the vocabulary or a tag written unusually stops the read.
    """
    tags = 0
    for match in _TAG.finditer(region):
        tags += 1
        if match.group(2) == b"segment" or not _apply(match, stack):
            return False
    return tags == region.count(b"<")


def _apply(match, stack):
    """Open or close the matched element on ``stack``; False if it cannot."""
    name = match.group(2)
    if match.group(3):
        return False  # self-closing
    if match.group(1):  # a closing tag
        if not stack or stack[-1] != name:
            return False
        stack.pop()
        return True
    if (stack[-1] if stack else None) != _PARENT[name]:
        return False
    stack.append(name)
    return True


def _hash_block(data, start, end, hashes):
    """Hash the segments in ``data[start:end]``, the inside of a block.

    False unless the stretch holds whole segments and nothing else: every
    "<" in it must belong to a segment that was read.
    """
    opened = data.count(b"<", start, end)
    texts = _PLAIN_SEGMENT.findall(data, start, end)
    if opened == 2 * len(texts):
        # No Python-level work per article: each step runs over the whole
        # list inside the interpreter.
        ids = map(bytes.lower, map(_STRIP_ANGLES, map(bytes.strip, texts)))
        hashes.extend(map(zlib.crc32, filter(None, ids)))
        return True
    # A segment with a reference or non-ASCII text, or something that is not
    # a segment: read each one with care.
    texts = _ANY_SEGMENT.findall(data, start, end)
    if opened != 2 * len(texts):
        return False
    for text in texts:
        msgid = _message_id(text)
        if msgid is None:
            return False
        if msgid:
            hashes.append(zlib.crc32(msgid))
    return True


_STRIP_ANGLES = operator.methodcaller("strip", b"<>")


_COMMENT = re.compile(rb"<!--(.*?)-->", re.DOTALL)


def _without_comments(data):
    """``data`` with its comments removed, or None for one XML forbids.

    A comment may not contain "--" or end in "--->"; the parser refuses such
    a document, so the fast read leaves it to the parser. A comment with no
    end is left in place and stops the read as not plain.
    """
    if b"<!--" not in data:
        return data
    for match in _COMMENT.finditer(data):
        body = match.group(1)
        if b"--" in body or body.endswith(b"-"):
            return None
    return _COMMENT.sub(b"", data)


def _message_id(text):
    """A segment's Message-ID as the XML walk normalizes it, or None.

    Stripped, without its angle brackets, lower-cased. None for text the fast
    read does not handle: non-ASCII, or a reference other than the five
    predefined entities and printable-ASCII character references.
    """
    if not text.isascii():
        return None
    if b"&" in text:
        if _UNKNOWN_REFERENCE.search(text):
            return None
        try:
            text = _REFERENCE.sub(_replace_reference, text)
        except ValueError:
            return None
    return text.strip().strip(b"<>").lower()


_REFERENCE = re.compile(rb"&(#[xX][0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);")
# An ampersand that does not start one of the forms above.
_UNKNOWN_REFERENCE = re.compile(
    rb"&(?!(?:#[xX][0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);)"
)
_NAMED = {b"amp": b"&", b"lt": b"<", b"gt": b">", b"quot": b'"', b"apos": b"'"}


def _replace_reference(match):
    """The character a reference stands for; ValueError outside plain ASCII."""
    body = match.group(1)
    if body in _NAMED:
        return _NAMED[body]
    code = int(body[2:], 16) if body[1:2] in (b"x", b"X") else int(body[1:])
    if not 0x20 <= code < 0x7F:
        raise ValueError("not printable ASCII")
    return bytes((code,))
