# Prerequisites

Before you install NeNeTeePee-Stream-Kodi, make sure the surrounding pieces are in place. NeNeTeePee-Stream-Kodi
is the glue between Kodi and your existing Usenet stack. It doesn't replace any
of these components.

## StreamNZB alternative

With **Playback backend → StreamNZB**, a reachable StreamNZB server and its
stream token replace the nzbdav/WebDAV and local search-provider requirements
that follow. Configure Usenet access and indexers on StreamNZB. Kodi and TMDBHelper
remain required. See [StreamNZB backend](../features/streamnzb-backend.md).

## Required for nzbdav / NZBGet

| Component | What you need | Notes |
|-----------|---------------|-------|
| **Kodi 21 (Omega)** or later | A working Kodi install | Runs on CoreELEC, LibreELEC, OSMC, Windows, macOS, and Linux. |
| **nzbdav** or **InfiniDysk** | A running, reachable [nzbdav](https://github.com/nzbdav-dev/nzbdav) instance, or its maintained fork [InfiniDysk](https://github.com/infinidysk/infinidysk) (recommended) | Accepts the NZB and serves the file over WebDAV for streaming. No separate download client is needed. (Beta builds can use NZBGet instead; see the NZBGet section later on this page.) |
| **A Usenet provider** | Configured in your backend (nzbdav, InfiniDysk, or NZBGet) | The backend connects to your news server. NeNeTeePee-Stream-Kodi never talks to Usenet directly. |
| **At least one search provider** | **NZBHydra2**, **Prowlarr**, *or* **direct Newznab indexers** | You can enable more than one; results are merged. [NZBHydra2](https://github.com/theotherp/nzbhydra2) is recommended. See [Choose your search provider](#choose-your-search-provider). |
| **TMDBHelper** | `plugin.video.themoviedb.helper` installed in Kodi | This is how you browse titles and trigger playback. |

## Choose your search provider

NeNeTeePee-Stream-Kodi supports three provider types. Enable any combination:

- **NZBHydra2** (recommended): a Newznab aggregator that fronts many
  indexers. You set up your indexers once in Hydra instead of entering each
  one in NeNeTeePee-Stream-Kodi, and every result still shows which indexer it came from.
- **Prowlarr:** an alternative aggregator. NeNeTeePee-Stream-Kodi queries Prowlarr's native
  search API and keeps only Usenet (not torrent) results.
- **Direct Newznab indexers:** connect straight to individual indexers
  (NZB.life / NZB.su, NZBGeek, NZBFinder, DrunkenSlug, NZBPlanet, DOGnzb,
  and more, plus custom entries). Use this when you don't run Hydra or Prowlarr.

You need only one of these to start. For details on how each behaves, see
[Search and indexers](../features/search-and-indexers.md).

## Recommended

| Component | Why |
|-----------|-----|
| **ffmpeg** on the Kodi device | Enables the optional remux and HLS compatibility tiers for large or awkward files. NeNeTeePee-Stream-Kodi works without it; the proxy falls back to direct pass-through. |
| **TMDB API key** | Lets NeNeTeePee-Stream-Kodi turn TMDBHelper's TMDB ids into the IMDb (movie) or TVDB (TV) ids that indexers search by, for more accurate results. It's a TMDB key; NeNeTeePee-Stream-Kodi doesn't need a TVDB key. Without it, searches use whatever ids TMDBHelper supplies, or the title. See [Configure connections](configuration.md#improve-search-accuracy-optional). |

## Optional: NZBGet instead of nzbdav

!!! info "Beta feature"
    Added in 2.0.0-beta.1, available on the [Beta channel](beta-channel.md).

NeNeTeePee-Stream-Kodi can use **NZBGet** as the download and playback backend instead of
nzbdav. In this mode NeNeTeePee-Stream-Kodi submits to NZBGet, waits for it to finish
downloading and post-processing, and plays the finished file from NZBGet's
completed-downloads folder. Kodi must be able to read that folder, either as an
SMB share (`smb://…`) or as a local or mounted path. An
[NFS hard mount](../features/nzbget-backend.md#recommended-mount-the-completed-folder-over-nfs)
works best. You still need a search provider. nzbdav's WebDAV streaming and
live stream fallback don't apply in NZBGet mode; NZBGet's own duplicate
handling covers failover instead. See
[NZBGet backend](../features/nzbget-backend.md).

## About dependencies

The add-on's runtime is **pure Python and 3.8-compatible**, with every library
vendored. There's nothing to `pip install`, and no compiled extensions, so it
runs cleanly on ARM64 CoreELEC boxes as well as x86-64 desktops.

## Next step

Once these are ready, [install the add-on](installation.md).
