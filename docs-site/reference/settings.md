# Settings reference

!!! note "Current source and next release"
    The unified **Playback backend** section and StreamNZB support are not in
    Stable 1.2.3 or Beta 2.0.0-beta.2. These instructions and illustrations
    describe current source builds and the next release.

    In released builds, configure nzbdav and WebDAV under **Connection**.
    Beta 2.0.0-beta.2 has a separate **NZBGet** section with an
    **Use NZBGet instead of nzbdav for playback** toggle. Stable 1.2.3 supports nzbdav only.
    StreamNZB requires a current source build until a release includes it.


Current released packages (Stable 1.2.3 and Beta 2.0.0-beta.2) use the Kodi menu name **NZB-DAV**. In those builds, choose that name wherever these instructions show **NeNeTeePee-Stream-Kodi**. Renamed source builds use the new name.

This page documents every setting in NeNeTeePee-Stream-Kodi, grouped by the tab it appears on
in Kodi's add-on settings (**My add-ons → Video add-ons → NeNeTeePee-Stream-Kodi → Configure**).

For each setting you'll find its label, its internal id (useful if you edit
`settings.xml` directly), its default, and what it does. Each tab also lists its actions, the buttons
that run a test or open a dialog.

!!! note "Defaults are chosen to be safe"
    Configure your selected **Playback backend** and, for nzbdav / InfiniDysk
    or NZBGet, at least one provider under **Indexers**. Other settings
    have working defaults. Change the **Advanced** tab in particular
    only when you have a specific reason.

See the [illustrated settings guide](../settings/index.md) for real Kodi screenshots, every individual option, and commit-pinned source footnotes.

## Playback backend

| Setting | Default | Purpose |
|---------|---------|---------|
| **Playback backend** | nzbdav / InfiniDysk | Select nzbdav / InfiniDysk, NZBGet, or StreamNZB. Existing NZBGet selections migrate automatically. |
| **StreamNZB server/base URL** | Empty | HTTP/HTTPS server reachable from Kodi. Visible with StreamNZB selected. |
| **StreamNZB stream token** | Empty | Masked stream token; never dashboard administrator credentials. Visible with StreamNZB selected. |

See [StreamNZB backend](../features/streamnzb-backend.md) for setup and limits.

Connection fields for all three backends appear on this tab. The tab shows only the
selected backend's fields. There are no separate Connection or NZBGet tabs.

### nzbdav / InfiniDysk

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| nzbdav URL | `nzbdav_url` | `http://localhost:3000` | Base URL of your nzbdav or InfiniDysk server. |
| API Key | `nzbdav_api_key` | *(empty)* | nzbdav API key, from **Settings → Usenet → API Key** in nzbdav. Stored hidden. |

**Action:** *Test nzbdav Connection* verifies the URL and API key.

### WebDAV

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| WebDAV URL (leave empty to use nzbdav URL) | `webdav_url` | `http://localhost:8080` | Base URL of the WebDAV server. **Clear it to reuse the nzbdav URL.** Keep a value only if WebDAV is on a separate address. |
| Username | `webdav_username` | *(empty)* | WebDAV username, from **Settings → WebDAV** in nzbdav. |
| Password | `webdav_password` | *(empty)* | WebDAV password. Stored hidden. |

**Action:** *Test WebDAV Connection* verifies WebDAV reachability and
credentials.

### NZBGet

An alternative backend to nzbdav. When you select it, NeNeTeePee-Stream-Kodi downloads through NZBGet
and plays from an SMB share or a local/mounted path. See [NZBGet backend](../features/nzbget-backend.md).
The URL, username, password, category, and completed-folder fields appear on
**Playback backend** when you select **NZBGet**.

!!! info "Beta feature"
    Added in 2.0.0-beta.1, available on the
    [Beta channel](../getting-started/beta-channel.md).

| Setting | id | Default | Description |
|---------|----|---------|-------------|
| Playback backend | `playback_backend` | `0` (nzbdav) | Select `1` for NZBGet or `2` for StreamNZB. Old `nzbget_enabled` choices migrate on first invocation. |
| NZBGet URL | `nzbget_url` | `http://localhost:6789` | NZBGet control address. |
| NZBGet Username | `nzbget_username` | `nzbget` | NZBGet control username. |
| NZBGet Password | `nzbget_password` | *(empty)* | NZBGet control password. Stored hidden. |
| NZBGet Category | `nzbget_category` | *(empty)* | Category to submit under; also used to locate the completed file. |
| Completed Folder (SMB or Local Path) | `nzbget_smb_root` | *(empty)* | `smb://` URL or local/mounted path of NZBGet's completed-downloads base. An [NFS hard mount](../features/nzbget-backend.md#recommended-mount-the-completed-folder-over-nfs) is recommended. |
| Maximum duplicate backups | `nzbget_max_backups` | `-1` | Same-release NZBs to send to NZBGet as duplicate backups. `-1` fills the fleet (up to 50 total members with `appendfleet`), `0` sends no backups, and a positive number limits backups. Each backup costs one indexer download. |

**Actions:** *Test NZBGet Connection*, *Test Completed Folder*.

## Indexers

NZBHydra2 is the first provider, followed by Prowlarr, then direct Newznab
indexers. These settings apply to nzbdav / InfiniDysk and NZBGet. StreamNZB
manages indexers on its own server and ignores these local settings.

### NZBHydra2

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Enable NZBHydra2 | `nzbhydra_enabled` | `false` | Use NZBHydra2 as a search provider. TMDBHelper playback honors this switch; NeNeTeePee-Stream-Kodi's own search menu and `plugin://` play URLs query NZBHydra2 even when it's off. |
| NZBHydra2 URL | `hydra_url` | `http://localhost:5076` | Base URL of your NZBHydra2 instance. |
| API Key | `hydra_api_key` | *(empty)* | NZBHydra2 API key. Stored hidden. |

**Action:** run *Test NZBHydra Connection* to verify the connection.

### Prowlarr

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Enable Prowlarr | `prowlarr_enabled` | `false` | Use Prowlarr as a search provider. NeNeTeePee-Stream-Kodi keeps only Usenet results. |
| Prowlarr URL | `prowlarr_host` | `http://localhost:9696` | Base URL of your Prowlarr instance. |
| Prowlarr API Key | `prowlarr_api_key` | *(empty)* | Prowlarr API key. Stored hidden. |
| Prowlarr Indexer IDs (comma-separated) | `prowlarr_indexer_ids` | *(empty)* | Indexer IDs to query. Required for Prowlarr search; a blank value returns no results. |

**Action:** *Test Prowlarr Connection* verifies the URL, key, and indexer
reachability.

### Direct Newznab indexers

Direct indexer fields and buttons stay greyed out until you enable direct
indexers.

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Enable direct Newznab indexers | `direct_indexers_enabled` | `false` | Master switch for direct indexers. |

**Popular indexers:** each has an *enable* toggle, an *API URL*, and an *API
key*. The URL defaults are the indexers' standard API endpoints:

| Indexer | Enable ID | URL ID (default) |
|---------|-----------|------------------|
| NZB.life / NZB.su | `direct_indexer_nzblife_enabled` | `direct_indexer_nzblife_url` (`https://api.nzb.su/api`) |
| NZBGeek | `direct_indexer_nzbgeek_enabled` | `direct_indexer_nzbgeek_url` (`https://api.nzbgeek.info/api`) |
| NZBFinder | `direct_indexer_nzbfinder_enabled` | `direct_indexer_nzbfinder_url` (`https://nzbfinder.ws/api`) |
| DrunkenSlug | `direct_indexer_drunkenslug_enabled` | `direct_indexer_drunkenslug_url` (`https://drunkenslug.com/api`) |
| NZBPlanet | `direct_indexer_nzbplanet_enabled` | `direct_indexer_nzbplanet_url` (`https://api.nzbplanet.net/api`) |
| DOGnzb | `direct_indexer_dognzb_enabled` | `direct_indexer_dognzb_url` (`https://api.dognzb.cr/api`) |

Each also has an API-key field (`direct_indexer_<name>_api_key`, stored hidden).

**Custom Newznab Indexers:** *Custom Indexer 1–3*, for any Newznab indexer
not in the preceding list. Each has an enable toggle (`direct_indexer_customN_enabled`),
*Indexer Name* (`direct_indexer_customN_name`), *API URL*
(`direct_indexer_customN_url`), and *API Key* (`direct_indexer_customN_api_key`,
stored hidden). All default to off/empty.

**Actions:**

- *Manage Indexers:* add from a 22-entry preset catalog or a custom URL;
  test, edit, turn on or off, and delete managed indexers; refresh NZBHydra2
  caps. Opening it copies complete rows from this tab into the managed list
  once; a managed entry then takes precedence over the tab row with the same id.
- *Test Direct Indexers:* fetches caps from every enabled indexer.

!!! info "Beta feature"
    *Manage Indexers* was added in 2.0.0-beta.1 and is available on the
    [Beta channel](../getting-started/beta-channel.md).

### TV search accuracy

| Setting | id | Default | Description |
|---------|----|---------|-------------|
| TMDB API key (optional, movies and TV) | `tmdb_api_key` | *(empty)* | A TMDB key (not a TVDB key). For episodes, it resolves the show's TVDB id when TMDBHelper didn't supply one. For movies, it converts a TMDB movie id into an IMDb id when no IMDb id was supplied. The add-on uses IDs supplied by TMDBHelper directly. Without the key, or on lookup failure, the search uses whatever ids and title it already has. Stored hidden. |

!!! info "Beta feature"
    Added in 2.0.0-beta.1 (TV lookup), available on the
    [Beta channel](../getting-started/beta-channel.md).

## Player Installation

Actions only, with no stored settings.

- **Install TMDBHelper Player:** installs the NeNeTeePee-Stream-Kodi player file into
  TMDBHelper.
- **Install Player Other:** installs the player file into another add-on that
  has a `players` folder.

See [Set up TMDBHelper](../getting-started/tmdbhelper.md).

## Quality filters

Every format toggle defaults to `true`. **Other / Unknown** independently
controls missing or unlisted metadata in each category, including HDR. If every named switch in a category is off, recognized values become unrestricted in that category.
See [Quality filtering](../features/quality-filtering.md) for the full options.

| Group | Settings (id) |
|-------|---------------|
| **Resolution** | `filter_4320p`, `filter_2160p`, `filter_1440p`, `filter_1080p` (p/i), `filter_720p` (p/i), `filter_576p` (p/i), `filter_540p`, `filter_480p` (p/i), `filter_360p`, `filter_240p`, `filter_unknown_resolution` |
| **HDR** | `filter_hdr10`, `filter_hdr10plus`, `filter_dolby_vision`, `filter_hlg`, `filter_sdr`, `filter_unknown_hdr` |
| **Audio** | `filter_atmos`, `filter_truehd`, `filter_dtshd_ma`, `filter_dtshd_hr`, `filter_dtsx`, `filter_dts`, `filter_ddplus`, `filter_dd`, `filter_aac`, `filter_flac`, `filter_pcm`, `filter_opus`, `filter_mp3`, `filter_alac`, `filter_vorbis`, `filter_mp2`, `filter_wma`, `filter_ac4`, `filter_unknown_audio` |
| **Video codec** | `filter_hevc`, `filter_avc`, `filter_av1`, `filter_vp9`, `filter_mpeg2`, `filter_mpeg4`, `filter_vc1`, `filter_mpeg1`, `filter_vp8`, `filter_wmv`, `filter_h263`, `filter_vvc`, `filter_mjpeg`, `filter_theora`, `filter_unknown_codec` |

## Languages

A separate tab after Quality filters: 48 language toggles, one per language
(`filter_<language>`, from `filter_arabic` to `filter_vietnamese`), plus **Other / Unknown Language**
(`filter_unknown_language`). All default to `true`. Spanish includes Latino.
If **Chinese** is on, releases tagged Cantonese or Urdu also pass. If every named language is off, recognized languages are unrestricted; Other / Unknown Language still controls unknown metadata.

## Keyword filters

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Preferred groups: Tier 1 / 2 / 3 | `filter_remux_tier_1`, `filter_remux_tier_2`, `filter_remux_tier_3` | TRaSH remux tiers 1/2/3 | Editable comma-separated group names; empty turns that tier off. Under Relevance, Tier 1 groups rank higher than Tier 2, then Tier 3 (after resolution, HDR, and REMUX). Ranking only; never hides a release. |
| Excluded release groups | `filter_exclude_release_group` | *(empty)* | Comma-separated groups to **remove**. Not shown as a field; edit it with *Configure Excluded Groups...*. |
| Min size (MB, 0=no limit) | `filter_min_size` | `0` | Remove releases smaller than this. A size that can't be read counts as 0 MB. |
| Max size (MB, 0=no limit) | `filter_max_size` | `0` | Remove releases larger than this. If max < min, the size filter is turned off. |
| Exclude keywords (comma-separated) | `filter_exclude_keywords` | *(empty)* | Remove releases whose title contains any keyword. |
| Required keywords (comma-separated) | `filter_require_keywords` | *(empty)* | Remove releases whose title doesn't contain every keyword. |

**Action:** select *Configure Excluded Groups...* to open a multi-select of 94
known release groups. An empty list means no exclusions.

## Sorting

| Setting | ID | Default | Values |
|---------|----|---------|--------|
| Sort by | `sort_order` | `0` (Relevance) | `0` Relevance, `1` Size (largest first), `2` Size (smallest first), `3` Age (newest first), `4` Age (oldest first) |
| Max results | `max_results` | `25` | Provider request limits are clamped to 1–10000. The filtered picker uses the raw positive integer as its limit; 0 or less leaves it unbounded. The picker's show-all view is not truncated. |
| Auto-select best match (skip result list) | `auto_select_best` | `false` | Play the top-ranked result that passed your filters and skip the picker. If nothing passed, the picker opens instead. |

## Advanced

These settings tune polling, caching, stream resilience, fallback streams, and the proxy.

!!! info "Beta settings"
    **Clear download queue when starting a new download**, **Seconds into
    playback before submitting fallback backups**, **Max seconds to wait for a
    slow/stalled backend**, and **Read-ahead buffer size** were added in
    2.0.0-beta.1 and are available on the
    [Beta channel](../getting-started/beta-channel.md).

### Polling

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Poll interval (seconds) | `poll_interval` | `1` | Seconds between download-status checks. Clamped to 1–60. |
| Download timeout (seconds) | `download_timeout` | `3600` | Give up if the download isn't ready within this time. Clamped to 60–86400. |
| NZB submit timeout (seconds) | `submit_timeout` | `300` | Max wait for nzbdav to accept the NZB (it fetches and parses the NZB before replying). Clamped to 5–600. |
| Clear download queue when starting a new download | `clear_queue_on_submit` | `0` (Ask) | For nzbdav / InfiniDysk only: `0` Ask, `1` Always clear, `2` Never. NZBGet ignores this option. Excludes this title's own in-flight job, and never clears a completed copy you're about to reuse. |

### Search cache

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Cache duration (minutes, 0=off) | `cache_ttl_minutes` | `30` | How long to cache search results, in minutes. `0` turns off the cache. Clamped to 0–1440. Stores raw pre-filter results, so filter/sort changes take effect immediately. The TMDBHelper player and the plugin routes share the cache. |

### Stream resilience

These drive the background playback monitor.

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Auto-retry on stream failure | `stream_auto_retry` | `true` | Retry a failed stream automatically. |
| Max retry attempts | `stream_max_retries` | `3` | How many times to retry. Clamped to 0–10. |
| Retry delay (seconds) | `stream_retry_delay` | `5` | Wait between retries. Clamped to 1–300. |

### Fallback streams

See [Fallback streams](../features/fallback-streams.md).

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Enable fallback streams | `fallback_streams_enabled` | `true` | Enable nzbdav / InfiniDysk proxy fallback or NZBGet duplicate-backup submission. StreamNZB ignores it. |
| Maximum standby fallback streams | `fallback_streams_max` | `5` | nzbdav / InfiniDysk clamps the standby limit to 0–5. NZBGet and StreamNZB ignore it; NZBGet uses `nzbget_max_backups`. |
| Seconds into playback before submitting fallback backups | `fallback_submit_delay` | `120` | Delay before backups are submitted. `0` submits immediately. |

### Proxy

See [Playback and remux](../features/playback-and-remux.md) and
[How it works → Stream proxy](../how-it-works/stream-proxy.md).

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Convert MP4 subtitles to SRT | `proxy_convert_subs` | `true` | During a Matroska remux, convert MP4 `mov_text` subtitles to SRT so embedded subtitles survive. The remux copies MKV subtitle tracks unchanged. |
| Force ffmpeg remux above (MB, 0=off) | `force_remux_threshold_mb` | `15000` | Size at which the chosen remux mode applies to non-MP4 files. `0` turns size-based remux off, so those files always stream pass-through. No effect while the mode is Direct pass-through. |
| Large non-MP4 stream mode | `force_remux_mode` | `0` (Direct pass-through) | `0` Direct pass-through (default, no ffmpeg), `1` fMP4 HLS (compatibility, experimental), `2` Matroska remux (compatibility). |

### Pass-through validation

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Strict upstream contract mode | `strict_contract_mode` | `1` (Warn only) | How to react when the upstream violates the strict Range/Content-Length contract: `0` Off, `1` Warn only, `2` Enforce. Off also disables the density breaker. |
| Enable density breaker | `density_breaker_enabled` | `false` | Stop a stream when a rolling 16 MB window becomes more than 50% zero-fill (catches dead releases early). Only active when contract mode isn't Off. |
| Enable zero-fill budget | `zero_fill_budget_enabled` | `true` | Cap total per-stream zero-fill; the stream ends with a clean error when the budget is hit. Zero fill skips data that can't be fetched from any source. For MKV/WebM the gap becomes EBML Void elements the player skips, resuming at the next intact cluster; other files, or MKV whose structure can't be confirmed, get plain zeros. |
| Enable retry ladder before skip probe | `retry_ladder_enabled` | `true` | Re-issue the original range request with backoff on transient upstream errors before skip-filling. |
| Max seconds to wait for a slow/stalled backend before giving up (0=off) | `passthrough_stall_wait` | `120` | For an established stream that stalls on a recoverable backend condition, hold the connection open up to this budget. `0` closes immediately. Clamped to 0–600. |
| Read-ahead buffer size in MB (keeps filling while paused; 0=off) | `readahead_buffer_mb` | `256` | Per-session forward read-ahead prefetch. Keeps filling while paused. `0` turns it off. Clamped to 0–4096. |
| Send 200 for no-range pass-through | `send_200_no_range` | `false` | Send `200 OK` instead of `206 Partial Content` when Kodi requests the whole file without a Range header. Leave off unless you've validated it on your build. |

### Hidden settings

These aren't shown in the UI but exist in `settings.xml`:

| Setting | ID | Default | Description |
|---------|----|---------|-------------|
| Content-root override | `webdav_content_root` | *(empty → `content`)* | Power-user override for the nzbdav content-root path segment. Change only for a non-standard reverse-proxy mount. |
| (migration/UI-state flags) | `force_remux_mode_v2_migrated`, `cache_warning_shown`, `cache_dialog_dismissed` | `false` | Internal state, not user-editable. |
