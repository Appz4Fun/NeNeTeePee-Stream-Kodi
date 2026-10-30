## 2025-05-24 - Unify XXE protection with xml_safety.py

**Vulnerability:** inconsistent, incomplete, and fragile XXE (XML External Entity) and Billion Laughs DoS protection across various files (`webdav.py`, `webdav_discovery.py`, `kodi_advancedsettings.py`, `nzb_manifest.py`). The standard library fallback in Python >= 3.3 does not have `.parser` attribute for `ET.XMLParser()`, resulting in `AttributeError` that was caught but silently allowed the parser to continue with entities enabled (failing open).
**Learning:** Python's C-implementation of ElementTree doesn't expose external entity resolution handlers. You cannot turn off entities via `parser.parser.ExternalEntityRefHandler = lambda *_: False` because `parser` lacks a `.parser` attribute. `xml_safety.py` provides a centralized, robust text-scanning fallback for when `defusedxml` is not available.
**Prevention:** always use `safe_fromstring` from `xml_safety.py` for parsing XML instead of ad-hoc parser customizations.

## 2026-10-30 - Fail-Open Loopback Token Check

**Vulnerability:** `_prepare_token_ok` used `if expected_token and not compare_digest(...)`, so an empty server token skipped the comparison and authorized `/prepare` and `/stream/<id>/fallbacks`. Production proxies always get a UUID token, so this was a latent gap and not a live bypass.
**Learning:** A truthiness precondition combined with a negative comparison fails open when the secret is missing.
**Prevention:** Check for the secret first and return `False` when it is missing, then compare.
