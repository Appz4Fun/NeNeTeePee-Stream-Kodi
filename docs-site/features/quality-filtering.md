# Quality filtering and sorting

NeNeTeePee-Stream-Kodi filters the combined search results down to what you actually want, then
ranks what's left. Format filters live on **Quality filters**, followed by a separate
**Languages** tab. Group preferences, size, and keywords live on **Keyword filters**;
ranking lives on **Sorting**. Filters never lock you out: the picker can always
[show the releases they removed](#filtered-and-all-results-in-the-picker).

<!--
Screenshot placeholder. Capture the Quality filters settings tab showing the
resolution, HDR, audio, codec, and language toggle groups.
To add: save it as docs-site/images/quality-filters.png, then replace this
comment with:  ![Quality filters](../images/quality-filters.png)
-->

## Quality filters

Every quality toggle is **on by default**, which means "show everything." Turn
off the attributes you never want. NeNeTeePee-Stream-Kodi reads each release's attributes by
parsing its name.

| Group | Options |
|-------|---------|
| **Resolution** | 4320p / 8K, 2160p / 4K, 1440p / QHD, 1080p(i), 720p(i), 576p(i), 540p, 480p(i), 360p, 240p |
| **HDR** | HDR / HDR10, HDR10+, Dolby Vision, HLG, SDR |
| **Audio** | Atmos, TrueHD, DTS-HD MA, DTS-HD High Resolution, DTS:X, DTS, DD+ (EAC3), DD (AC3), AAC, FLAC, PCM / LPCM, Opus, MP3, ALAC, Vorbis, MP2, WMA, AC-4 |
| **Video Codec** | x265 / HEVC, x264 / AVC, AV1, VP9, MPEG-2, MPEG-4 ASP / Xvid / DivX, VC-1, MPEG-1, VP8, WMV, H.263, VVC (also matches H.266), MJPEG, Theora |
| **Languages** (own tab) | 48 languages, including Cantonese and Urdu; names and abbreviations are matched case-insensitively |

Each group also has **Other / Unknown**, enabled by default. It allows
releases whose attribute is missing or has no dedicated option. Turn it off to
require a recognized value in that group. It never overrides a known format
you've cleared. If you clear every known option in a group, known formats
are no longer restricted there; **Other / Unknown** still applies on its own.

There are four HDR types: Dolby Vision, HDR/HDR10, HDR10+ and HLG. A release
with none of their tags is **SDR**, so an explicit `SDR` tag changes nothing.
`HDR` selects HDR/HDR10; `HDR+`, `HDRP`, `HDRPlus`, `HDR10P`, and `HDR10+`
select HDR10+. Dolby Vision requires `DV`, `DoVi`, or `Dolby Vision`. Multiple
explicit HDR tags are retained, and a release passes when any selected format
matches. These are filename attributes, not a scan of the actual video tracks.

Resolution aliases share their filters: `2560x1440` is 1440p, `8K` and
`7680x4320` are 4320p, and 1080i/720i use the 1080p(i)/720p(i) selections.

Language abbreviations such as `fr` select French, and Latino/Latin American
Spanish select Spanish. A multilingual release passes when any selected
language matches. Cantonese and Urdu have their own toggles. If **Chinese** is
on, releases tagged Cantonese or Urdu also pass.

## Keyword and group filters

On the **Keyword filters** tab:

| Setting | Effect |
|---------|--------|
| **Exclude keywords** | Comma-separated. A release is removed if any keyword appears anywhere in its title. |
| **Required keywords** | Comma-separated. A release is removed unless every keyword appears in its title. |
| **Min size** / **Max size** | In MB, `0` = no limit. A release outside the range is removed. If a release's size can't be read, it's treated as 0 MB, so a non-zero minimum removes size-less placeholder rows. If you set a maximum below the minimum, the add-on turns the size filter off and logs a warning. |
| **Preferred groups: Tier 1 / 2 / 3** | Comma-separated preferred groups, ranked in that order under Relevance. Defaults come from the TRaSH remux tiers. |
| **Configure Excluded Groups...** | Opens a multi-select of 94 known release groups. Checked groups are **removed**. Empty means no exclusions. |

!!! warning "Preferred and excluded groups behave differently"
    - **Excluded groups** are a hard filter that removes matching releases.
    - **Preferred groups** are **not** a filter. They only **boost ranking**
      under the Relevance sort. Choosing a preferred group never hides other
      groups. If you want only certain groups, use required keywords or
      excluded groups instead.

<!--
Screenshot placeholder. Capture the preferred tiers and Configure Excluded
Groups control.
To add: save it as docs-site/images/configure-groups.png, then replace this
comment with:  ![Configure groups dialog](../images/configure-groups.png)
-->

## Sorting and selection

On the **Sorting** tab:

| Setting | Options | Default |
|---------|---------|---------|
| **Sort by** | Relevance, Size (largest first), Size (smallest first), Age (newest first), Age (oldest first) | Relevance |
| **Results view** | List compacted, List | List compacted |
| **Max results** | Whole number, clamped to 1–10000 when sent to providers | 25 |
| **Auto-select best match (skip result list)** | Skip the picker and play the top-ranked result | Off |

### How relevance ranking works

When you sort by **Relevance**, NeNeTeePee-Stream-Kodi ranks releases by this priority order:

1. **Resolution:** highest resolution first, from 8K down to 240p; unknown
   resolution last. A 2160p release always ranks higher than a 1080p release,
   regardless of HDR or REMUX.
2. **Pinned groups:** a CiNEPHiLES release ranks first in its resolution and
   a FraMeSToR release second, ahead of HDR and everything below. These two
   are fixed, not a setting, and match without case sensitivity.
3. **Release group:** a release with a group ranks above one without, so a
   release with no group ranks last within its resolution.
4. **HDR:** Dolby Vision, HDR10+, HDR/HDR10, HLG, then SDR (no HDR tag). A
   release with several tags ranks by its best.
5. **Release type:** REMUX releases tagged HYBRID first, then other REMUX
   releases, then everything else.
6. **Preferred group:** **Tier 1**, then **Tier 2**, then **Tier 3**, then
   groups in no tier. Group names match exactly, without case sensitivity.
7. **Audio:** four tiers, ranked by the release's best track:
    1. lossless: TrueHD (with or without Atmos), DTS-HD MA (with or without
       DTS:X), FLAC, PCM and ALAC;
    2. lossy with object audio: Dolby Digital Plus with Atmos, and lossy
       DTS:X;
    3. lossy surround: Dolby Digital Plus, Dolby Digital, DTS and DTS-HD High
       Resolution;
    4. everything else, such as AAC, Opus and MP3.

    A release with no audio tag ranks after all four. Formats in one tier
    tie, so FLAC ranks beside TrueHD Atmos.
8. **NZB copies:** among releases still tied, the release with more NZBs in
   the results sorts first, so a pick has more backups if one NZB fails.
   Copies are counted with the [same-release rule](nzbget-backend.md#what-counts-as-the-same-release)
   that NZBGet duplicate backups use, so a release with no group counts only
   itself.
9. **Size:** larger files win the final tie-break.

#### How audio tags are read

Atmos and DTS:X are object audio carried on top of another track. Atmos rides
on TrueHD (lossless) or on Dolby Digital Plus (lossy), so a name with `DD` and
`Atmos` but no TrueHD is read as Dolby Digital Plus with Atmos, as is `DDPA5.1`.
DTS:X on a disc sits on DTS-HD MA and counts as lossless; a WEB release that
tags DTS:X without DTS-HD MA carries the lossy kind. `DD_5.1`, or `DD 5.1` in
a name that otherwise separates its words with dots, is read as `DD+5.1`: the
plus sign was replaced on the way from the indexer.

The three editable, comma-separated preferred group lists default to the
[TRaSH tier 1](https://github.com/TRaSH-Guides/Guides/blob/master/docs/json/radarr/cf/remux-tier-01.json),
[tier 2](https://github.com/TRaSH-Guides/Guides/blob/master/docs/json/radarr/cf/remux-tier-02.json), and
[tier 3](https://github.com/TRaSH-Guides/Guides/blob/master/docs/json/radarr/cf/remux-tier-03.json)
definitions retrieved on September 5, 2026. NeNeTeePee-Stream-Kodi bundles their published
group expressions locally, so a search doesn't fetch the guides. Clearing a tier leaves
it empty. Group preferences affect ranking, never whether a release is kept.
The Size and Age modes sort only by that property. Age uses the release's post
date; a missing or unreadable date sorts as the oldest, and an unreadable size
as 0.

### About "Max results"

**Max results** applies in two places: it caps how many results each provider is
asked for, and it truncates the filtered list you see. The picker's show-all
view (below) is not truncated. With **Auto-select best match** on, NeNeTeePee-Stream-Kodi
plays the first release that passed your filters after ranking, so "best"
always means "the top item under your current sort order." If nothing passed
your filters, the picker opens instead.

## Picker views

**Sorting › Results view** picks how the picker lays out results:

- **List compacted** (default) shows each release once, however many indexers
  carry it, with its copy count on the right of the row. A panel on the right
  lists the focused release's copies with indexer, size and age. A green
  **DL** on the row means a copy is already downloaded. Press ++enter++ to
  play the downloaded copy if there is one, otherwise the top-ranked copy.
- **List** shows every indexer copy on its own row, as earlier versions did.

Copies are grouped with the [same-release rule](nzbget-backend.md#what-counts-as-the-same-release):
the same title and year or episode, and equal resolution, HDR, audio,
channels, codec, quality (source), edition, PROPER/REPACK, HYBRID, 3D, dubbed,
hardcoded-subtitle and cut tags, and release group. Language and soft-subtitle tags don't split a release, and a
result with no release group is never grouped with another.

### How a release is named in the picker

Indexers list the same release as a dotted name, a spaced one, or a raw Usenet
subject such as `[1/3] - "Name.mkv" yEnc 4400443301`. The picker shows every
row in one format, built from what the name itself says:

```text
Dune Part Two (2024) [Remux-2160p][HYBRID][DV HDR10+][TrueHD Atmos 7.1][HEVC]-CiNEPHiLES
Six Feet Under (2001) - S02E01 [WEB-DL-1080p][DD+ 5.1][H264]-FLUX
```

That is the title and year, the episode for TV, then source and resolution,
HYBRID, HDR, the main audio track with its channels, MULTi or dubbed
languages, the video codec, and the release group. A part the name doesn't
state is left out. File counters, `yEnc`, sizes, file extensions and posting
wrappers are never shown and never affect grouping. In **List compacted**, the
side panel shows the release name as the indexer listed it in grey under the
normalized one, so you can check how it was read. A name with no readable
title, source or resolution is shown as listed.

**HYBRID** has its own cyan slot on each row's second line, between the source
and the container. A hybrid release combines sources, such as one disc's video
with another's audio or Dolby Vision added to an HDR10+ encode. Only a
`Hybrid` tag after the year, episode or resolution counts; a title such as
*Hybrid (2007)* isn't one.

## Filtered and all results in the picker

The picker opens on the filtered list, and its header reads *Showing N of M
sources after filters*. To see everything the search returned:

- Press **C**, or use Kodi's context-menu button (long-press on most
  remotes), to switch to all results. The header changes to *Showing all N
  sources (filters off)*. Press it again to return to the filtered view.
- On CoreELEC/Linux devices where NeNeTeePee-Stream-Kodi can read the remote's input device,
  you can instead **hold OK for five seconds** to turn filtering off. A short
  press still selects a result. Releasing after the five-second hold doesn't
  select or download anything, and holding again keeps filtering off.

In the all-results view, every release your filters would remove carries a
yellow **FILTERED:** chip naming the first filter that rejected it:
`resolution`, `HDR`, `audio`, `codec`, `language`, `keyword`, `group`, or
`size`, checked in that order. You can pick one of these rows and it plays
normally. Nothing here changes your saved filter settings, and the footer
always shows the shortcuts available.

If no release passes your filters, the picker opens straight into the
all-results view, so you can still choose something.

For the exact ranking math and filter internals, see
[How it works → Search pipeline](../how-it-works/search-pipeline.md).
