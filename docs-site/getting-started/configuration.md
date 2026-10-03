# Configure connections

Current released packages (Stable 1.2.3 and Beta 2.0.0-beta.2) use the Kodi menu name **NZB-DAV**. In those builds, choose that name wherever these instructions show **NeNeTeePee-Stream-Kodi**. Renamed source builds use the new name.

Open the add-on settings at **Settings → Add-ons → My add-ons → Video add-ons →
NeNeTeePee-Stream-Kodi → Configure**. Choose and configure your server on **Playback backend**.
For nzbdav / InfiniDysk or NZBGet, configure a search provider on **Indexers**,
starting with NZBHydra2 at the top. Every setting is listed in
the [settings reference](../reference/settings.md).

See [Backend services](../backends/index.md) for illustrated connection guides.

## Choose the playback backend

Select **nzbdav / InfiniDysk**, **NZBGet** or **StreamNZB** on the
**Playback backend** tab. Existing NZBGet selections migrate automatically.
For StreamNZB, enter its server/base URL and stream token, then use the existing
TMDBHelper player. Follow [StreamNZB backend](../features/streamnzb-backend.md);
the nzbdav, WebDAV and local search-provider steps below are not required.

## Connect to nzbdav

On **Playback backend**, under **nzbdav / InfiniDysk**, enter the address and API key of your nzbdav server. If you
run [InfiniDysk](https://github.com/infinidysk/infinidysk), the maintained
nzbdav fork, enter its address and API key here the same way.

| Setting | What to enter |
|---------|---------------|
| **nzbdav URL** | The base URL of your nzbdav instance, for example `http://192.168.1.100:3000`. Default: `http://localhost:3000`. |
| **API Key** | From the nzbdav web UI: **Settings → Usenet tab → API Key**. |

Select **Test nzbdav Connection**. It reads nzbdav's queue with your key, so
it confirms both the URL and the API key.

## Connect to WebDAV

The **WebDAV** group is also on **Playback backend**, visible with
**nzbdav / InfiniDysk** selected. nzbdav serves finished files over WebDAV;
NeNeTeePee-Stream-Kodi streams from there.

| Setting | What to enter |
|---------|---------------|
| **WebDAV URL (leave empty to use nzbdav URL)** | Leave this **empty** if WebDAV is served from the same address as the nzbdav URL. NeNeTeePee-Stream-Kodi then reuses the nzbdav URL. Enter a value only if your setup exposes WebDAV on a separate address. |
| **Username** | From the nzbdav web UI: **Settings → WebDAV tab → Username**. |
| **Password** | From the nzbdav web UI: **Settings → WebDAV tab → Password**. |

Select **Test WebDAV Connection** to confirm access. It reports separately
whether the server was unreachable, rejected your credentials, or returned a
server error.

!!! warning "Leave WebDAV URL empty unless you need it"
    The WebDAV URL field defaults to `http://localhost:8080`. If your WebDAV
    lives at the same address as nzbdav, clear this field so NeNeTeePee-Stream-Kodi reuses the
    nzbdav URL. Leaving an unreachable `localhost:8080` in place is a common
    cause of WebDAV connection errors.

## Enable a search provider

For nzbdav and NZBGet, open **Indexers** and enable at least one provider.
NZBHydra2 is the first option. Turn on whichever you use and fill in its
details. You can enable more than one. results are merged and de-duplicated.

!!! tip "Recommended: NZBHydra2"
    [NZBHydra2](https://github.com/theotherp/nzbhydra2) is the recommended
    provider. Configure your indexers once in Hydra, which has a better search
    interface and is highly configurable, instead of entering each indexer's
    Newznab API on NeNeTeePee-Stream-Kodi's **Indexers** tab. Each result still shows which
    indexer Hydra found it on. See
    [Search and indexers](../features/search-and-indexers.md#provider-types).

=== "NZBHydra2"

    | Setting | What to enter |
    |---------|---------------|
    | **Enable NZBHydra2** | Turn on. |
    | **NZBHydra2 URL** | for example, `http://192.168.1.100:5076`. |
    | **API Key** | NZBHydra2 web UI → **Config → Main → Security → API key**. |

    Select **Test NZBHydra Connection** to verify.

=== "Prowlarr"

    | Setting | What to enter |
    |---------|---------------|
    | **Enable Prowlarr** | Turn on. |
    | **Prowlarr URL** | for example, `http://192.168.1.100:9696`. |
    | **Prowlarr API Key** | Prowlarr web UI → **Settings → General → Security → API Key**. |
    | **Prowlarr Indexer IDs (comma-separated)** | Comma-separated indexer IDs to query. **Required**. NeNeTeePee-Stream-Kodi skips the Prowlarr search if this is left empty. |

    Select **Test Prowlarr Connection**. It checks the URL and API key by
    listing Prowlarr's indexers. It doesn't check the indexer IDs you entered.
    Prowlarr only contributes **Usenet** results. Torrent results are
    dropped.

=== "Direct Newznab indexers"

    Use this if you don't run Hydra or Prowlarr. Go to the **Indexers** tab:

    1. Turn on **Enable direct Newznab indexers**.
    2. Under **Popular Indexers**, turn on the ones you use (NZB.life / NZB.su,
       NZBGeek, NZBFinder, DrunkenSlug, NZBPlanet, DOGnzb) and enter each
       **API Key**. For any other Newznab indexer, fill in one of the three
       **Custom Newznab Indexers** slots (name, API URL, API key), or use
       **Manage Indexers** (beta builds) to pick from a longer preset list.
    3. Select **Test Direct Indexers**. It queries each enabled indexer's
       capabilities endpoint. It shows **Direct indexers OK: n/n** when all of
       them respond, or the first error when one fails.

    See [Search and indexers](../features/search-and-indexers.md#direct-newznab-indexers)
    for the full indexer manager.

## Improve search accuracy (optional)

!!! info "Beta feature"
    Added in 2.0.0-beta.1 and available on the
    [Beta channel](beta-channel.md).

The last group on the **Indexers** tab, **TV search accuracy**, has one
setting: **TMDB API key (optional, movies and TV)**. Enter a key from TMDB
here, not from TVDB. With a key, NeNeTeePee-Stream-Kodi converts the ids TMDBHelper sends:

- **Movies:** When TMDBHelper sends only a TMDB id, NeNeTeePee-Stream-Kodi looks up the IMDb
  id and uses it to query indexers.
- **TV:** When TMDBHelper doesn't send a TVDB id, NeNeTeePee-Stream-Kodi looks up the show's
  TVDB id so indexers can search by id. Id searches return more accurate
  episode results than a title-only search.

Ids that TMDBHelper already supplies are used directly. If you don't enter a
key or a lookup fails, NeNeTeePee-Stream-Kodi falls back to the supplied ids or the title.

!!! tip "Entering long API keys with a remote"
    Typing API keys on a TV remote is painful. Use a Kodi remote app with
    keyboard and clipboard support (for example, Kore or Sybu): copy the key on
    your computer, then paste it into the Kodi field from the app.

## Using NZBGet instead of nzbdav

!!! info "Beta feature"
    Added in 2.0.0-beta.1. available on the [Beta channel](beta-channel.md).

If you use NZBGet, its fields appear on **Playback backend**:

1. Select **NZBGet** in the **Playback backend** dropdown.
2. Enter the **NZBGet URL**, **NZBGet Username**, **NZBGet Password**, and
   **NZBGet Category**.
3. Set **Completed Folder (SMB or Local Path)** to NZBGet's completed-downloads
   folder as Kodi sees it. An
   [NFS hard mount](../features/nzbget-backend.md#recommended-mount-the-completed-folder-over-nfs)
   is recommended over `smb://`.
4. Run **Test NZBGet Connection** and **Test Completed Folder**.

See [NZBGet backend](../features/nzbget-backend.md) for details.

## Next step

[Set up TMDBHelper](tmdbhelper.md) so you can launch playback from any movie or
episode.
