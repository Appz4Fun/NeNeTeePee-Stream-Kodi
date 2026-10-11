# Kodi settings guide

Use this guide alongside **Settings → Add-ons → My add-ons → Video add-ons → NeNeTeePee-Stream-Kodi → Configure**. You can also open the add-on, open its context menu, and select **Settings**.

Beta 2.0.0-beta.3 uses the name **NeNeTeePee-Stream-Kodi**. Stable 1.2.3 still uses **NZB-DAV**. Both use the same add-on ID, `plugin.video.nzbdav`. See [repository installation](../getting-started/repositories.md) for the Beta channel and how to open Configure.

| Kodi tab | What you configure |
| --- | --- |
| [Playback backend](backend.md) | nzbdav / InfiniDysk, NZBGet, or StreamNZB and its connection fields |
| [Indexers](indexers.md) | NZBHydra2, Prowlarr, direct providers, and the optional TMDB API key |
| [Player Installation](player-installation.md) | TMDBHelper and other compatible player definitions |
| [Quality Filters](quality-filters.md) | Allowed resolutions, HDR formats, audio formats, and video codecs |
| [Languages](language-filters.md) | Allowed language tags |
| [Keyword Filters](keyword-filters.md) | Preferred tiers, excluded groups, size limits, and title keywords |
| [Sorting](sorting.md) | Relevance, size or age ranking, result limits, and auto-select |
| [Advanced](advanced.md) | Polling, cache, retry, fallback, remux, and proxy validation |

The tab guides explain every option. This includes buttons and hidden state fields. Each source footnote links to the captured commit. [Copy and paste from your phone](../getting-started/phone-remote.md) instead of entering long keys and passwords with a television remote.

## Capture details

These are real 1280 × 720 screenshots from the `kodi-ebml` OrbStack VM using Kodi's Estuary skin. The add-on files came from `origin/main` at commit [`db07f61d4090a0c89ec8461ee9b3ca34f9607aa6`](https://github.com/Appz4Fun/NeNeTeePee-Stream-Kodi/commit/db07f61d4090a0c89ec8461ee9b3ca34f9607aa6), fetched on October 3, 2026. The remote has no `master` branch.

Debug overlays were turned off before capture. Connection examples use `192.0.2.10` and dummy credentials. They do not show working servers or account secrets. Scrolled views overlap to keep the surrounding controls visible. The [capture manifest](../images/kodi/capture-manifest.json) records the context and checksum for each screenshot. The **Standard** setting level shows this add-on's visible options; hidden migration fields do not appear in screenshots.

The unified backend tab, StreamNZB support, and renamed display name ship in Beta 2.0.0-beta.3. Stable 1.2.3 uses an earlier settings layout. Later changes on `main` are not in beta.3 until a release includes them.

Repository screenshots show the public Beta repository available at capture time. The [TMDBHelper project](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) is a required dependency for the browsing and player workflow.
