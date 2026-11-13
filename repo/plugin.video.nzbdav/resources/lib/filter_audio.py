# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio ranking tiers and the one-line audio label of a release.

Both read the audio formats ``filter_tags.detect_audio`` found in the name.
Atmos and DTS:X are object metadata on top of a "bed" track: Atmos rides on
TrueHD (lossless) or on Dolby Digital Plus (lossy), DTS:X on DTS-HD MA
(lossless, every disc) or on a lossy DTS stream (streaming services). So a
format's tier depends on the formats beside it.

Relevance ranks audio in four tiers, best first:

0. lossless: TrueHD (with or without Atmos), DTS-HD MA (with or without
   DTS:X), FLAC, PCM, ALAC;
1. lossy with objects: Dolby Digital Plus with Atmos, lossy DTS:X;
2. lossy surround: Dolby Digital Plus, Dolby Digital, DTS, DTS-HD HR;
3. everything else (AAC, Opus, MP3, ...).

A release with no audio tag ranks after all of them.
"""

AUDIO_TIER_UNKNOWN = 4

_LOSSLESS = frozenset(("TrueHD", "DTS-HD MA", "FLAC", "PCM", "ALAC"))
_LOSSY_SURROUND = frozenset(("DD+", "DD", "DTS", "DTS-HD HR"))
# The format a label leads with when a name tags several of one tier.
_LABEL_ORDER = (
    "TrueHD",
    "DTS:X",
    "DTS-HD MA",
    "FLAC",
    "PCM",
    "ALAC",
    "DD+",
    "DTS-HD HR",
    "DD",
    "DTS",
    "AAC",
    "OPUS",
)
_LABEL_NAMES = {"OPUS": "Opus"}
_CHANNEL_NAMES = {"stereo": "2.0", "mono": "1.0"}


def _is_web(quality):
    return "web" in str(quality or "").lower()


def _format_tier(name, names, web):
    """The tier of one format, given the formats tagged beside it."""
    if name in _LOSSLESS:
        return 0
    if name == "DTS:X":
        # DTS:X on a disc sits on DTS-HD MA. Only a streaming release
        # carries the lossy kind, unless it names the lossless bed itself.
        return 1 if web and "DTS-HD MA" not in names else 0
    if name == "Atmos":
        return 0 if "TrueHD" in names else 1
    if name in _LOSSY_SURROUND:
        return 2
    return 3


def audio_tier(audio, quality=""):
    """The best (lowest) tier among ``audio``; see the module docstring.

    ``quality`` is the parsed source ("WEB-DL", "BluRay REMUX", ...). It
    only decides whether a bare DTS:X tag is the lossy streaming kind.
    """
    if isinstance(audio, str):
        audio = [audio]
    names = [str(name) for name in audio or [] if name]
    if not names:
        return AUDIO_TIER_UNKNOWN
    web = _is_web(quality)
    return min(_format_tier(name, names, web) for name in names)


def _label_rank(name, names, web):
    order = _LABEL_ORDER.index(name) if name in _LABEL_ORDER else len(_LABEL_ORDER)
    return (_format_tier(name, names, web), order)


def _channel_text(channels):
    if isinstance(channels, (list, tuple)):
        channels = channels[0] if channels else ""
    text = str(channels or "").strip()
    return _CHANNEL_NAMES.get(text.lower(), text)


def audio_label(audio, channels="", quality=""):
    """One label for a release's main track: "TrueHD Atmos 7.1", "DD+ 5.1".

    The main track is the best-tier format; Atmos is appended to the TrueHD
    or Dolby Digital Plus track that carries it, and the channel count goes
    last. A name tagging only "Atmos" is shown as "Atmos". Empty when the
    name tags no audio.
    """
    if isinstance(audio, str):
        audio = [audio]
    names = [str(name) for name in audio or [] if name]
    if not names:
        return ""
    web = _is_web(quality)
    beds = [name for name in names if name != "Atmos"]
    parts = []
    if beds:
        main = min(beds, key=lambda name: _label_rank(name, names, web))
        parts.append(_LABEL_NAMES.get(main, main))
        if "Atmos" in names and main in ("TrueHD", "DD+", "DD"):
            parts.append("Atmos")
    else:
        parts.append("Atmos")
    channel_text = _channel_text(channels)
    if channel_text:
        parts.append(channel_text)
    return " ".join(parts)
