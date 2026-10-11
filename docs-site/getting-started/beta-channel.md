# Beta channel and beta features

Beta 2.0.0-beta.3 and later use the Kodi menu name **NeNeTeePee-Stream-Kodi**. Stable 1.2.3 still uses **NZB-DAV**. In Stable, choose that name wherever these instructions show **NeNeTeePee-Stream-Kodi**.

NeNeTeePee-Stream-Kodi ships through two channels of the
[Appz4Fun Kodi repository](installation.md#choose-a-channel). **Stable** gets
only full releases. **Beta** gets every release, including pre-releases. This
page covers what the Beta channel gives you, how to join it or leave it, and how
to report problems.

## Where each channel is today

| Channel | Version | Released |
|---------|---------|----------|
| Stable | 1.2.3 | 2026-05-08 |
| Beta | 2.0.0-beta.3 | 2026-10-11 |

Beta releases so far:

| Version | Released |
|---------|----------|
| 2.0.0-beta.3 | 2026-10-11 |
| 2.0.0-beta.2 | 2026-07-18 |
| 2.0.0-beta.1 | 2026-07-09 |

The full release list is on the
[GitHub releases page](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/releases). Any
release marked **Pre-release** there goes only to the Beta channel. The release
workflow sets that flag automatically for any version tag with a hyphen, such
as `v2.0.0-beta.3`.

!!! warning "Beta means beta"
    The 2.0.0 line changes a lot at once: new download and streaming backends,
    a rewritten settings screen, and reworked fallback and recovery. It gets daily use, but
    expect rough edges. If something breaks,
    [report it](#reporting-beta-problems).

## What's in the beta

These features are in 2.0.0-beta.1, 2.0.0-beta.2, or 2.0.0-beta.3 and are
**not** in Stable 1.2.3. The full notes are in the
[changelog](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/main/CHANGELOG.md).

### New features

| Feature | What it does | Details |
|---------|--------------|---------|
| **NZBGet backend** | Use NZBGet instead of nzbdav. NeNeTeePee-Stream-Kodi submits the NZB, shows download and post-processing progress, then plays the finished file from your completed-downloads folder. | [NZBGet backend](../features/nzbget-backend.md) |
| **Smart Duplicates failover** (NZBGet) | Other same-name results are queued as backups. If your pick can't be repaired, NZBGet switches to a backup and playback follows it. In beta.3, the backups go to NZBGet in one `appendfleet` request. The add-on falls back to one-by-one submissions on servers that don't support it. | [Smart Duplicates](../features/nzbget-backend.md#smart-duplicates-failover) |
| **StreamNZB backend** (beta.3) | Play through a StreamNZB server with the full NZB picker and the add-on's local filters. | [StreamNZB backend](../features/streamnzb-backend.md) |
| **One Playback backend section** (beta.3) | A single **Playback backend** section replaces the old **Connection** and **NZBGet** tabs. Your old "use NZBGet" choice is migrated once. NZBHydra2, Prowlarr, and the TMDB key moved to **Indexers**, and languages have their own category. | [Settings guide](../settings/index.md) |
| **Complete media filters and ranking** (beta.3) | Filters cover resolution, HDR, audio, codec, and language. Results rank by resolution, then HDR, then REMUX, with three editable release-group tiers. The old preferred-groups list is gone. | [Settings reference](../reference/settings.md) |
| **Show-all picker toggle** (beta.3) | Show filtered-out results in the picker, with the reason each row was filtered. | [Settings reference](../reference/settings.md) |
| **TMDbHelper scrobbling restored** (beta.3) | TMDbHelper scrobbling works again on every backend. The add-on migrates the TMDbHelper player file to schema 10 automatically and keeps a backup. | [TMDBHelper setup](tmdbhelper.md) |
| **Shared search cache** (beta.3) | The cache duration is now in minutes (`cache_ttl_minutes`, default 30). TMDBHelper plays use the same cache as the picker. | [Settings reference](../reference/settings.md) |
| **Exact season-pack episode reuse** (beta.2) | A finished season pack plays the episode you asked for, not the largest file. Later episodes from the same pack play from it without downloading again. | [Reuse a completed season pack](first-playback.md#reuse-a-completed-season-pack) |
| **Indexer manager** | Add, edit, and remove direct Newznab indexers from a preset list of known indexers, with searches that respect each indexer's capabilities. | [Search and indexers](../features/search-and-indexers.md) |
| **TVDB-aware TV search** | With an optional TMDB API key, NeNeTeePee-Stream-Kodi looks up the show's TVDB id and searches indexers by id instead of by title. | [Search and indexers](../features/search-and-indexers.md) |
| **Read-ahead buffer** | While a stream plays, and while it's paused, NeNeTeePee-Stream-Kodi reads ahead of the playhead so a pause builds real buffer. Default 256 MB. | [Settings reference](../reference/settings.md) |
| **Stall wait and starvation notices** | A slow backend gets a patience window (default 120 s) instead of a dropped stream, and a notification tells you what's happening instead of a silent black screen. | [Playback](../features/playback-and-remux.md) |
| **Queue-clear prompt** | When you start a new download, NeNeTeePee-Stream-Kodi can clear nzbdav's queue: **Ask** (default), **Always clear**, or **Never**. It never cancels the job for the title you're starting. | [Settings reference](../reference/settings.md) |
| **Help text for every setting** | The settings screen was rebuilt on Kodi's newer settings format, so every setting and category shows a help line. | [Settings reference](../reference/settings.md) |

### Improvements

- **Fallback streams** match alternate releases in tiers and search more
  widely for same-content peers. For files of 1 GiB or more, the byte-fingerprint
  check samples 100 points instead of 20. See [Fallback streams](../features/fallback-streams.md).
- **SMB playback is checked before it starts** (beta.2). NeNeTeePee-Stream-Kodi retries a file
  that lists over SMB but isn't readable yet until it can read it. If it never becomes
  readable, you get a "restart Kodi" hint instead of a failed player.
- **The results dialog** scrolls long labels on the focused row, has
  zebra-striped rows, and keeps remote focus inside the list.
- **MKV and WebM gaps are concealed with EBML awareness** (beta.3). When a
  stream has a dead span, the proxy conceals it without breaking the container.
- **The NZBGet completed folder can be a local or mounted path** (beta.3), not
  only an SMB share.
- **`.m2ts` files play** (beta.3).
- **`/resolve-v2` accepts a source manifest** (beta.3) from external callers:
  a primary source plus alternates, which become NZBGet duplicate backups.
- **Large MKVs start faster.** The proxy pre-reads the end of the file, where
  Matroska keeps its seek index, before playback starts.
- **Prowlarr results** come from Prowlarr's native search API.
- **Security:** every XML parser that reads network data now goes through one
  hardened parser.

Changes on `main` that aren't in a release yet are listed under
**Unreleased** at the top of the
[changelog](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/blob/main/CHANGELOG.md#unreleased--main).

## Joining the beta

**New install:** follow [Install the add-on](installation.md) and choose the
**Beta** repository zip (`repository.appz4fun.beta-<version>.zip`).

**Already on Stable:** Kodi only updates a third-party add-on from the
repository it was installed from, so adding the Beta repository isn't enough on
its own.

1. Install the Beta repository zip (**Settings → Add-ons → Install from zip
   file**).
2. Open **Settings → Add-ons → My add-ons → Video add-ons → NeNeTeePee-Stream-Kodi →
   Versions**, and pick the newest version listed under **Appz4Fun Repository
   (Beta)**.
3. Optionally uninstall the Stable repository add-on (**Appz4Fun
   Repository**). It no longer affects NeNeTeePee-Stream-Kodi.

Your settings stay in place. Beta 2.0.0-beta.3 replaces the **Connection** and
**NZBGet** tabs with one **Playback backend** section, and every new setting
starts at its default.

## Switching channels

To go back to Stable, use the same **Versions** list: open NeNeTeePee-Stream-Kodi's add-on
info, choose **Versions**, and pick the version listed under **Appz4Fun
Repository**. You have to do this by hand: Stable (1.2.3) is a lower version
than the beta (2.0.0-beta.x), and Kodi never downgrades an add-on on its own.
See [Return from the beta channel to the stable channel](../operations/troubleshooting.md#return-from-the-beta-channel-to-the-stable-channel).

After you switch either way, check that **Auto-update** is still on in the
add-on info page.

!!! warning "When 2.0.0 final is released"
    Kodi reads a version like `2.0.0-beta.3` as the base version `2.0.0` plus
    an extra suffix, and it ranks a version with a suffix **higher than** the same
    version without one. So Kodi treats `2.0.0-beta.3` as newer than a final
    `2.0.0`, and won't offer that update on its own. If a final release has the
    same base number as the beta you're running, install it from
    **Versions**. Any later version, such as `2.0.1`, updates normally.

## Reporting beta problems

1. Turn on **Settings → System → Logging → Enable debug logging**, reproduce
   the problem, and save `kodi.log`. On CoreELEC and LibreELEC it's at
   `/storage/.kodi/temp/kodi.log`.
2. Remove API keys, passwords, and server addresses from anything you share.
   NeNeTeePee-Stream-Kodi redacts credentials in its own log lines, but other add-ons may not.
3. Open an issue on
   [GitHub](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/issues). Include the
   NeNeTeePee-Stream-Kodi version (shown on the add-on info page), your backend (nzbdav or
   NZBGet), your platform, and the relevant log lines.

See [Troubleshooting](../operations/troubleshooting.md) for common problems
and fixes.
