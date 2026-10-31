# Security policy

## Reporting a vulnerability

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do not** open a public issue
2. Email the maintainer or use [GitHub's private vulnerability reporting](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/security/advisories/new)
3. Include steps to reproduce and any relevant details

The maintainer responds within 72 hours and works to release a fix promptly.

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Older releases | No |

## Scope

This addon runs locally on your Kodi device and communicates only with services you configure: your search provider (NZBHydra2, Prowlarr, or direct Newznab indexers) and your backend (nzbdav, InfiniDysk, or NZBGet). Security concerns include:

- API key handling and storage
- WebDAV, NZBGet, and SMB (Windows/Samba file sharing) credential management
- URL construction and validation
- XML parsing of search-provider and WebDAV responses (see `resources/lib/xml_safety.py`)

## Security invariants

Follow these rules when you write or review code in this repository. Automated security scanners also read this section as policy context.

### Parse XML only through `safe_fromstring`

Parse XML with `safe_fromstring` from `repo/plugin.video.nzbdav/resources/lib/xml_safety.py`. Don't call `xml.etree.ElementTree` parsers directly or customize a parser.

Search-provider, WebDAV, and NZB responses are untrusted, so ad-hoc parsing can expose the add-on to XML External Entity (XXE) and Billion Laughs attacks. Python's C implementation of `ElementTree` has no `.parser` attribute, so setting `parser.parser.ExternalEntityRefHandler` raises `AttributeError`. Code that catches that error keeps parsing without protection. `safe_fromstring` uses `defusedxml` when it's available and otherwise refuses entity declarations.

### Fail closed when a secret is missing

Check that a secret exists and return `False` if it doesn't, then compare it with `hmac.compare_digest`.

Don't write `if expected and not compare_digest(...)`. An empty secret skips the comparison and authorizes the request. This pattern affected `_prepare_token_ok`, which guards the proxy's `/prepare` and `/stream/<id>/fallbacks` routes on `127.0.0.1`. Production proxies always received a UUID token, so the gap was latent and not a live bypass.
