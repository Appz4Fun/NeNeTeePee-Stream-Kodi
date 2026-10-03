# NeNeTeePee-Stream-Kodi

Play Usenet movies and TV episodes in Kodi through TMDBHelper. Choose nzbdav / InfiniDysk, NZBGet, or StreamNZB as your playback backend.

The add-on provides no media or indexers. You need your own backend service and Usenet access.

## Get started

1. [Install the add-on](getting-started/installation.md).
2. [Connect a backend service](backends/index.md).
3. [Configure search providers](features/search-and-indexers.md) if you use nzbdav / InfiniDysk or NZBGet.
4. [Install the TMDBHelper player](getting-started/tmdbhelper.md).
5. [Play your first title](getting-started/first-playback.md).

This site describes the current source. Released builds can have fewer features. Check the [beta channel guide](getting-started/beta-channel.md) and [release notes](https://github.com/Appz4Fun/nzbdavkodi/releases) for your installed version.

## Choose a backend

| Backend | How it plays | Connection guide |
| --- | --- | --- |
| nzbdav / InfiniDysk | Streams video over WebDAV through the add-on proxy. | [Connect nzbdav or InfiniDysk](backends/nzbdav.md) |
| NZBGet | Downloads and processes the release, then plays the completed file. | [Connect NZBGet](backends/nzbget.md) |
| StreamNZB | Searches on the server and gives Kodi a direct HTTP stream. | [Connect StreamNZB](backends/streamnzb.md) |

The add-on uses a shared release picker with parsed filenames, quality metadata, filters, and sorting. Recovery differs by backend. Configure the settings that apply to your selected backend.

## Select a release

![Release picker with parsed media details](images/results-dialog.png)

The existing screenshot shows the release picker before the branding change. The current add-on name is `NeNeTeePee-Stream-Kodi`.

Use [quality filtering and sorting](features/quality-filtering.md) to control the releases you see. The picker can also show entries that your filters rejected.

## Find more information

- [Settings reference](reference/settings.md) lists connection and playback options.
- [Playback and seeking](features/playback-and-remux.md) explains proxy behavior for nzbdav / InfiniDysk.
- [Fallback streams](features/fallback-streams.md) explains source recovery in the proxy.
- [Troubleshooting](operations/troubleshooting.md) helps diagnose connection and playback failures.
- [Architecture](how-it-works/architecture.md) describes the source modules and playback paths.

## Compatibility

The display name is `NeNeTeePee-Stream-Kodi`. The add-on ID remains `plugin.video.nzbdav` so existing installations, settings, and TMDBHelper routes continue to work. Repository and documentation URLs retain their existing paths.
