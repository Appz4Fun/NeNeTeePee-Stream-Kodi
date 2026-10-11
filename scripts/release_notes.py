#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 nzbdav contributors

"""Print one version's CHANGELOG.md section, for the GitHub release body.

The Release workflow runs this before creating the release, so a tag whose
version has no (or an empty) ``## [<version>][]`` section fails early instead
of publishing a release with no curated notes.
"""

import argparse
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CHANGELOG = os.path.join(_ROOT, "CHANGELOG.md")
# The next version heading, or the link-reference block that ends the file.
_SECTION_END = re.compile(r"^(?:## \[|\[[^\]]+\]: )", re.MULTILINE)


def section(changelog_text, version):
    """The body of ``version``'s section (stripped); ``LookupError`` if none.

    A leading ``v`` (the tag form) is ignored.
    """
    if version.startswith("v"):
        version = version[1:]
    heading = re.compile(
        r"^## \[" + re.escape(version) + r"\]\[\][^\n]*\n", re.MULTILINE
    )
    match = heading.search(changelog_text)
    if match is None:
        raise LookupError("CHANGELOG.md has no section for {}".format(version))
    rest = changelog_text[match.end() :]
    end = _SECTION_END.search(rest)
    body = (rest[: end.start()] if end else rest).strip()
    if not body:
        raise LookupError("CHANGELOG.md section for {} is empty".format(version))
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="release version, with or without 'v'")
    parser.add_argument(
        "--changelog", default=_DEFAULT_CHANGELOG, help="path to CHANGELOG.md"
    )
    args = parser.parse_args(argv)
    with open(args.changelog, encoding="utf-8") as handle:
        text = handle.read()
    try:
        print(section(text, args.version))
    except LookupError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
